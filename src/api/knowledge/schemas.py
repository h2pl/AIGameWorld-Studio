"""知识库 API Schemas / Knowledge API Pydantic DTOs."""

# ---- 导入依赖 ----
from __future__ import annotations

# 启用类型注解的前向引用支持
from typing import Any

# 导入任意类型注解
from pydantic import BaseModel, Field

# 导入 Pydantic 基础模型和字段定义


# === Topic Registry / 主题注册相关模型 ===


class TopicCreateRequest(BaseModel):
    """创建/更新主题请求."""

    # 主题唯一标识（短 slug），必填，不能为空
    topic: str = Field(..., min_length=1, description="主题唯一标识（短 slug）")
    # 选填：主题显示名称，默认用 topic
    name: str | None = Field(default=None, description="主题显示名称（默认用 topic）")
    # 选填：主题文字描述
    description: str | None = Field(default=None, description="主题描述")
    # 标签列表，默认空数组
    tags: list[str] = Field(default_factory=list, description="主题标签列表")
    # 主题状态，默认激活（active / archived）
    status: str = Field(default="active", description="状态：active / archived")


class TopicResponse(BaseModel):
    """主题详情响应."""

    # 主题记录 UUID
    id: str = Field(..., description="主题记录 UUID")
    # 主题唯一ID
    topic: str = Field(..., description="主题唯一标识")
    # 主题展示名
    name: str = Field(..., description="主题显示名称")
    # 主题描述文本
    description: str | None = Field(default=None, description="主题描述")
    # 主题标签数组
    tags: list[str] = Field(default_factory=list, description="主题标签列表")
    # 当前状态（active/archived）
    status: str = Field(default="active", description="状态")
    # 该主题向量分块总数
    chunks_in_collection: int = Field(default=0, description="当前主题下的 chunk 总数")
    # 创建时间（yyyy-MM-dd HH:mm:ss 格式字符串）
    created_at: str | None = Field(default=None, description="创建时间（yyyy-MM-dd HH:mm:ss）")
    # 更新时间（yyyy-MM-dd HH:mm:ss 格式字符串）
    updated_at: str | None = Field(default=None, description="更新时间（yyyy-MM-dd HH:mm:ss）")
    # 扩展字段（任意 JSON）
    ext_json: dict[str, Any] = Field(default_factory=dict, description="扩展字段（任意 JSON）")


class TopicListResponse(BaseModel):
    """主题列表响应."""

    # 主题总数
    total: int
    # 主题详情列表
    topics: list[TopicResponse]


# === Topic 分组结束 ===


# === World-Topic Binding / World包与主题绑定 ===


class BindingRequest(BaseModel):
    """绑定 world 到主题请求."""

    # 必填：World包ID（如 mordor）
    world_id: str = Field(..., description="world pack id，如 mordor")
    # 必填：目标主题ID
    topic: str = Field(..., description="主题 id")
    # 绑定优先级，数字越大优先级越高
    priority: int = Field(default=0, description="绑定优先级（数值越大越优先）")


class BindingResponse(BaseModel):
    """绑定操作响应."""

    # 操作是否成功
    ok: bool
    # 操作涉及的World包ID
    world_id: str
    # 操作涉及的主题ID
    topic: str
    # 当前绑定优先级，默认0
    priority: int = 0
    # 解绑场景下删除的行数
    removed_rows: int | None = Field(default=None, description="解绑时删除的行数")


class BindingListResponse(BaseModel):
    """绑定列表响应."""

    # 绑定记录总数
    total: int
    # 绑定记录列表（字典格式）
    bindings: list[dict]


# === Binding 分组结束 ===


# === Search / 语义检索相关模型 ===


