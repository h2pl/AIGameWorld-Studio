"""索引任务 + 审计 列表路由.

本文件包含两条只读路由，给 UI 的「索引进度」和「操作日志」面板提供数据：
- list_jobs：按主题查询最近的索引任务（kb_index_job）
- list_audit：按主题查询最近的操作审计（kb_audit_log）
"""

# ---- 导入依赖 ----
from __future__ import annotations
# 启用类型注解的前向引用支持

from fastapi import APIRouter, Depends
# 导入 FastAPI 路由与依赖注入工具

# ---- 子模块导入 ----
# 导入同模块的 Pydantic 数据模型（响应 DTO 集中定义在 schemas.py 中）
from . import schemas
# 导入依赖：知识库管理器、主题ID校验（由 FastAPI Depends 负责注入实例）
from .deps import get_knowledge_manager, require_topic_id

# ---- 初始化路由 ----
# 创建 FastAPI 子路由实例：所有路由挂到这里，再由 kb_router.include_router 汇总
router = APIRouter()


# ---- 路由分组: 索引任务列表 ----

@router.get(
    "/{topic_id}/jobs",
    response_model=schemas.JobListResponse,
    summary="最近索引任务",
    description="返回最近 N 条 kb_index_job 记录（含 progress / 状态 / 耗时所需字段）。",
)
def list_jobs(
    topic_id: str = Depends(require_topic_id),
    # 从路径参数获取并校验主题 ID（空值直接 400）
    kb=Depends(get_knowledge_manager),
    # 注入知识库管理器实例（请求结束后自动 close）
    limit: int = 50,
    # 查询条数限制，默认 50 条；UI 展示够用，同时保护查询
) -> schemas.JobListResponse:
    # 返回类型注解（Pydantic 自动序列化 + OpenAPI 生成）
    # 1. 业务查询：manager 层封装 SQL，按 created_at 倒序、按 topic_id 过滤
    rows = kb.job_list(topic_id, limit=limit)
    # 2. DTO 转换：每行 dict → Pydantic JobItem（保证字段名一致 + from_attributes=True）
    jobs = [schemas.JobItem(**r) for r in rows]
    # 3. 组装顶层响应：顺带回显 topic_id，方便前端渲染 breadcrumb
    return schemas.JobListResponse(topic_id=topic_id, jobs=jobs)


# ---- 路由分组: 审计日志列表 ----

@router.get(
    "/{topic_id}/audit",
    response_model=schemas.AuditListResponse,
    summary="最近 N 条审计日志",
    description="包含 index_start / index_done / retrieve / clear_collection / doc_delete 等操作。",
)
def list_audit(
    topic_id: str = Depends(require_topic_id),
    # 主题 id 参数校验 + 注入
    kb=Depends(get_knowledge_manager),
    # 注入知识库管理器
    limit: int = 100,
    # 查询条数上限：默认 100，审计日志条数多一点方便查历史
) -> schemas.AuditListResponse:
    # 返回类型：Pydantic AuditListResponse
    # 1. 查询：manager 层封装 SQL（按 created_at DESC，按 topic_id 过滤）
    rows = kb.audit_list(topic_id, limit=limit)
    # 2. DTO 转换：dict list → Pydantic AuditItem list
    items = [schemas.AuditItem(**r) for r in rows]
    # 3. 组装响应：带回 topic_id 方便前端上下文判断
    return schemas.AuditListResponse(topic_id=topic_id, audit=items)
