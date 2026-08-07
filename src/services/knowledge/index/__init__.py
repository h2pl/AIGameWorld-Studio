"""Knowledge index 子包 — RAG 阶段二：向量存储适配（LlamaIndex ↔ Qdrant/SQLite）."""

# 导出向量存储工厂与 SQLite 实现
from .vector_store import KBVectorStoreFactory, SQLiteVectorStore

__all__ = ["KBVectorStoreFactory", "SQLiteVectorStore"]