class SearchHit(BaseModel):
    # 匹配到的分块文本内容
    text: str
    # 余弦相似度分数，范围0-1
    score: float = Field(description="cosine similarity 0~1")
    # 向量距离（可选）
    distance: float | None = None
    # 分块稳定外键（== Qdrant point id == kb_chunk.id），下游可精确回查
    chunk_id: str = Field(default="", description="chunk 稳定外键，用于溯源/引用")
    # 规范化出处（幻觉规避：LLM 生成时可精确引用）
    citation: dict[str, Any] = Field(
        default_factory=dict,
        description="出处：document_id/file_name/title/page_number/chunk_index 等",
    )
    # 分块的附属元数据
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    # 必填：用户查询文本，不能为空
    query: str = Field(..., min_length=1, description="自然语言查询")
    # 返回结果条数，默认5，范围1-100
    top_k: int = Field(default=5, ge=1, le=100)
    # 最小相似度阈值，默认0，范围0-1（默认0=只取 top_k，不按分数过滤）
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    # 元数据过滤条件（Qdrant where 条件）
    filters: dict[str, Any] | None = Field(default=None, description="Qdrant where 条件")
    # 是否启用 Query 改写（multi_query 扩展召回），默认关闭（零额外 LLM 依赖）
    query_rewrite: bool = Field(default=False, description="启用 Query 改写提升召回，默认关闭")


class SearchResponse(BaseModel):
    # 查询所属的主题ID
    topic: str
    # 用户原始查询文本
    query: str
    # 请求的返回条数
    top_k: int
    # 实际匹配到的结果总数，默认0
    total: int = 0
    # 匹配命中的结果列表
    hits: list[SearchHit]


# === Search 分组结束 ===


# === Documents / 文档管理相关模型 ===


class DocumentListItem(BaseModel):
    # 文档唯一ID
    id: str
    # 文档所属主题ID
    topic: str
    # 文档标题
    title: str
    # 来源类型（如 documents、web 等）
    source_type: str
    # 内容类型（如 markdown、pdf 等）
    content_type: str
    # 文件名
    file_name: str
    # 文件存储路径
    file_path: str
    # 文件大小（字节）
    file_size: int
    # 文档版本号
    version: int
    # 文档状态
    status: str
    # 文档标签列表，默认空
    tags: list[str] = Field(default_factory=list)
    # 该文档切分出的分块数，默认0
    chunk_count: int = 0
    # 创建时间戳
    created_at: str | None = None
    # 更新时间戳
    updated_at: str | None = None
    # 扩展字段（任意 JSON）
    ext_json: dict[str, Any] = Field(default_factory=dict, description="扩展字段（任意 JSON）")


class DocumentListResponse(BaseModel):
    # 所属主题ID
    topic: str
    # 文档总数
    total: int
    # 文档列表
    documents: list[DocumentListItem]


class DocumentDeleteResponse(BaseModel):
    # 删除是否成功
    ok: bool
    # 被软删除的文档数量
    soft_deleted: int
    # 被删除的分块ID数量
    deleted_chunk_ids_count: int


# === Documents 分组结束 ===


# === Index / Trigger reindex / 索引触发相关模型 ===


class IndexRequest(BaseModel):
    # 是否强制全量重建索引，默认False（增量）
    force: bool = False


class IndexResponse(BaseModel):
    # 索引操作是否成功
    ok: bool
    # 所属主题ID
    topic: str
    # 索引完成后分块总数
    total_chunks: int
    # 本次处理的文件数，默认0
    files: int = 0
    # 跳过的文件数（无变更），默认0
    skipped_files: int = 0
    # 新增处理的文件数，默认0
    new_files: int = 0
    # 本次新增/更新的分块数，默认0
    chunks: int = 0
    # 关联任务ID（可选）
    job_id: str | None = None
    # 任务状态（异步索引：pending/running/done/failed）
    status: str | None = None
    # 错误信息（失败时填充）
    error: str | None = None


# === Index 分组结束 ===


# === Jobs / 索引任务相关模型 ===


