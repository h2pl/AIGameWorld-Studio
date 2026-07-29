"""依赖注入 / FastAPI Depends helpers for knowledge APIs.

所有 ``Depends(...)`` 用的 helper 集中放在这里：目录解析 / SQLite 连接 /
向量库 factory 注入 / 主题 id 校验 / 启动时一次性迁移。
"""

# ---- 导入依赖 ----
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException, Path as FPath, Request

# ---- 业务层依赖 ----
# KnowledgeManager 负责 KB 全流程；KBVectorStoreFactory 负责按环境变量切换向量库后端
from ...services.knowledge.manager import KnowledgeManager
from ...services.knowledge.vector_store import KBVectorStoreFactory
from ...utils.sqlite_store import SQLiteStore


# ---- 辅助函数: 项目根目录解析 ----
def project_root(request: Request) -> Path:
    """从 app.state 里拿项目根目录（serve.py 启动时注入）."""
    # app.state.PROJECT_ROOT 由 serve.py 在启动时显式注入（绝对路径），避免这里猜测路径
    root: Path | None = getattr(request.app.state, "PROJECT_ROOT", None)
    if root is None:
        # 兜底：src/api/knowledge/deps.py → 向上 3 级刚好是项目根（有 pyproject.toml 的那个目录）
        root = Path(__file__).resolve().parents[2]
    return root


# ---- 辅助函数: Chroma 目录解析（兼容保留） ----
def chroma_path_from_app(request: Request) -> Path:
    """兼容保留：从 app.state 里拿 chroma 目录（KB_VECTOR_STORE=chroma 时 factory 才会用到）."""
    # 保留这个 Depends 是为了兼容旧配置：默认 Qdrant，但用户没 Docker 时可以一键切回本地文件 Chroma
    p: Path | None = getattr(request.app.state, "CHROMA_PATH", None)
    if p is None:
        # 默认路径 data/chroma/（和旧版本保持一致，方便平滑迁移）
        p = project_root(request) / "data" / "chroma"
    return p


# ---- 辅助函数: data 目录解析 ----
def data_dir_from_app(request: Request) -> Path:
    """从 app.state 里拿 data 目录（SQLite 所在父目录，必须已经是绝对路径），默认 project_root/data."""
    # DATA_DIR 允许部署时用环境变量覆盖，比如把 DB 和 staging 存到 NFS 盘
    d: Path | None = getattr(request.app.state, "DATA_DIR", None)
    if d is None:
        d = project_root(request) / "data"
    return d


# ---- 依赖: KnowledgeManager 注入 ----
def get_knowledge_manager(
    request: Request,
) -> Iterator[KnowledgeManager]:
    """全局单例式 Depends — 返回不带作用域的 KnowledgeManager.

    - 向量库：通过 KBVectorStoreFactory.get_default() 按 KB_VECTOR_STORE 环境变量选择 Qdrant/Chroma
      （默认：qdrant，Docker 容器暴露 http://127.0.0.1:6333；设 KB_VECTOR_STORE=chroma 切回旧模式）
    - SQLite：project_root/data/studio.db，每次请求前确保 migrations 已跑（store.run_migrations 幂等）
    - 自动 ensure 默认主题（genshin / wow_worldview），打开 UI 直接可见
    - 请求结束后自动 close
    """
    # 1. 目录解析：获取项目根目录、data 目录、migrations 目录
    root = project_root(request)
    dd = data_dir_from_app(request)
    migrations_dir = root / "migrations"
    # data 目录在首次启动时可能不存在（比如全新 clone 的仓库），mkdir 避免 SQLite 报 unable to open
    dd.mkdir(parents=True, exist_ok=True)

    # 2. 向量库：按环境变量切换，不再手写 chromadb client
    # 允许 serve.py 在启动时在 app.state 注入自定义 factory；否则走默认单例
    factory: KBVectorStoreFactory | None = getattr(
        request.app.state, "KB_VECTOR_FACTORY", None
    )
    if factory is None:
        # 默认单例：进程内共享同一个 factory 实例（避免每请求重新建立 Qdrant gRPC 连接）
        factory = KBVectorStoreFactory.get_default(project_root=root)

    # 3. SQLite 连接：用 data/studio.db；每个请求新建独立连接，避免 SQLite 跨线程共享出问题
    store = SQLiteStore(dd / "studio.db")
    # 幂等迁移：版本号由 migrations 表管理，重复跑不报错
    store.run_migrations(migrations_dir)

    # 4. 构造 KnowledgeManager 实例并通过生成器注入
    kb = KnowledgeManager(
        factory=factory,
        store=store,
        project_root=root,
        auto_run_migrations=False,
        # 上面 store.run_migrations 已经跑过一次，避免重复
        created_by="ui",
        bootstrap_default_topics=True,
        # 首启自动建 genshin / wow_worldview 两个默认主题占位
    )
    try:
        yield kb
    finally:
        # 关闭 SQLite 连接 + 释放 manager 内持有但非线程共享的轻量资源
        kb.close()


# ---- 依赖: topic_id 参数校验 ----
def require_topic_id(
    topic_id: str = FPath(..., description="主题 topic_id，如 mordor_lore"),
) -> str:
    """校验并返回 strip 后的 topic_id（轻量级，不查数据库）."""
    # 这里只做最基本的非空校验；涉及表存在性校验由 service 层在执行前查 kb_topic 表
    if not topic_id or not topic_id.strip():
        raise HTTPException(400, detail="topic_id 不能为空")
    return topic_id.strip()


# ---- 启动时一次性迁移（带缓存） ----
@lru_cache(maxsize=1)
def _migrate_once(root: Path) -> list[str]:
    """进程内只跑一次迁移（serve.py 启动时调一下就好）."""
    # 带 lru_cache(maxsize=1)：重复调也只执行一次，避免多 worker 下重复写 SQLite 迁移表
    store = SQLiteStore(root / "data" / "studio.db")
    try:
        return store.run_migrations(root / "migrations")
    finally:
        # 用完就关：启动时的临时连接，不参与后续请求复用
        store.close()
