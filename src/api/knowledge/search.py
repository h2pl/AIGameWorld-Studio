"""搜索 + 手动触发索引路由 / Search & manual index trigger."""

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


# ---- 路由: 语义检索 ----
@router.post(
    "/{topic_id}/search",
    response_model=schemas.SearchResponse,
    summary="语义检索",
    description="用 BGE-M3 embedding 在 Chroma 里做向量检索，返回 score+metadata，自动写审计。",
)
def search(
    body: schemas.SearchRequest,
    # 请求体：包含查询文本、top_k等参数
    topic_id: str = Depends(require_topic_id),
    # 从路径参数获取并校验主题ID
    kb=Depends(get_knowledge_manager),
    # 注入知识库管理器实例
) -> schemas.SearchResponse:
    # 返回类型注解
    # 1. 参数校验
    if not body.query.strip():
        # 检查查询文本去除空白后是否为空
        raise HTTPException(400, detail="query 不能为空")
        # 抛出400错误：查询不能为空

    # 2. 业务查询：调用管理器执行带元数据的向量检索
    hits = kb.search_with_meta(
        topic_id,
        # 主题ID
        body.query.strip(),
        # 去除首尾空白的查询文本
        top_k=body.top_k,
        # 返回结果条数
        min_score=body.min_score,
        # 最小相似度阈值
        filters=body.filters,
        # 元数据过滤条件
    )

    # 3. DTO 转换返回：将原始检索结果转换为SearchHit模型列表
    search_hits = [
        schemas.SearchHit(
            # 构造SearchHit对象
            text=h.get("text", ""),
            # 取文本字段，默认空字符串
            score=float(h.get("score_cosine_sim", 0.0)),
            # 取余弦相似度并转float，默认0
            distance=h.get("distance"),
            # 取向量距离（可为None）
            metadata=dict(h.get("metadata") or {}),
            # 取元数据字典，空则转空dict
        )
        for h in hits
        # 遍历每条检索命中记录
    ]

    # 组装并返回检索响应
    return schemas.SearchResponse(
        topic_id=topic_id,
        # 主题ID
        query=body.query,
        # 原始查询文本
        top_k=body.top_k,
        # 请求的top_k
        total=len(search_hits),
        # 实际命中总数
        hits=search_hits,
        # 命中结果列表
    )


# ---- 路由: 触发索引（同步） ----
@router.post(
    "/{topic_id}/index",
    response_model=schemas.IndexResponse,
    summary="触发索引（同步）",
    description="立刻跑 ``base_dir/knowledge/`` 的索引流程（incremental 默认，force=True 全量重建）。同步阻塞 — UI 调用时请用长超时或以后改成异步任务。",
)
def index_now(
    body: schemas.IndexRequest,
    # 请求体：是否强制重建
    topic_id: str = Depends(require_topic_id),
    # 从路径参数获取并校验主题ID
    kb=Depends(get_knowledge_manager),
    # 注入知识库管理器实例
) -> schemas.IndexResponse:
    # 返回类型注解
    # 1. 业务执行：调用管理器执行索引，捕获异常
    try:
        # 捕获索引执行过程中的异常
        r = kb.index(topic_id, force=body.force)
        # 调用管理器执行索引（force控制增量/全量）
    except Exception as exc:
        # 捕获任意异常
        # 异常情况下返回失败响应
        return schemas.IndexResponse(
            ok=False,
            # 标记失败
            topic_id=topic_id,
            # 主题ID
            total_chunks=0,
            # 分块数置0
            error=f"{type(exc).__name__}: {exc}",
            # 拼接异常类型名称与消息
        )

    # 2. 检查业务层返回是否失败
    if not r.get("ok"):
        # 管理器返回结果中ok为假的情况
        # 返回业务层面失败的响应
        return schemas.IndexResponse(
            ok=False,
            # 标记失败
            topic_id=topic_id,
            # 主题ID
            total_chunks=int(r.get("total_chunks", 0)),
            # 取结果中的分块总数，默认0
            error=r.get("error") or "unknown error",
            # 取错误消息，无则用默认文案
        )

    # 3. DTO 转换返回：取indexed字段中的索引详情字典
    idx = r.get("indexed") or {}
    # 取indexed字段中的索引详情字典，空则用空dict

    # 组装并返回成功响应
    return schemas.IndexResponse(
        ok=True,
        # 标记成功
        topic_id=topic_id,
        # 主题ID
        total_chunks=int(r.get("total_chunks", 0)),
        # 索引后的总分块数
        files=int(idx.get("files", 0)),
        # 本次处理的文件数
        skipped_files=int(idx.get("skipped_files", 0)),
        # 跳过的文件数（无变更）
        new_files=int(idx.get("new_files", 0)),
        # 新增处理的文件数
        chunks=int(idx.get("chunks", 0)),
        # 本次生成的分块数
        job_id=idx.get("job_id") or None,
        # 关联任务ID，无则为None
    )
