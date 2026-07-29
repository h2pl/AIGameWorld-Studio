"""Crawler 依赖注入 —— 复用 SQLiteStore，不依赖 KnowledgeManager。

与 knowledge/deps.py 的 ``project_root`` / ``data_dir_from_app`` 共用同一套 app.state 注入。
"""

# ---- 导入依赖 ----
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException, Request

# ---- 服务层导入 ----
from ...services.crawler import CrawlerService
from ...utils.sqlite_store import SQLiteStore


# ---- 辅助函数: 项目根目录解析 ----
def _project_root(request: Request) -> Path:
    """从 app.state.PROJECT_ROOT 取值（serve.py 启动时注入），兜底通过当前文件向上回溯两级定位 src."""
    # app.state.PROJECT_ROOT 由 serve.py 在启动时显式注入（绝对路径），避免这里猜测路径
    root: Path | None = getattr(request.app.state, "PROJECT_ROOT", None)
    if root is None:
        # 兜底：src/api/crawler/deps.py → src → 往上一级就是项目根
        root = Path(__file__).resolve().parents[2]
    return root


# ---- 辅助函数: data 目录解析 ----
def _data_dir(request: Request) -> Path:
    """从 app.state.DATA_DIR 取值（SQLite 所在父目录，绝对路径），默认走 project_root/data."""
    # DATA_DIR 允许部署时用环境变量覆盖，比如把 DB 和 staging 存到 NFS 盘
    d: Path | None = getattr(request.app.state, "DATA_DIR", None)
    if d is None:
        d = _project_root(request) / "data"
    return d


# ---- 依赖: CrawlerService 注入 ----
def get_crawler_service(request: Request) -> Iterator[CrawlerService]:
    """每请求一个 CrawlerService（共享 SQLiteStore 路径，独立连接）。

    migrations 由 serve.py 启动时 ``_migrate_once`` 统一跑过，这里不再重复；
    仅在直接 import 绕过 serve 启动时做幂等兜底。
    """
    # 1. 目录解析：获取项目根目录和 data 目录
    root = _project_root(request)
    dd = _data_dir(request)
    # data 目录可能还没创建（首次启动空环境），先建好避免 sqlite3 报 unable to open
    dd.mkdir(parents=True, exist_ok=True)

    # 2. SQLite 连接：每个请求独立连接，避免跨线程问题
    store = SQLiteStore(dd / "studio.db")
    # 兜底：若启动迁移没跑（比如直接 import 不走 serve），这里补一次（幂等）
    try:
        store.run_migrations(root / "migrations")
    except Exception:
        # 迁移失败不阻塞请求：表不存在时后续 DB 操作会报错，由上游统一返回 5xx
        pass

    # 3. 构造服务实例并通过生成器注入
    svc = CrawlerService(store=store, project_root=root)
    try:
        yield svc
    finally:
        # 请求结束即关闭 SQLite 连接（SQLite 连接跨线程不安全，别复用）
        store.close()


# ---- 依赖: topic_id 参数校验 ----
def require_topic_id(topic_id: str) -> str:
    """校验并返回 strip 后的 topic_id（轻量参数检查，不查 DB 表存在性）."""
    # 轻量校验：只判断非空；涉及表存在性由 service 层在执行前查 kb_topic 表
    if not topic_id or not topic_id.strip():
        raise HTTPException(400, detail="topic_id 不能为空")
    return topic_id.strip()
