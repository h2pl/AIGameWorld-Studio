"""Studio 知识库 MCP Server / Knowledge-base MCP server for AIGameWorld Studio.

把 Studio 的知识库检索能力以 **MCP（Model Context Protocol）** 协议暴露给外部
进程（主项目 AIGameWorld backend）。独立于主服务（``aw-studio serve``），以 stdio
transport 作为独立 MCP server 进程运行。

设计要点：
- 用 FastMCP（``mcp`` 库）声明工具，独立进程、stdio 通信，主项目 backend 作为
  MCP client 通过 langchain-mcp-adapters 连接。
- 复用 Studio 已有的 ``KnowledgeManager``（混合检索：BGE-M3 dense + BM25 + CrossEncoder
  重排，后端 Qdrant），不引入第二套向量库。
- 暴露两个工具（用户确认的设计）：
    - ``list_topics()``：列出全部知识库主题（topic_slug / name / description）。
    - ``search(topic_slug, query, top_k, min_score)``：指定主题的混合语义检索。

启动方式：
    aw-studio mcp                 # stdio 模式（被主项目 client 拉起）
    python -m src.mcp_server      # 等价

环境变量：
    KB_VECTOR_STORE  qdrant|chroma|sqlite  向量库后端（默认 qdrant）
    QDRANT_URL        Qdrant 地址（默认 http://127.0.0.1:6333）
    STUDIO_DATA_DIR   SQLite / 数据根目录（默认 <project_root>/data）
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .services.knowledge.index.vector_store import KBVectorStoreFactory

# ── Studio 内部依赖 / Studio internal deps ──
from .services.knowledge.manager import KnowledgeManager
from .utils.sqlite_store import SQLiteStore

_log = logging.getLogger(__name__)

# MCP server 名称 / server name
_MCP_NAME = "aw-studio-knowledge"

# Studio 项目根（src/mcp_server.py → 向上两级）/ project root
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def _build_manager() -> KnowledgeManager:
    """构造一个 KnowledgeManager 单例（进程内共享，MCP 工具复用其检索编排）."""
    # 解析数据目录（可经环境变量覆盖）
    data_dir = Path(os.environ.get("STUDIO_DATA_DIR", str(PROJECT_ROOT / "data")))
    data_dir.mkdir(parents=True, exist_ok=True)

    # 选择默认向量库后端（qdrant/chroma/sqlite）
    factory = KBVectorStoreFactory.get_default(project_root=PROJECT_ROOT)
    store = SQLiteStore(data_dir / "studio.db")
    # 幂等建表（与 serve.py 一致）
    migrations_dir = PROJECT_ROOT / "migrations"
    if migrations_dir.exists():
        store.init_schema(migrations_dir)

    return KnowledgeManager(
        factory=factory,
        store=store,
        project_root=PROJECT_ROOT,
        auto_init_schema=False,
        created_by="mcp",
        bootstrap_default_topics=True,
    )


# 进程级单例 / process-wide singleton
_manager: KnowledgeManager | None = None


def _get_manager() -> KnowledgeManager:
    global _manager
    if _manager is None:
        _manager = _build_manager()
    return _manager


# ── FastMCP 声明 / FastMCP app ──
# 创建 FastMCP 应用实例（stdio transport）
mcp = FastMCP(_MCP_NAME)


@mcp.tool()
def list_topics() -> dict[str, Any]:
    """列出 Studio 知识库的全部主题（topic）。

    返回主题列表，每个主题含：topic（短 slug，search 工具要用）、name（显示名）、
    description（描述）、chunks_in_collection（chunk 总数）。主项目可据此决定 search
    时传入哪个 topic_slug。
    """
    # 取进程级 manager 单例
    kb = _get_manager()
    topics = kb.list_topics(include_archived=False)
    return {
        "total": len(topics),
        "topics": [
            {
                "topic": t.get("topic"),
                "name": t.get("name"),
                "description": t.get("description"),
                "chunks_in_collection": t.get("chunks_in_collection", 0),
                "status": t.get("status"),
            }
            for t in topics
        ],
    }


@mcp.tool()
def search(
    topic_slug: str,
    query: str,
    top_k: int = 5,
    min_score: float = 0.0,
) -> dict[str, Any]:
    """在指定主题的知识库里做混合语义检索（BGE-M3 dense + BM25 → CrossEncoder 重排）。

    参数:
        topic_slug: 主题 ID（先调 list_topics() 拿可用的 slug，如 genshin / world_of_warcraft）
        query: 自然语言查询（如 "索伦的弱点"、"蒙德城的位置"）
        top_k: 返回前 N 条（默认 5）
        min_score: 最小相似度阈值 0~1（默认 0，不过滤）

    返回:
        topic / query / total / hits（每条含 text, score, distance, metadata）
    """
    if not query or not query.strip():
        return {"topic": topic_slug, "query": query, "total": 0, "hits": [], "error": "query 不能为空"}
    # 取 manager 单例并执行混合检索
    kb = _get_manager()
    try:
        # 调用 manager 的混合检索（dense + BM25 + 重排）
        hits = kb.search_with_meta(
            topic_slug,
            query.strip(),
            top_k=max(1, int(top_k)),
            min_score=float(min_score),
        )
    except Exception as e:  # noqa: BLE001
        _log.exception("[mcp] search failed topic=%s", topic_slug)
        return {"topic": topic_slug, "query": query, "total": 0, "hits": [], "error": f"{type(e).__name__}: {e}"}

    return {
        "topic": topic_slug,
        "query": query,
        "total": len(hits),
        "hits": [
            {
                "text": h.get("text", ""),
                "score": float(h.get("score_cosine_sim", 0.0)),
                "distance": h.get("distance"),
                "metadata": dict(h.get("metadata") or {}),
            }
            for h in hits
        ],
    }


def main() -> None:
    """MCP server 入口（stdio transport）."""
    # 配置日志格式
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    _log.info("[mcp] starting %s (stdio)", _MCP_NAME)
    # FastMCP 默认 stdio；run() 阻塞直到客户端断开
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