class JobItem(BaseModel):
    # 任务唯一ID
    id: str
    # 任务模式（incremental/full 等）
    mode: str
    # 任务状态（pending/running/done/failed）
    status: str
    # 任务进度百分比，默认0
    progress: int = 0
    # 待处理文件总数，默认0
    file_total: int = 0
    # 已处理完成的文件数，默认0
    file_done: int = 0
    # 分块总数，默认0
    chunk_total: int = 0
    # 错误信息（失败时）
    error_msg: str | None = None
    # 任务开始时间戳
    started_at: str | None = None
    # 任务结束时间戳
    finished_at: str | None = None
    # 任务创建时间戳
    created_at: str | None = None
    # 扩展字段（任意 JSON）
    ext_json: dict[str, Any] = Field(default_factory=dict, description="扩展字段（任意 JSON）")


class JobListResponse(BaseModel):
    # 所属主题ID
    topic: str
    # 任务列表
    jobs: list[JobItem]


# === Jobs 分组结束 ===


# === Audit / 审计日志相关模型 ===


class AuditItem(BaseModel):
    # 审计记录ID
    id: str | None = None
    # 记录创建时间戳
    created_at: str | None = None
    # 操作类型（index_start/retrieve 等）
    op: str = ""
    # 操作者标识
    actor: str = ""
    # 涉及的主题ID（DB 列名，存储 slug）
    topic_id: str = ""
    # 关联任务ID
    job_id: str | None = None
    # 关联文档ID
    document_id: str | None = None
    # 查询文本（检索场景）
    query_text: str | None = None
    # 过滤条件JSON字符串
    filters_json: str | None = None
    # 结果JSON字符串
    result_json: str | None = None
    # 错误信息
    error_msg: str | None = None


class AuditListResponse(BaseModel):
    # 所属主题ID
    topic: str
    # 审计记录列表
    audit: list[AuditItem]


# === Audit 分组结束 ===


# === Stats / 统计与清空相关模型 ===


class StatsResponse(BaseModel):
    # 主题ID
    topic: str
    # Qdrant collection 名称
    collection: str
    # 主题目录路径
    topic_dir: str
    # 主题 knowledge 子目录路径
    topic_knowledge_dir: str
    # knowledge 目录是否存在，默认不存在
    topic_knowledge_dir_exists: bool = False
    # 向量库中分块总数，默认0
    total_chunks: int = 0
    # 文档总数，默认0
    documents: int = 0
    # 元数据中记录的分块数，默认0
    chunks_in_meta: int = 0
    # 按 source_type 分组的分块数统计
    by_source_type: dict[str, int] = Field(default_factory=dict)
    # 成功完成的索引任务数，默认0
    successful_jobs: int = 0


class ClearResponse(BaseModel):
    # 清空操作是否成功
    ok: bool
    # 主题ID
    topic: str
    # Qdrant collection 名称
    collection: str
    # collection 是否已清空
    cleared: bool
    # 被软删除的文档数
    soft_deleted_documents: int
    # 清空后剩余的分块数（应为0）
    total_chunks: int


# === Stats 分组结束 ===


# === Ingest Files / 直接导入本地文件（一步到位） ===


class IngestFilesRequest(BaseModel):
    """直接导入本地文件请求 — 传本地路径，一步完成读取+切块+嵌入+写入."""

    # 本地文件绝对路径列表
    file_paths: list[str] = Field(..., min_length=1, description="本地文件绝对路径列表（支持 pdf/md/txt）")
    # 每块字符数，默认 800
    chunk_size: int = Field(default=800, ge=100, le=4000, description="每块字符数")
    # 重叠字符数，默认 120
    chunk_overlap: int = Field(default=120, ge=0, le=1000, description="重叠字符数")


class IngestFilesResponse(BaseModel):
    """直接导入文件响应."""

    # 操作是否成功
    ok: bool
    # 所属主题ID
    topic: str
    # 导入的文档ID列表
    doc_ids: list[str] = Field(default_factory=list)
    # 新增分块总数
    chunks: int = 0
    # 耗时（秒）
    elapsed: float = 0.0
    # 错误信息
    error: str | None = None


# === Ingest Files 分组结束 ===
