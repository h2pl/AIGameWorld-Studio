"""Crawler API schemas — Pydantic 请求/响应模型。"""

# ---- 导入依赖 ----
from __future__ import annotations
# 启用类型注解的前向引用支持

from typing import Any
# 导入任意类型注解

from pydantic import BaseModel, Field
# 导入 Pydantic 基础模型和字段定义


# === 请求 / Request Models ===

class SearchRequest(BaseModel):
    # 必填：搜索关键词，长度1-500
    query: str = Field(..., min_length=1, max_length=500, description="搜索关键词")
    # 返回最大结果数，默认10，范围1-30
    max_results: int = Field(10, ge=1, le=30)


class CrawlUrlsRequest(BaseModel):
    # 必填：目标主题ID，长度至少1
    topic_id: str = Field(..., min_length=1, description="目标主题 topic_id")
    # 必填：待抓取URL列表，至少1条
    urls: list[str] = Field(..., min_length=1, description="要抓取的 URL 列表")
    # promote时的子目录，默认documents
    source_type: str = Field("documents", description="promote 时落到 knowledge/{source_type}/")


class CrawlSearchRequest(BaseModel):
    # 必填：目标主题ID
    topic_id: str = Field(..., min_length=1)
    # 必填：搜索关键词，长度1-500
    query: str = Field(..., min_length=1, max_length=500)
    # 抓取搜索结果数，默认5，范围1-20
    max_results: int = Field(5, ge=1, le=20)
    # promote时的子目录，默认documents
    source_type: str = Field("documents")


class PromoteRequest(BaseModel):
    # 指定要promote的条目ID，None=全部
    item_ids: list[str] | None = Field(None, description="指定提升的 item id 列表；None=全部")

# === 请求模型分组结束 ===


# === 响应 / Response Models ===

class SearchResultItem(BaseModel):
    # 搜索结果标题
    title: str
    # 搜索结果链接
    url: str
    # 搜索结果摘要片段
    snippet: str


class SearchResponse(BaseModel):
    # 用户原始查询关键词
    query: str
    # 搜索结果总数
    total: int
    # 搜索结果条目列表
    results: list[SearchResultItem]


class JobCreatedResponse(BaseModel):
    # 操作是否成功，默认True
    ok: bool = True
    # 创建的任务ID
    job_id: str
    # 任务模式（urls/search等）
    mode: str
    # 任务总条目数
    total_items: int


class JobItemOut(BaseModel):
    # 子任务/条目ID
    id: str
    # 原始抓取URL
    url: str
    # 页面标题，可选
    title: str | None = None
    # 内容类型（markdown/html/pdf等），可选
    content_type: str | None = None
    # 暂存区文件路径，可选
    file_path: str | None = None
    # 文件大小（字节），默认0
    file_size: int = 0
    # 文件SHA256哈希，可选
    sha256: str | None = None
    # 抓取状态
    status: str
    # 错误信息（失败时）
    error_msg: str | None = None
    # 内容预览文本，默认空
    preview: str = ""

    class Config:
        # Pydantic配置类
        from_attributes = True
        # 支持从ORM/字典属性自动映射


class JobOut(BaseModel):
    # 主任务ID
    id: str
    # 所属主题ID
    topic_id: str
    # 搜索关键词（search模式下），可选
    query: str | None = None
    # 任务模式（urls/search）
    mode: str
    # 任务状态
    status: str
    # promote时的source_type
    source_type: str
    # 总条目数
    total_items: int
    # 已完成条目数
    done_items: int
    # 暂存区目录路径
    staging_dir: str
    # 创建者标识
    created_by: str
    # 创建时间戳
    created_at: int
    # 开始时间戳，可选
    started_at: int | None = None
    # 结束时间戳，可选
    finished_at: int | None = None
    # 错误信息，可选
    error_msg: str | None = None
    # 子条目列表，默认空
    items: list[JobItemOut] = []

    class Config:
        # Pydantic配置类
        from_attributes = True
        # 支持从ORM/字典属性自动映射


class JobListResponse(BaseModel):
    # 任务总数
    total: int
    # 任务列表
    jobs: list[JobOut]


class PromoteResponse(BaseModel):
    # promote操作是否成功
    ok: bool
    # 关联任务ID
    job_id: str
    # 所属主题ID，可选
    topic_id: str | None = None
    # 成功promote的文件数，默认0
    promoted_count: int = 0
    # 跳过的文件数，默认0
    skipped_count: int = 0
    # 成功promote的条目详情列表
    promoted: list[dict[str, Any]] = []
    # 跳过的条目详情列表
    skipped: list[dict[str, Any]] = []


class DiscardResponse(BaseModel):
    # discard操作是否成功
    ok: bool
    # 关联任务ID
    job_id: str
    # 暂存区目录是否已成功删除
    staging_removed: bool

# === 响应模型分组结束 ===
