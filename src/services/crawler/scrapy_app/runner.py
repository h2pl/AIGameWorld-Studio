"""Scrapy runner — 子进程架构（v3，彻底解决 reactor/asyncio 兼容性）.

设计说明 v3
-----------
v1/v2 尝试在当前进程启动 reactor 线程（和 Uvicorn 主进程共享 Python 解释器），
在 Windows + Python 3.14 下，`AsyncioSelectorReactor` / `SelectReactor` 都卡在
engine 启动前（spider_opened 之后就不前进了），根本原因是和 Uvicorn 的
asyncio.ProactorEventLoop 冲突或 Twisted 新版本对 selector 的使用方式有问题。

v3 改为 **独立 Python 子进程** 执行 Scrapy 任务：
1. 本模块只负责 **启动子进程 + 状态跟踪**，不 import scrapy/twisted；
   （如果用户想关闭该功能，import 不会炸）
2. 真正跑 Scrapy 的代码在 :mod:`_job_launcher`，子进程自己 install reactor、
   自己跑 CrawlerProcess，和主进程完全隔离；
3. job 状态 / item 状态 / 计数 全部通过 SQLite 共享（和 v2 完全相同），
   主进程轮询 get_job() 即可，完全不需要跨进程通信；
4. 子进程 stdout/stderr 默认重定向到 ``{staging}/_launcher.log``，
   不污染 FastAPI 主进程日志；
5. Scrapy 自己的日志仍写 ``{staging}/_scrapy.log``（由 settings.LOG_FILE 控制）。

对外 API（与 v2 完全兼容，service.py 无需改动）：
    run_generic_crawl(**kw) -> JobLaunchResult
    run_topic_crawl(**kw)   -> JobLaunchResult
    is_running(job_id)      -> bool
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 模块级 logger
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 进程跟踪（模块级单例）
# ---------------------------------------------------------------------------
# 保护 _RUNNING 字典的读写锁（多线程并发访问用）
_LOCK = threading.Lock()
# job_id -> {"proc": Popen, "monitor_thread": Thread}
# 内存中正在运行的 job 跟踪表
_RUNNING: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# 返回值结构
# ---------------------------------------------------------------------------

# 启动函数的返回值数据类，保持与 v2 API 兼容
@dataclass
class JobLaunchResult:
    # 被启动的 job ID
    job_id: str
    # 兼容 v2 字段；子进程模式下等价于 proc 已启动
    thread_started: bool


# ---------------------------------------------------------------------------
# 公共辅助：失败安全地标 DB 为 failed（子进程启动失败时用）
# ---------------------------------------------------------------------------

# 启动失败时兜底把 job 标记为 failed（不抛异常）
# 参数 db_path：SQLite DB 文件路径
# 参数 job_id：任务 ID
# 参数 err：错误消息字符串
# 返回 None
def _mark_failed_safe(db_path: Path, job_id: str, err: str) -> None:
    # 延迟导入避免循环引用
    try:
        from ..store import update_job_status  # type: ignore
        from ....utils.sqlite_store import SQLiteStore as _S  # 延迟导入 SQLiteStore

        # db_path 为空就啥也不做
        if not db_path:
            return
        # 打开独立 SQLite 连接（本模块不持有长连接）
        store = _S(Path(db_path))
        try:
            # 截断错误消息到 500 字符防溢出
            update_job_status(store, job_id, "failed", finished=True, error_msg=str(err)[:500])
        finally:
            # 无论成功失败都关闭连接
            store.close()
    except Exception:
        # 整个标记过程再套一层兜底（避免 launcher 自身 bug 把主进程弄崩）
        # 仅记日志，不向上抛
        _log.exception("mark job=%s failed crashed", job_id)


# ---------------------------------------------------------------------------
# 子进程入口定位（我们把 launcher 和本模块放同一目录）
# ---------------------------------------------------------------------------

# 找到 _job_launcher.py 的绝对路径
# 返回 Path：launcher 脚本路径
def _entry_script_path() -> Path:
    # runner.py 所在目录（scrapy_app/）
    here = Path(__file__).resolve().parent
    # 同目录下的 launcher 脚本
    entry = here / "_job_launcher.py"
    # 理论上不会发生，但还是防御一下
    if not entry.exists():
        raise FileNotFoundError(f"Scrapy job launcher missing: {entry}")
    # 返回 launcher 脚本 Path
    return entry


# 定位可执行的 Python 解释器（优先 venv）
# 返回 str：Python 可执行文件的绝对路径字符串
def _find_python_exe() -> str:
    """优先用当前 venv 的 python.exe，退而求其次用 sys.executable."""
    # uv run python 场景：sys.executable 就是 .venv/Scripts/python.exe
    exe = Path(sys.executable)
    # 名字已经是标准 python 可执行文件
    if exe.name.lower() in ("python.exe", "pythonw.exe", "python"):
        # 直接返回
        return str(exe)
    # 兜底：按项目约定在 .venv/Scripts/python.exe（Windows）或 .venv/bin/python（Linux/Mac）
    # src/services/crawler/scrapy_app → 向上 5 层到 project root
    root = Path(__file__).resolve().parents[5]
    # 候选路径列表（Windows 在前，Linux/Mac 在后）
    candidates = [
        # Windows venv
        root / ".venv" / "Scripts" / "python.exe",
        # POSIX venv
        root / ".venv" / "bin" / "python",
    ]
    # 遍历候选路径
    for c in candidates:
        # 找到存在的可执行文件
        if c.exists():
            # 返回
            return str(c)
    # 最后兜底：原样返回 sys.executable（哪怕名字不对）
    return str(exe)


# ---------------------------------------------------------------------------
# 子进程启动 & 监控
# ---------------------------------------------------------------------------

# 核心：把参数写 JSON，拉起子进程，启监控线程
# 参数 job_id：任务 ID
# 参数 db_path：SQLite DB 文件路径
# 参数 staging_dir：暂存目录 Path
# 参数 spider_name：要启动的 spider 名（generic_crawl / topic_search）
# 参数 spider_kwargs：传给 Spider __init__ 的 kwargs 字典
# 参数 scrapy_settings：完整的 Scrapy settings dict
# 返回 JobLaunchResult：包含 job_id 和 thread_started 标记
def _launch_subprocess(
    *,
    job_id: str,
    db_path: Path,
    staging_dir: Path,
    spider_name: str,
    spider_kwargs: dict[str, Any],
    scrapy_settings: dict[str, Any],
) -> JobLaunchResult:
    # --- 序列化参数到 staging，避免 Windows cmd 超长/转义噩梦 ---
    # 确保是 Path 对象
    staging_dir = Path(staging_dir)
    # 保证 staging 目录存在
    staging_dir.mkdir(parents=True, exist_ok=True)
    # 参数 JSON 落盘路径
    args_file = staging_dir / "_launcher_args.json"
    # 组装传给子进程的完整参数
    payload = {
        # job ID
        "job_id": job_id,
        # SQLite DB 路径（转绝对路径字符串）
        "db_path": str(Path(db_path)),
        # staging 目录绝对路径
        "staging_dir": str(staging_dir),
        # 要跑的 spider 名：generic_crawl / topic_search
        "spider_name": spider_name,
        # 传给 Spider __init__ 的 kwargs
        "spider_kwargs": spider_kwargs,
        # 完整的 Scrapy settings dict
        "scrapy_settings": scrapy_settings,
    }
    # 写参数 JSON 文件，处理异常
    try:
        # 以 UTF-8 写入 JSON
        args_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        # 写文件失败（权限、磁盘满等）
        # 把 DB 中 job 标记 failed
        _mark_failed_safe(Path(db_path), job_id, f"write launcher args failed: {e}")
        # 启动失败
        return JobLaunchResult(job_id=job_id, thread_started=False)

    # --- 构造命令：python _job_launcher.py --args-file <path> ---
    # 找到 Python 解释器可执行文件
    py = _find_python_exe()
    # 找到 _job_launcher.py 脚本路径
    entry = _entry_script_path()
    # 子进程 PYTHONPATH 要包含项目根，确保能 import src.*（无论用户从哪层目录启动服务）
    # scrapy_app/ → 向上 5 层到项目根
    root = Path(__file__).resolve().parents[5]
    # 复制当前进程环境变量
    env = os.environ.copy()
    # 已有的 PYTHONPATH（可能为空）
    existing_pp = env.get("PYTHONPATH", "")
    # 项目根前置到 PYTHONPATH
    env["PYTHONPATH"] = str(root) + (os.pathsep + existing_pp if existing_pp else "")

    # launcher 子进程自身的日志（不是 Scrapy 日志）
    launcher_log = staging_dir / "_launcher.log"
    try:
        # 行缓冲写日志文件
        log_fh = open(launcher_log, "w", encoding="utf-8", buffering=1)
    except Exception:
        # 打开日志失败则丢到 DEVNULL（不影响主流程）
        log_fh = subprocess.DEVNULL

    # -u 禁用 stdout 缓冲，日志即时写入
    cmd = [py, "-u", str(entry), "--args-file", str(args_file)]

    # 记录启动日志
    _log.info("[job=%s] launching scrapy subprocess: %s (cwd=%s)", job_id, " ".join(cmd[:4]) + " ...", root)

    # 启动子进程（不阻塞主进程）
    try:
        # 子进程不继承 stdin（避免挂在交互式输入上）
        proc = subprocess.Popen(
            # 命令列表
            cmd,
            # 子进程工作目录设为项目根
            cwd=str(root),
            # 注入增强后的环境变量
            env=env,
            # 关闭 stdin，避免 Python 等待用户输入
            stdin=subprocess.DEVNULL,
            # 标准输出重定向到日志文件
            stdout=log_fh,
            # 标准错误合并到 stdout（都写日志）
            stderr=subprocess.STDOUT,
            # 二进制模式，避免编码层开销
            text=False,
            # 无缓冲，配合 -u 立即写
            bufsize=0,
        )
    except Exception as e:
        # Popen 失败（找不到解释器、权限不足等）
        # DB 标记失败
        _mark_failed_safe(Path(db_path), job_id, f"Popen failed: {type(e).__name__}: {e}")
        # 记异常栈
        _log.exception("[job=%s] Popen failed", job_id)
        # 返回启动失败
        return JobLaunchResult(job_id=job_id, thread_started=False)

    # --- 启动监控线程（等子进程结束 + 清理 tracking） ---
    # 加锁写 _RUNNING
    with _LOCK:
        # 先登记 proc，监控线程稍后填
        _RUNNING[job_id] = {"proc": proc, "monitor_thread": None}

    # 监控线程函数：等子进程退出，超时则强杀，最后清理
    def _monitor():
        # return code 初始 None
        rc = None
        try:
            # 最多跑 24h（硬上限），之后主进程里 DB 轮询会看到仍 running
            rc = proc.wait(timeout=24 * 3600)
        except subprocess.TimeoutExpired:
            # 24h 到了子进程还没结束
            # 记警告
            _log.warning("[job=%s] subprocess exceeded 24h, killing", job_id)
            try:
                # 发 SIGKILL 强杀
                proc.kill()
            except Exception:
                # kill 也可能失败（比如已经退出了）
                pass
            try:
                # 杀完再等 15s 回收
                rc = proc.wait(timeout=15)
            except Exception:
                # 15s 也没收尸到，给个默认 -9
                rc = -9
            # DB 标超时失败
            _mark_failed_safe(Path(db_path), job_id, f"killed after 24h (rc={rc})")
        finally:
            # 无论正常退出还是被 kill，都执行清理
            # 清理 tracking 表
            # 加锁
            with _LOCK:
                # 从 _RUNNING 移除（不存在也不报错）
                _RUNNING.pop(job_id, None)
            try:
                # log_fh 是文件句柄（不是 DEVNULL）
                if log_fh and hasattr(log_fh, "close"):
                    # 关闭日志文件
                    log_fh.close()
            except Exception:
                # 关闭失败也不影响主流程
                pass
            # 记录退出码
            _log.info("[job=%s] subprocess finished rc=%s", job_id, rc)

    # daemon 线程：主进程退出不阻塞
    t = threading.Thread(target=_monitor, name=f"ags-scrapy-{job_id[:8]}", daemon=True)
    # 加锁填 monitor_thread 引用
    with _LOCK:
        # 再次确认；防止极罕见竞态（比如 _monitor 已跑完把条目 pop 掉）
        # 条目仍在
        if job_id in _RUNNING:
            # 填线程引用
            _RUNNING[job_id]["monitor_thread"] = t
    # 启动监控线程
    t.start()
    # 返回启动成功
    return JobLaunchResult(job_id=job_id, thread_started=True)


# ---------------------------------------------------------------------------
# Settings 构造（和 v2 一样，但不 import Scrapy；直接返回 dict，传给子进程序列化）
# ---------------------------------------------------------------------------

# 以 settings.py 大写常量为基准，叠加本次 job 的运行时参数
# 参数 job_id：任务 ID
# 参数 staging_dir：暂存目录 Path
# 参数 db_path：SQLite DB 路径
# 参数 log_level：Scrapy 日志级别
# 参数 concurrent_requests：全局并发请求数
# 参数 concurrent_per_domain：单域名并发请求数
# 参数 download_delay：同域下载延迟秒数
# 参数 extra：可选的额外 settings dict
# 返回 dict[str, Any]：最终的 settings 字典
def _build_settings_dict(
    *,
    job_id: str,
    staging_dir: Path,
    db_path: Path,
    log_level: str = "INFO",
    concurrent_requests: int = 8,
    concurrent_per_domain: int = 2,
    download_delay: float = 1.0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 settings dict（不 import Scrapy，由子进程真正组装成 Settings 对象）."""
    # 延迟导入默认 settings 模块
    from . import settings as default_mod

    # 取模块所有大写属性作为默认配置
    defaults = {k: getattr(default_mod, k) for k in dir(default_mod) if k.isupper()}
    # dict-valued settings (ITEM_PIPELINES etc) are already dicts here; fine
    # 把本次 job_id 注入 settings（Pipeline / Extension 要用）
    defaults["JOB_ID"] = job_id
    # 注入 staging 目录
    defaults["STAGING_DIR"] = str(staging_dir)
    # 注入 DB 路径
    defaults["DB_PATH"] = str(db_path)

    # Scrapy 自身日志级别（默认 INFO）
    defaults["LOG_LEVEL"] = log_level
    # Scrapy 日志写 staging 下的 _scrapy.log
    defaults["LOG_FILE"] = str(Path(staging_dir) / "_scrapy.log")
    # 不把 print 重定向到日志（子进程 stdout 已走 launcher.log）
    defaults["LOG_STDOUT"] = False

    # 全局并发下限 1，避免 0 或负数
    defaults["CONCURRENT_REQUESTS"] = max(1, int(concurrent_requests))
    # 单域并发下限 1
    defaults["CONCURRENT_REQUESTS_PER_DOMAIN"] = max(1, int(concurrent_per_domain))
    # 下载延迟下限 0（不允许负数）
    defaults["DOWNLOAD_DELAY"] = max(0.0, float(download_delay))

    # NOTE: JOBDIR 暂不启用 — 在 Windows 上多次测试发现 Scrapy 会因为 JOBDIR
    # 的存在而跳过 start_requests()（即使清理了目录，新建的空目录里 active.json 是 "{}"
    # 也会导致 Engine 认为调度已恢复完毕，scheduler.enqueued=0 的症状）。
    # 我们的 job 状态由 SQLite 独立追踪 + 24h 硬超时，不需要 Scrapy 原生断点续爬。
    # 启用 HTTP 缓存目录（重复 URL 不重复下载）
    defaults["HTTPCACHE_DIR"] = str(Path(staging_dir) / "_httpcache")
    # Scrapy 2.x 有个内置 spider state 如果没被明确禁用，仍可能尝试持久化；显式设一个空目录禁止它
    # 不要设置 JOBDIR（Scrapy 默认即不启用断点续爬），避免在 Windows 上触发
    # "start_requests() 被跳过，scheduler.enqueued=0" 的 bug。
    # settings.py 默认值里万一有 JOBDIR
    if "JOBDIR" in defaults:
        # 强制删掉，防止触发 Windows bug
        del defaults["JOBDIR"]

    # 调用方传了额外 settings（topic_search 传 MAX_RESULTS 等）
    if extra:
        # 覆盖合并
        defaults.update(extra)
        # 别让 extra 里的 JOBDIR 覆盖我们
        if "JOBDIR" in defaults and defaults.get("JOBDIR") is None or not defaults.get("JOBDIR"):
            # 占位：如果 extra 里的 JOBDIR 本来就是空，啥也不做
            pass
        # 无论传了啥 JOBDIR，一律 pop 掉（强制不用断点续爬）
        defaults.pop("JOBDIR", None)
    # 返回最终 settings 字典
    return defaults


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------

