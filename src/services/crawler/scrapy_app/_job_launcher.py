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
import os
import sys
import traceback
from pathlib import Path
from typing import Any

# 作为顶层脚本启动（python path/to/_job_launcher.py）时，__package__ 是 None，
# 相对 import 会失败："attempted relative import with no known parent package"。
# 这里提前把项目根目录（src 的父目录）加到 sys.path，改用绝对 import。
_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # _job_launcher → scrapy_app → crawler → services → src → 项目根
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


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
def _mark_job_safe(
    db_path: str | Path, job_id: str, status: str, *, finished: bool, error_msg: str | None = None
) -> None:
    # 延迟导入（失败了也不会让 launcher import 阶段崩）
    try:
        # 用绝对 import：项目根已加到 sys.path
        from src.services.crawler.store import update_job_status
        from src.utils.sqlite_store import SQLiteStore as _S
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
        # CrawlerProcess（跨 spider 单进程跑）
        from scrapy.crawler import CrawlerProcess
        from scrapy.settings import Settings
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

    # --- 【关键】显式 import Spider 类，手动映射，避免 Scrapy SpiderLoader 的字符串查找问题 ---
    # 这样做的好处：
    #   1. 避开 Scrapy SpiderLoader 遍历 SPIDER_MODULES 子包 import 时可能吞掉异常的问题；
    #   2. 显式 import 失败会直接 stderr 打印 traceback，方便排查；
    #   3. 直接传 Spider 类给 CrawlerProcess.crawl()，行为更可预测。
    print(f"[launcher] resolving spider class for name={spider_name!r}", file=sys.stderr, flush=True)
    try:
        if spider_name == "topic_search":
            from src.services.crawler.scrapy_app.spiders.topic_search import TopicSearchSpider as _SpiderCls
        elif spider_name == "generic_crawl":
            from src.services.crawler.scrapy_app.spiders.generic import GenericCrawlSpider as _SpiderCls
        else:
            err = f"unknown spider_name: {spider_name!r} (expected 'topic_search' or 'generic_crawl')"
            print(f"[launcher] {err}", file=sys.stderr)
            _mark_job_safe(db_path, job_id, "failed", finished=True, error_msg=err)
            return 1
        print(
            f"[launcher] resolved spider class: {_SpiderCls.__module__}.{_SpiderCls.__name__}",
            file=sys.stderr,
            flush=True,
        )
    except Exception as _e:
        # import Spider 失败（比如模块内部有语法错误或依赖缺失）
        print(f"[launcher] import spider class FAILED: {type(_e).__name__}: {_e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        _mark_job_safe(db_path, job_id, "failed", finished=True, error_msg=f"ImportSpider: {type(_e).__name__}: {_e}")
        return 1

    # --- 【关键 2】构造 Crawler 对象 + 监听 spider_opened 信号，解决 Scrapy 2.17 不自动调 start_requests() 的问题 ---
    #
    # 问题背景（已在 _debug_launcher_env.py 中最小复现并验证修复方案有效）：
    #   在 Windows + Python 3.14 + Scrapy 2.17 环境下，若调用 CrawlerProcess.crawl(SpiderClass, **kwargs)
    #   传入 Spider 类，Engine 在 Spider opened 后不会自动调用 spider.start_requests()，导致整个
    #   spider 在 0.001s 内 finish，没有任何请求发出。
    # 修复方案：
    #   1. 先显式构造 Crawler(SpiderClass, scrapy_settings) 对象；
    #   2. 监听 crawler.signals 的 spider_opened；
    #   3. 在回调中：拿到 spider 实例 → 手动调用 spider.start_requests() → 遍历产生的 Request，
    #      用 crawler.engine.crawl(req) 逐个塞进 Scheduler（Engine.crawl 在 Scrapy 2.17 中只接受
    #      一个 positional argument，spider 关联由 Engine 当前打开的 slot 自动完成）；
    #   4. 最后调用 process.crawl(crawler, **spider_kwargs) 把 Crawler 对象交给 Process 调度。
    #   这样完全绕开 Scrapy 自身 "自动调 start_requests" 这条有 bug 的代码路径。
    #
    from scrapy import signals as _scrapy_signals
    from scrapy.crawler import Crawler as _ScrapyCrawler

    crawler = _ScrapyCrawler(_SpiderCls, scrapy_settings)
    _sr_flag_done = False  # 防止 signal 被多次触发时重复调 start_requests

    def _on_spider_opened(*_args, **_kwargs):
        nonlocal _sr_flag_done
        _spider_ref = _kwargs.get("spider") or (_args[0] if _args else None)
        if not _spider_ref or _sr_flag_done:
            return
        _sr_flag_done = True

        # 【关键】reactor.callLater(0, ...)：把"调 start_requests + 塞 Request"推迟到下一个 reactor tick。
        # 原因：spider_opened 信号会触发多个中间件（尤其是 OffsiteMiddleware）的初始化，
        #   OffsiteMiddleware.spider_opened 会初始化 self.host_regex；
        #   而一旦我们把 Request 塞给 Engine，它立即会发 request_scheduled 信号，
        #   OffsiteMiddleware.request_scheduled 又需要 host_regex。
        #   如果 spider_opened 信号回调的连接顺序是：
        #     我们的回调 → OffsiteMiddleware.request_scheduled → OffsiteMiddleware.spider_opened
        #   就会在塞 Request 时遇到 AttributeError: host_regex。
        #   推迟到下一个 reactor tick，可以确保 spider_opened 的**所有**回调都先跑完。
        def _enqueue_later():
            try:
                _req_iter = _spider_ref.start_requests()
                _req_list = list(_req_iter or [])
                print(
                    f"[launcher.signal/spider_opened(callLater)] start_requests() => "
                    f"{len(_req_list)} requests, enqueueing via engine.crawl(req) ...",
                    file=sys.stderr,
                    flush=True,
                )
                for _r in _req_list:
                    crawler.engine.crawl(_r)
            except Exception as _e:
                print(
                    f"[launcher.signal/spider_opened(callLater)] ERROR while enqueueing "
                    f"start_requests: {type(_e).__name__}: {_e}",
                    file=sys.stderr,
                    flush=True,
                )
                traceback.print_exc(file=sys.stderr)

        try:
            from twisted.internet import reactor as _tw_reactor

            _tw_reactor.callLater(0, _enqueue_later)
        except Exception:
            # 万一 reactor 还没好（罕见），直接立即塞，总比啥都不做强
            _enqueue_later()

    crawler.signals.connect(_on_spider_opened, signal=_scrapy_signals.spider_opened)

    # 启动爬虫（blocking 直到爬完或崩溃）
    # 外层 try 捕获 reactor 跑起来之后任何未被 Scrapy 自身处理的异常
    # Scrapy 设计上 crawl 过程里 spider 级别的错误会自己记在 stats 里，不会抛到 process.start 外层
    # 但我们还是加一层兜底，极端情况下保证把 DB 标为 failed
    fatal_err: str | None = None
    try:
        # 【关键】把 Crawler 对象（不是 Spider 类）交给 Process，并附带 spider 构造 kwargs
        process.crawl(crawler, **spider_kwargs)
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
