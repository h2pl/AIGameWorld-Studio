"""AIGameWorld-Studio storage layer — 裸存储客户端，零业务.

对标主项目 backend/src/storage/ 的分层契约：
- storage 层只负责与外部存储系统的底层通信（向量库 / 缓存 / 文件系统 等），
  不包含任何业务概念（文档 / 主题 / chunk 等）。
- 业务实体的数据访问请在 repository/ 层实现（repository 持有本层客户端）。
"""

# 对外暴露的存储客户端实现
from .file_io import FileIO
from .qdrant_client import QdrantClient

__all__ = ["FileIO", "QdrantClient"]
