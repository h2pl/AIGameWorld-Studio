"""AIGameWorld-Studio repository layer — 领域实体存取 / Data access layer.

对标主项目 backend/src/repository/ 的分层契约：
- repository 层持有 storage 层的裸客户端（或本项目的 SQLiteStore），
  封装各业务实体的 CRUD，是 service 层与存储之间的唯一数据访问边界。
- 按主项目粒度「一实体一 repo 文件」拆分，本文件平铺导出所有 repo 类。
- service 层直接持有多个独立 repo（无聚合门面），与主项目各级 service 一致。
"""

# 平铺导出所有业务实体的 repository 类
# 每个 repo 对应一个领域实体的数据访问（见各 *_repo.py）
from .audit_repo import AuditRepository
from .binding_repo import BindingRepository
from .document_repo import DocumentRepository
from .job_repo import JobRepository
from .topic_repo import TopicRepository

__all__ = [
    "DocumentRepository",
    "TopicRepository",
    "BindingRepository",
    "JobRepository",
    "AuditRepository",
]
