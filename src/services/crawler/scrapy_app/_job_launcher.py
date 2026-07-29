"""Scrapy job launcher（子进程入口，与 runner 搭配使用）.

本模块 **不能** 由主进程 import（它一进来就 import scrapy/twisted 并 install reactor）。
只能由 runner.py 通过 `python _job_launcher.py --args-file <json>` 启动为独立子进程。

职责：
1. 解析命令行 JSON 参数文件；
2. 为该子进程 install 合适的 Twisted reactor（Windows 下避免
   AsyncioSelectorReactor 和主进程 Uvicorn 冲突）；
3. 用 CrawlerProcess 运行指定 Spider，完成后退出（进程退出码 0 成功；1 异常）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any


# 解析命令行：只接受 --args-file 参数
# 返回 argparse.Namespace：包含 args_file 属性
def _parse_args() -> argparse.Namespace:
    """解析命令行参数.

    只支持 ``--args-file <path>``。失败时调用 sys.exit(1)。
    """
    # 创建参数解析器
    parser = argparse.ArgumentParser(description="AIGameWorld Studio scrapy job launcher (subprocess)")
    # 添加必填的 args-file 参数
    parser.add_argument("--args-file", required=True, help="runner.py 写出的参数 JSON 文件绝对路径")
    # 解析参数
    return parser.parse_args()


# 读取 JSON 参数文件
# 参数 path：JSON 文件路径（字符串或 Path）
# 返回 dict：解析后的完整 payload
def _load_payload(path: str | Path) -> dict[str, Any]:
    # 转 Path 对象
    p = Path(path)
    # 文件不存在直接报错退出
    if not p.exists():
        # 消息打印到 stderr（stdout 可能在 runner 里被重定向到文件）
        print(f"[launcher] args file not found: {p}", file=sys.stderr)
        # 退出码 1
        sys.exit(1)
    # 以 UTF-8 读取 JSON，处理异常
    try:
        # 读取内容
        text = p.read_text(encoding="utf-8")
        # 解析 JSON
        return json.loads(text)
    except Exception as e:
        # 读 JSON 失败（格式错、权限不足等）
        # 打印错误信息
        print(f"[launcher] load payload failed: {type(e).__name__}: {e}", file=sys.stderr)
        # 退出码 1
        sys.exit(1)


# 防御性地把 job 状态落到 DB（不影响 Scrapy 主流程，出错只打印）
# 参数 db_path：SQLite DB 路径
# 参数 job_id：任务 ID
# 参数 status：目标状态
# 参数 finished：是否标记 finished_at 时间戳
# 参数 error_msg：可选的错误消息
# 返回 None
def _mark_job_safe(db_path: str | Path, job_id: str, status: str, *, finished: bool, error_msg: str | None = None) -> None:
    # 延迟导入（失败了也不会让 launcher import 阶段崩）
    try:
        # 导入 store 层函数
        from ..store import update_job_status  # type: ignore
        # 导入 SQLiteStore
        from ....utils.sqlite_store import SQLiteStore as _S  # type: ignore
    except Exception as e:
        # 导入失败（import 路径不对之类）
        # 打印但不抛
        print(f"[launcher] import store failed (mark {status} skipped): {e}", file=sys.stderr)
        return
    # db_path 为空就啥也不做
    if not db_path:
        return
    # 打开 DB，处理异常
    try:
        # 打开 SQLite 连接
        store = _S(Path(db_path))
    except Exception as e:
        # 打开失败
        print(f"[launcher] open store failed (mark {status} skipped): {e}", file=sys.stderr)
        return
    try:
        # 截断错误消息到 500 字符
        err500 = (error_msg or "")[:500] or None
        # 调 store 更新状态
        update_job_status(store, job_id, status, finished=finished, error_msg=err500)
    except Exception as e:
        # 写 DB 失败
        print(f"[launcher] update_job_status({status}) failed: {e}", file=sys.stderr)
    finally:
        # 无论成功失败都关闭连接
        try:
            # 安全关闭
            store.close()
        except Exception:
            # 关闭失败也不管
            pass


# 在 import Scrapy 之前 install Twisted reactor（避免 default reactor 被自动选）
# 返回 None
def _install_reactor_early() -> None:
    # 已 install 过就跳过（防御多次调用）
    if "twisted.internet.reactor" in sys.modules:
        return

    # Windows 下：select() 基础的 SelectReactor 在 Windows + Python 3.14 上和 Uvicorn
    # 的 ProactorEventLoop 共进程时存在问题，但我们这里是独立子进程，选一个"最传统、
    # 不和 asyncio 有任何瓜葛"的 reactor 更稳定。
    # 用 importlib 绕过 early import 检查（Twisted 推荐的官方写法）
    import importlib
    # 根据平台挑 reactor 模块名
    if os.name == "nt":
        # Windows 子进程选 win32eventreactor（不依赖 asyncio、消息循环更稳）
        mod = importlib.import_module("twisted.internet.win32eventreactor")
    else:
        # POSIX 下默认选 POLL reactor（传统选法）
        # 某些极简系统没 poll，降级 selectreactor 也能跑
        try:
            # 优先 pollreactor（高并发更稳）
            mod = importlib.import_module("twisted.internet.pollreactor")
        except Exception:
            # poll 不可用，退到 selectreactor
            mod = importlib.import_module("twisted.internet.selectreactor")
    # 安装 reactor（Twisted 官方 API：install()）
    mod.install()
    # stderr 打印日志（stdout 可能被重定向）
    print(f"[launcher] installed reactor: {mod.__name__}", file=sys.stderr)


# 主流程：读取参数 → 构造 settings → 跑 CrawlerProcess
# 参数 payload：runner 写出的完整参数字典
# 返回 int：进程退出码（0 成功，1 失败）
def _main(payload: dict[str, Any]) -> int:
    # --- 启动前把 job 标 running ---
    # 解析必选字段
    job_id = payload["job_id"]
    # DB 路径
    db_path = payload["db_path"]
    # 开始时间戳先记一下（print 用）
    print(f"[launcher] job={job_id} start, db={db_path}", file=sys.stderr)
    # 标记 running（防御式）
    _mark_job_safe(db_path, job_id, "running", finished=False)

    # --- 真正 import Scrapy ---
    # 延迟到这里 import；保证 reactor 已 install、且 runner.py（主进程）不需要 scrapy
    try:
        # Scrapy 设置对象
        from scrapy.settings import Settings
        # CrawlerProcess（跨 spider 单进程跑）
        from scrapy.crawler import CrawlerProcess
    except Exception as e:
        # Scrapy 未安装（没装 crawler 可选依赖）
        # 失败消息
        err = f"import scrapy failed: {type(e).__name__}: {e}"
        # 打印
        print(f"[launcher] {err}", file=sys.stderr)
        # 标记 DB 失败
        _mark_job_safe(db_path, job_id, "failed", finished=True, error_msg=err)
        # 返回失败退出码
        return 1

    # --- 组装 Scrapy Settings ---
    # 构造 Settings 对象（空的，先）
    scrapy_settings = Settings()
    # payload 里的 settings dict
    raw_settings = payload.get("scrapy_settings") or {}
    # 遍历 key/value 逐个 set
    for k, v in raw_settings.items():
        # set 到 Settings 对象
        scrapy_settings.set(k, v, priority="cmdline")

    # --- 构造并运行 CrawlerProcess ---
    # 创建 CrawlerProcess 实例
    process = CrawlerProcess(settings=scrapy_settings)
    # 要跑的 spider 名
    spider_name = payload.get("spider_name") or ""
    # spider 构造 kwargs
    spider_kwargs = payload.get("spider_kwargs") or {}
    # 空 spider_name 是 bug（runner 不会传，但防御一下）
    if not spider_name:
        # 失败消息
        err = "empty spider_name in payload"
        # 打印
        print(f"[launcher] {err}", file=sys.stderr)
        # 标记 DB 失败
        _mark_job_safe(db_path, job_id, "failed", finished=True, error_msg=err)
        # 返回失败退出码
        return 1

    # 启动爬虫（blocking 直到爬完或崩溃）
    # 外层 try 捕获 reactor 跑起来之后任何未被 Scrapy 自身处理的异常
    # Scrapy 设计上 crawl 过程里 spider 级别的错误会自己记在 stats 里，不会抛到 process.start 外层
    # 但我们还是加一层兜底，极端情况下保证把 DB 标为 failed
    fatal_err: str | None = None
    try:
        # 注册 spider 到 CrawlerProcess
        # CrawlerProcess.crawl 返回 Deferred；我们用 start() 阻塞直到 reactor 退出
        process.crawl(spider_name, **spider_kwargs)
        # 阻塞运行 reactor 直到爬取结束
        process.start(install_signal_handlers=True)
    except Exception as e:
        # process.start 抛异常（罕见，但可能）
        # 打印 traceback 方便排错
        traceback.print_exc(file=sys.stderr)
        # 组装 fatal 错误
        fatal_err = f"crawl runtime error: {type(e).__name__}: {e}"

    # --- 结束：按 stats 决定 job 最终状态 ---
    # scrapy job stats（crawler.stats 的最终快照）
    try:
        # 先尝试从 CrawlerProcess 里拿 stats（公共 API）
        crawler_stats = process.crawlers[-1].stats.get_stats() if list(getattr(process, "crawlers", [])) else {}
    except Exception:
        # 拿不到就当空（不影响主流程）
        crawler_stats = {}

    # 成功状态（默认 done）
    final_status = "done"
    # 结束错误消息
    final_error = None

    # 有致命异常
    if fatal_err:
        # 最终状态 failed
        final_status = "failed"
        # 错误消息
        final_error = fatal_err
    else:
        # 没致命异常，看 stats：Scrapy 自己统计的错误数
        # spider 级 exception 数
        spider_errs = crawler_stats.get("spider_exceptions/Exception", 0) or 0
        # pipeline 级 exception 数（ItemError）
        item_errs = crawler_stats.get("item_pipeline_errors/ItemError", 0) or 0
        # downloader/error（HTTP 5xx、超时等非致命错误一般不影响整体 done，但我们可以决定）
        # 这里我们保守：只要有任何蜘蛛/管道异常就标 failed，方便上层重试
        # 蜘蛛或管道出现异常
        if spider_errs + item_errs > 0:
            # 标为 failed
            final_status = "failed"
            # 错误信息摘要
            final_error = f"stats spider_exceptions={spider_errs}, item_pipeline_errors={item_errs}"

    # 把最终状态写入 DB（第二次调，若状态相同不影响）
    _mark_job_safe(db_path, job_id, final_status, finished=True, error_msg=final_error)
    # 打印结束日志
    print(f"[launcher] job={job_id} final_status={final_status}", file=sys.stderr)
    # 返回退出码（0 成功，1 失败）
    return 0 if final_status == "done" else 1


# 模块入口（当脚本跑）
# 返回 None
if __name__ == "__main__":
    # 先解析参数
    args = _parse_args()
    # 读取 JSON payload
    payload = _load_payload(args.args_file)
    # 确保 DB 的父目录等存在（极端情况下避免首次 SQLite 连接失败）
    db_path = payload.get("db_path")
    # 有 db_path
    if db_path:
        # 转 Path
        dbp = Path(db_path)
        # 父目录不存在就创建
        dbp.parent.mkdir(parents=True, exist_ok=True)

    # --- 在任何 Scrapy/Twisted import 前 install reactor ---
    # （必须在 _main 里的 import scrapy.settings 之前跑）
    try:
        # 安装 reactor
        _install_reactor_early()
    except Exception as e:
        # 安装失败（极端情况：没装 Twisted → 后面 import scrapy 也会失败，那里会标 DB）
        # 这里只打印
        print(f"[launcher] install reactor warning (continuing): {e}", file=sys.stderr)

    # 退出码变量
    exit_code = 1
    try:
        # 跑主流程，拿退出码
        exit_code = _main(payload)
    except Exception as e:
        # 最外层兜底：_main 没捕获的任何异常
        # 打印 traceback
        traceback.print_exc(file=sys.stderr)
        # 拼错误消息
        msg = f"_main unhandled: {type(e).__name__}: {e}"
        try:
            # 尽力标记 DB 为 failed（再一次兜底）
            _mark_job_safe(db_path, payload.get("job_id", ""), "failed", finished=True, error_msg=msg)
        except Exception:
            # 兜底的兜底：连 mark 都失败，就只打印
            pass
        # 确保失败退出
        exit_code = 1
    # 用退出码结束子进程
    sys.exit(exit_code)
