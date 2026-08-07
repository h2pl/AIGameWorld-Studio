"""Knowledge ingest 子包 — RAG 阶段一：读取 → 富化 → 切块/嵌入/双写.

- reader:      多模态文件加载（Markdown / PDF / 图片 OCR / 视频抽帧）
- enrichers:   可插拔的元数据增强器（主题特定业务元数据）
- pipeline:    Reader → Splitter → Embedding → 向量库 + SQLite 双写编排
"""

# 导出 ingest 阶段的核心组件
from .enrichers import ChronicleVolumeEnricher, MetadataEnricher, get_enrichers_for_topic
from .pipeline import KnowledgePipeline
from .reader import KnowledgeReader

__all__ = [
    "KnowledgeReader",
    "KnowledgePipeline",
    "MetadataEnricher",
    "ChronicleVolumeEnricher",
    "get_enrichers_for_topic",
]
