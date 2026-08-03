"""文档列表 / 删除路由 / Document list & soft delete.

- list_documents：分页返回该主题的所有 kb_document（默认不包含软删）
- delete_document：软删单个文档，同步在向量库中按 chunk_id 删除
"""

# ---- 导入依赖 ----
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

# ---- 子模块导入 ----
# 复用 schemas（响应 DTO 集中定义）
from . import schemas

# 复用 deps：主题 id 校验 + manager 依赖注入
from .deps import get_knowledge_manager, require_topic

# ---- 初始化路由 ----
# 所有路由挂在同一个 APIRouter 上，由 kb_router.include_router(本文件router) 统一加载
router = APIRouter()


# ---- 路由: 文档列表 ----
@router.get(
    "/{topic}/documents",
    response_model=schemas.DocumentListResponse,
    summary="文档列表",
    description="返回该主题下所有 kb_document 行（tags_json 解析成 tags list，含 chunk_count 摘要）。",
)
def list_documents(
    topic: str = Depends(require_topic),
    kb=Depends(get_knowledge_manager),
    limit: int = 200,
    # 分页上限：默认 200，UI 侧够用；保护查询避免一次拉太多
    offset: int = 0,
    # 分页偏移：配合 limit 做简单翻页
    include_deleted: bool = False,
    # 默认不返回已软删的文档（前端用垃圾箱视图时才传 True）
) -> schemas.DocumentListResponse:
    # 1. 业务查询：manager 负责把 SQL 结果里的 tags_json 解析成 list
    rows = kb.document_list(topic, limit=limit, offset=offset, include_deleted=include_deleted)
    # 2. DTO 转换：Pydantic 做字段映射 + 序列化（from_attributes=True 兼容 dict 输入）
    docs = [schemas.DocumentListItem(**d) for d in rows]
    # 3. 返回组装好的响应
    return schemas.DocumentListResponse(topic=topic, total=len(docs), documents=docs)


# ---- 路由: 软删单个文档 ----
@router.delete(
    "/{topic}/documents/{doc_id}",
    response_model=schemas.DocumentDeleteResponse,
    summary="软删单个文档",
    description=(
        "1) kb_document.status='deleted' + SQLite 级联删 kb_chunk/kb_document_tag；2) Qdrant 按 chunk_id 批量删。"
    ),
)
def delete_document(
    doc_id: str,
    topic: str = Depends(require_topic),
    kb=Depends(get_knowledge_manager),
) -> schemas.DocumentDeleteResponse:
    # 1. 轻量参数校验：id 至少 5 个字符（我们用 uuid/ulid，都比这个长），避免手误输短 id 删错
    if not doc_id or len(doc_id) < 5:
        raise HTTPException(400, detail="doc_id 格式不正确")
    # 2. 业务执行：manager 里做双写 — 先 SQLite 软删（事务内），再删向量库（失败会返回 ok=False）
    r = kb.document_delete(topic, doc_id)
    # 3. 结果校验：删除失败（通常是向量库侧连接问题），500 + error 让前端提示用户重试
    if not r.get("ok"):
        raise HTTPException(500, detail=r.get("error") or "delete failed")
    # 4. DTO 转换返回
    return schemas.DocumentDeleteResponse(**r)
