"""搜索 + 手动触发索引路由 / Search & manual index trigger."""

# ---- 导入依赖 ----
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from . import schemas
from .deps import get_knowledge_manager, require_topic_id

# ---- 路由初始化 ----
router = APIRouter()


# ---- 路由: 语义检索 ----
@router.post(
    "/{topic_id}/search",
    response_model=schemas.SearchResponse,
    summary="语义检索",
    description="用 BGE-M3 embedding 在 Qdrant 里做 dense 检索，返回 score+metadata，自动写审计。",
)
def search(
    body: schemas.SearchRequest,
    topic_id: str = Depends(require_topic_id),
    kb=Depends(get_knowledge_manager),
) -> schemas.SearchResponse:
    # 1. 参数校验：query 不能为空
    if not body.query.strip():
        raise HTTPException(400, detail="query 不能为空")

    # 2. 业务执行：调用管理器做语义检索（含元数据 + 审计）
    hits = kb.search_with_meta(
        topic_id,
        body.query.strip(),
        top_k=body.top_k,
        min_score=body.min_score,
        filters=body.filters,
    )

    # 3. DTO 转换：dict 列表 → SearchHit 列表
    search_hits = [
        schemas.SearchHit(
            text=h.get("text", ""),
            score=float(h.get("score_cosine_sim", 0.0)),
            distance=h.get("distance"),
            metadata=dict(h.get("metadata") or {}),
        )
        for h in hits
    ]

    # 4. 返回组装好的响应
    return schemas.SearchResponse(
        topic_id=topic_id,
        query=body.query,
        top_k=body.top_k,
        total=len(search_hits),
        hits=search_hits,
    )


# ---- 路由: 触发索引（异步） ----
@router.post(
    "/{topic_id}/index",
    response_model=schemas.IndexResponse,
    status_code=202,
    summary="触发索引（异步）",
    description=(
        "异步触发索引：立刻返回 202 + job_id，后台执行 index_directory。"
        "前端轮询 GET /{topic_id}/jobs 查看进度。"
        "force=True 全量重建，默认增量。"
    ),
)
def index_now(
    body: schemas.IndexRequest,
    background_tasks: BackgroundTasks,
    topic_id: str = Depends(require_topic_id),
    kb=Depends(get_knowledge_manager),
) -> schemas.IndexResponse:
    # 1. 创建异步任务记录（返回 job_id 供前端轮询）
    r = kb.index_async(topic_id, force=body.force)
    job_id = r.get("job_id") or ""

    # 2. 注册后台执行：实际索引逻辑在 _run_index_job 中
    background_tasks.add_task(kb._run_index_job, topic_id, job_id, force=body.force)

    # 3. 立即返回 202（前端用 job_id 轮询 GET /{topic_id}/jobs）
    return schemas.IndexResponse(
        ok=True,
        topic_id=topic_id,
        total_chunks=0,
        job_id=job_id,
        error=None,
    )
