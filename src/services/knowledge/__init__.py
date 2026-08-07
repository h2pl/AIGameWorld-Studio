"""Knowledge base module — 知识库 RAG 服务（按阶段拆分：ingest / index / retrieve）.

对外统一入口：
    - KnowledgeManager  (高层编排，CLI / API 直接调用)

内部按 RAG 阶段组织为子包：
    - ingest.  读取 → 富化 → 切块/嵌入/双写（reader / enrichers / pipeline）
    - index.   向量存储适配（LlamaIndex ↔ Qdrant / SQLite）
    - retrieve. 混合检索 + 重排（search_engine）
"""

# 对外导出知识库编排入口
from .manager import KnowledgeManager

__all__ = ["KnowledgeManager"]
