"""知识库 stats + clear 路由 / Stats & clear endpoints."""

# ---- 导入依赖 ----
from __future__ import annotations

# 启用类型注解的前向引用支持
from fastapi import APIRouter, Depends, HTTPException

# 导入FastAPI路由、依赖、HTTP异常
from . import schemas

# 导入同模块的Pydantic数据模型
from .deps import get_knowledge_manager, require_topic_id

# 导入依赖：知识库管理器、主题ID校验

# ---- 初始化路由 ----
router = APIRouter()
# 创建FastAPI子路由实例


# ---- 路由: 知识库统计 ----
@router.get(
    "/{topic_id}/stats",
    response_model=schemas.StatsResponse,
    summary="知识库统计",
    description="返回 chunk/doc 数/按 source_type 分布/索引成功任务数等。",
)
def kb_stats(
    topic_id: str = Depends(require_topic_id),
    # 从路径参数获取并校验主题ID
    kb=Depends(get_knowledge_manager),
    # 注入知识库管理器实例
) -> schemas.StatsResponse:
    # 返回类型注解
    # 1. 业务查询 + DTO 转换返回
    return schemas.StatsResponse(**kb.stats(topic_id))
    # 调用管理器获取统计并解包构造响应


# ---- 路由: 清空知识库 ----
@router.delete(
    "/{topic_id}",
    response_model=schemas.ClearResponse,
    summary="清空知识库",
    description="Chroma collection 重置 + kb_document 软删（自动级联 kb_chunk/kb_document_tag），并写审计。",
)
def kb_clear(
    topic_id: str = Depends(require_topic_id),
    # 从路径参数获取并校验主题ID
    kb=Depends(get_knowledge_manager),
    # 注入知识库管理器实例
) -> schemas.ClearResponse:
    # 返回类型注解
    # 1. 业务执行：调用管理器执行清空操作
    r = kb.clear(topic_id)
    # 调用管理器执行清空操作并获取结果

    # 2. 结果校验：判断操作是否成功
    if not r.get("ok"):
        # 判断清空操作是否失败
        raise HTTPException(500, detail="clear 失败")
        # 失败则抛出500服务器错误

    # 3. DTO 转换返回
    return schemas.ClearResponse(**r)
    # 成功则解包结果构造并返回响应
