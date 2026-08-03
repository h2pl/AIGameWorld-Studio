"""Knowledge base module — document ingestion, indexing, and retrieval.

对外统一使用：
    - KnowledgeManager  (高层编排，CLI / 业务代码直接调用)
    - KnowledgeReader   (多模态文件读取)
    - KnowledgePipeline (Reader → Splitter → Embedding → Qdrant 入库)
    - KnowledgeRetriever(语义检索)
    - MetadataEnricher  (可插拔的元数据增强器协议)
"""

# 对外统一导出（供 CLI / API 使用）
# 分层：Manager(编排) → Pipeline(入库) / Retriever(检索) → Reader(读文件)

# 高层编排类：CLI / 业务代码入口（封装了 Pipeline + Retriever + SQLite 元数据）
# 可插拔元数据增强器：主题特定的业务元数据扩展
from .enrichers import ChronicleVolumeEnricher, MetadataEnricher, get_enrichers_for_topic
from .manager import KnowledgeManager

# 入库流水线：Reader → Splitter → Embedding → 向量库 + SQLite 双写
from .pipeline import KnowledgePipeline

# 多模态文件读取：Markdown / PDF / 图片(OCR) / 视频(抽帧OCR)
from .reader import KnowledgeReader

# 语义检索：向量相似度 + metadata 过滤
from .retriever import KnowledgeRetriever

# __all__ 显式导出列表
__all__ = [
    "KnowledgeManager",
    "KnowledgePipeline",
    "KnowledgeReader",
    "KnowledgeRetriever",
    "MetadataEnricher",
    "ChronicleVolumeEnricher",
    "get_enrichers_for_topic",
]