# 内部默认并发/速率参数（与 service 层默认一致）
_SCRAPY_DEFAULTS_INTERNAL = dict(
    # 全局并发默认 8
    concurrent_requests=8,
    # 单域并发默认 2
    concurrent_per_domain=2,
    # 默认下载延迟 1s
    download_delay=1.0,
    # 默认日志级别 INFO
    log_level="INFO",
)


# 对外 API：启动贴 URL 模式的 GenericSpider
# 参数 job_id：任务 ID
# 参数 staging_dir：暂存目录 Path
# 参数 db_path：SQLite DB 路径
# 参数 urls：起始 URL 列表
# 参数 url_item_ids：URL→item_id 映射
# 参数 url_titles：可选的 URL→标题映射
# 参数 allowed_domains：可选的允许域名白名单
# 参数 follow_links：是否追链
# 参数 follow_depth：追链深度
# 参数 concurrent_requests：全局并发请求数
# 参数 concurrent_per_domain：单域并发请求数
# 参数 download_delay：同域下载延迟秒数
# 参数 log_level：Scrapy 日志级别
# 返回 JobLaunchResult：启动结果
def run_generic_crawl(
    *,
    job_id: str,
    staging_dir: Path,
    db_path: Path,
    urls: list[str],
    url_item_ids: dict[str, str],
    url_titles: dict[str, str] | None = None,
    allowed_domains: list[str] | None = None,
    follow_links: bool = False,
    follow_depth: int = 0,
    concurrent_requests: int = 8,
    concurrent_per_domain: int = 2,
    download_delay: float = 1.0,
    log_level: str = "INFO",
) -> JobLaunchResult:
    # 先构造 settings dict
    settings_dict = _build_settings_dict(
        job_id=job_id,
        staging_dir=staging_dir,
        db_path=db_path,
        log_level=log_level,
        concurrent_requests=concurrent_requests,
        concurrent_per_domain=concurrent_per_domain,
        download_delay=download_delay,
    )
    # 组装传给 Spider 构造函数的参数
    spider_kwargs = dict(
        # job ID
        job_id=job_id,
        # 起始 URL 列表（copy 一份避免外部改 list）
        seed_urls=list(urls),
        # URL→item_id 映射（copy 一份）
        url_item_ids=dict(url_item_ids),
        # URL→标题映射（可能为空 dict）
        url_titles=dict(url_titles or {}),
        # 是否追链
        follow_links=bool(follow_links),
        # 追链深度（下限 0）
        follow_depth=max(0, int(follow_depth or 0)),
        # 允许的域名白名单，None 表示不限制
        allowed_domains=list(allowed_domains) if allowed_domains else None,
    )
    # 统一走子进程启动流程
    return _launch_subprocess(
        job_id=job_id,
        db_path=Path(db_path),
        staging_dir=Path(staging_dir),
        # 对应 spiders/generic.py 的 name
        spider_name="generic_crawl",
        spider_kwargs=spider_kwargs,
        scrapy_settings=settings_dict,
    )


# 对外 API：启动搜索模式的 TopicSearchSpider
# 参数 job_id：任务 ID
# 参数 staging_dir：暂存目录 Path
# 参数 db_path：SQLite DB 路径
# 参数 query：搜索关键词
# 参数 max_results：搜索结果条数上限
# 参数 allowed_domains：可选的允许域名白名单
# 参数 follow_links：是否追链
# 参数 follow_depth：追链深度
# 参数 concurrent_requests：全局并发请求数
# 参数 concurrent_per_domain：单域并发请求数
# 参数 download_delay：同域下载延迟秒数
# 参数 log_level：Scrapy 日志级别
# 返回 JobLaunchResult：启动结果
def run_topic_crawl(
    *,
    job_id: str,
    staging_dir: Path,
    db_path: Path,
    query: str,
    max_results: int = 10,
    allowed_domains: list[str] | None = None,
    follow_links: bool = False,
    follow_depth: int = 0,
    concurrent_requests: int = 8,
    concurrent_per_domain: int = 2,
    download_delay: float = 1.0,
    log_level: str = "INFO",
) -> JobLaunchResult:
    # 构造 settings，带 extra 自定义项
    settings_dict = _build_settings_dict(
        job_id=job_id,
        staging_dir=staging_dir,
        db_path=db_path,
        log_level=log_level,
        concurrent_requests=concurrent_requests,
        concurrent_per_domain=concurrent_per_domain,
        download_delay=download_delay,
        # Spider/Pipeline 可能用到的自定义 setting
        extra={"MAX_RESULTS": int(max_results), "CRAWL_QUERY": str(query)},
    )
    # Spider 构造参数
    spider_kwargs = dict(
        # job ID
        job_id=job_id,
        # 搜索关键词
        query=str(query),
        # 搜索结果数上限
        max_results=int(max_results),
        # Spider 内部要写 DB 建 item，所以传 db 路径
        store_db_path=str(Path(db_path)),
        # 是否追链
        follow_links=bool(follow_links),
        # 追链深度下限 0
        follow_depth=max(0, int(follow_depth or 0)),
        # 域名白名单
        allowed_domains=list(allowed_domains) if allowed_domains else None,
    )
    # 子进程启动
    return _launch_subprocess(
        job_id=job_id,
        db_path=Path(db_path),
        staging_dir=Path(staging_dir),
        # 对应 spiders/topic_search.py 的 name
        spider_name="topic_search",
        spider_kwargs=spider_kwargs,
        scrapy_settings=settings_dict,
    )


# 查内存中跟踪表 + 子进程 poll，判断 job 是否仍在运行
# 参数 job_id：任务 ID
# 返回 bool：True 表示子进程仍在运行，False 表示未启动或已结束
def is_running(job_id: str) -> bool:
    # 加锁读 _RUNNING
    with _LOCK:
        # 取跟踪条目
        entry = _RUNNING.get(job_id)
    # 内存表没记录（没启动过 / 已跑完清理）
    if entry is None:
        # 视为未运行
        return False
    # 取 Popen 对象
    proc: subprocess.Popen | None = entry.get("proc")
    # 条目里没 proc（理论不可能）
    if proc is None:
        # 视为未运行
        return False
    # poll 返回 None 表示子进程还在运行；返回 int 表示已退出
    return proc.poll() is None
