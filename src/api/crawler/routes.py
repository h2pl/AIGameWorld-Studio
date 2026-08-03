"""Crawler API 路由 / Crawler API routes.

挂在 ``/api/crawler`` 前缀下。所有路由同步执行（爬虫本身是阻塞 IO）。
"""

# ---- 导入依赖 ----
from __future__ import annotations

# 启用类型注解的前向引用支持
from fastapi import APIRouter, Depends, HTTPException, Query

# 导入FastAPI路由、依赖、HTTP异常、查询参数
from ...services.crawler import CrawlerService

# 导入爬虫服务类
from . import schemas

# 导入同模块的Pydantic数据模型
from .deps import get_crawler_service

# 导入依赖：爬虫服务获取、主题ID校验

# ---- 初始化路由 ----
router = APIRouter()
# 创建FastAPI子路由实例


# ---- 路由分组: 搜索 / Search Endpoints ----


@router.post(
    "/search",
    response_model=schemas.SearchResponse,
    summary="全网搜索（DuckDuckGo）",
    description="输入关键词，返回 DuckDuckGo 搜索结果（标题+URL+摘要）。不抓取，仅返回结果列表。",
)
def search(
    req: schemas.SearchRequest,
    # 请求体：搜索关键词与结果条数
    svc: CrawlerService = Depends(get_crawler_service),
    # 注入爬虫服务实例
) -> schemas.SearchResponse:
    # 返回类型注解
    # 1. 业务查询：调用服务执行DuckDuckGo搜索
    results = svc.search(req.query, max_results=req.max_results)
    # 调用服务执行DuckDuckGo搜索

    # 2. DTO 转换返回：组装搜索响应
    return schemas.SearchResponse(
        query=req.query,
        # 原始查询关键词
        total=len(results),
        # 实际返回结果数
        results=[schemas.SearchResultItem(**r) for r in results],
        # 转换为SearchResultItem列表
    )


# ---- 路由分组: 抓取任务 / Crawl Job Endpoints ----


@router.post(
    "/jobs/urls",
    response_model=schemas.JobCreatedResponse,
    summary="贴 URL 抓取 → 暂存区",
    description="同步抓取给定 URL 列表，HTML 转 Markdown、PDF 原样保存，落到 .staging/crawler/{job_id}/。",
)
def crawl_urls(
    req: schemas.CrawlUrlsRequest,
    # 请求体：主题ID、URL列表、source_type
    svc: CrawlerService = Depends(get_crawler_service),
    # 注入爬虫服务实例
) -> schemas.JobCreatedResponse:
    # 返回类型注解
    # 1. 业务执行：调用服务执行URL列表抓取并获取任务ID
    job_id = svc.crawl_urls(
        req.topic_id,
        # 目标主题ID
        req.urls,
        # 待抓取URL列表
        source_type=req.source_type,
        # promote时使用的source_type
    )

    # 2. 补充查询：再次查询该任务详情以获取总条目数
    job = svc.get_job(job_id)
    # 再次查询该任务详情以获取总条目数

    # 3. DTO 转换返回：组装创建响应
    return schemas.JobCreatedResponse(
        job_id=job_id,
        # 新创建的任务ID
        mode="urls",
        # 任务模式：urls（直接贴URL）
        total_items=(job or {}).get("total_items", 0),
        # 取总条目数，失败默认0
    )


@router.post(
    "/jobs/search",
    response_model=schemas.JobCreatedResponse,
    summary="搜索 + 抓取一条龙 → 暂存区",
    description="先用关键词搜索，再把搜索结果 URL 全部抓取到暂存区。一个接口完成「搜+抓」。",
)
def crawl_search(
    req: schemas.CrawlSearchRequest,
    # 请求体：主题ID、关键词、结果数等
    svc: CrawlerService = Depends(get_crawler_service),
    # 注入爬虫服务实例
) -> schemas.JobCreatedResponse:
    # 返回类型注解
    # 1. 业务执行：调用服务执行搜索+抓取流程
    job_id = svc.crawl_search(
        req.topic_id,
        # 目标主题ID
        req.query,
        # 搜索关键词
        max_results=req.max_results,
        # 抓取的搜索结果数
        source_type=req.source_type,
        # promote时使用的source_type
    )

    # 2. 补充查询：再次查询该任务详情以获取总条目数
    job = svc.get_job(job_id)
    # 再次查询该任务详情以获取总条目数

    # 3. DTO 转换返回：组装创建响应
    return schemas.JobCreatedResponse(
        job_id=job_id,
        # 新创建的任务ID
        mode="search",
        # 任务模式：search（搜索+抓取）
        total_items=(job or {}).get("total_items", 0),
        # 取总条目数，失败默认0
    )


# ---- 路由分组: 任务查询 / Job Query Endpoints ----


@router.get(
    "/jobs",
    response_model=schemas.JobListResponse,
    summary="爬虫任务列表",
)
def list_jobs(
    topic_id: str | None = Query(None, description="按主题过滤"),
    # 查询参数：按主题ID过滤，可选
    limit: int = Query(50, ge=1, le=200),
    # 查询参数：每页条数，默认50，范围1-200
    offset: int = Query(0, ge=0),
    # 查询参数：分页偏移，默认0，非负
    svc: CrawlerService = Depends(get_crawler_service),
    # 注入爬虫服务实例
) -> schemas.JobListResponse:
    # 返回类型注解
    # 1. 业务查询：调用服务查询任务列表
    jobs = svc.list_jobs(topic_id=topic_id, limit=limit, offset=offset)
    # 调用服务查询任务列表

    # 列表接口不返回子条目，手动填充空列表
    out = []
    # 初始化输出列表
    for j in jobs:
        # 遍历每条任务记录
        j["items"] = []
        # 手动给每条任务添加空的items列表
        out.append(schemas.JobOut(**j))
        # 构造JobOut对象并加入输出

    # 2. DTO 转换返回：组装任务列表响应
    return schemas.JobListResponse(total=len(out), jobs=out)
    # 组装并返回任务列表响应


@router.get(
    "/jobs/{job_id}",
    response_model=schemas.JobOut,
    summary="任务详情（含 items）",
)
def get_job(
    job_id: str,
    # 路径参数：任务ID
    svc: CrawlerService = Depends(get_crawler_service),
    # 注入爬虫服务实例
) -> schemas.JobOut:
    # 返回类型注解
    # 1. 业务查询：调用服务查询任务详情
    job = svc.get_job(job_id)
    # 调用服务查询任务详情

    # 2. 存在性校验：判断任务是否存在
    if job is None:
        # 判断任务是否存在
        raise HTTPException(404, detail="job not found")
        # 不存在则抛出404

    # 3. DTO 转换返回
    return schemas.JobOut(**job)
    # 存在则解包构造JobOut并返回


@router.get(
    "/jobs/{job_id}/items",
    response_model=list[schemas.JobItemOut],
    summary="暂存区文件列表（含预览）",
)
def list_job_items(
    job_id: str,
    # 路径参数：任务ID
    svc: CrawlerService = Depends(get_crawler_service),
    # 注入爬虫服务实例
) -> list[schemas.JobItemOut]:
    # 返回类型注解
    # 1. 存在性校验：先检查该任务是否存在
    if svc.get_job(job_id) is None:
        # 先检查该任务是否存在
        raise HTTPException(404, detail="job not found")
        # 不存在则抛出404

    # 2. 业务查询：调用服务查询该任务的子条目列表
    items = svc.list_job_items(job_id)
    # 调用服务查询该任务的子条目列表

    # 3. DTO 转换返回
    return [schemas.JobItemOut(**it) for it in items]
    # 转换为JobItemOut列表并返回


# ---- 路由分组: Promote / Discard / 批准与丢弃 ----


@router.post(
    "/jobs/{job_id}/promote",
    response_model=schemas.PromoteResponse,
    summary="批准 → 移到 knowledge/{source_type}/",
    description=(
        "把暂存区文件移动到 knowledge/documents/（或指定 source_type）。"
        "**不触发索引**，需另行调 /api/kb/{topic}/index。"
    ),
)
def promote_job(
    job_id: str,
    # 路径参数：任务ID
    req: schemas.PromoteRequest,
    # 请求体：指定要promote的条目ID（可选）
    svc: CrawlerService = Depends(get_crawler_service),
    # 注入爬虫服务实例
) -> schemas.PromoteResponse:
    # 返回类型注解
    # 1. 业务执行：调用服务执行promote操作
    result = svc.promote_job(job_id, item_ids=req.item_ids)
    # 调用服务执行promote操作

    # 2. 结果校验：判断操作是否失败
    if not result.get("ok"):
        # 判断操作是否失败
        raise HTTPException(400, detail=result.get("error", "promote failed"))
        # 失败则抛出400

    # 3. DTO 转换返回
    return schemas.PromoteResponse(**result)
    # 成功则解包构造响应并返回


@router.delete(
    "/jobs/{job_id}",
    response_model=schemas.DiscardResponse,
    summary="丢弃任务（删暂存文件）",
)
def discard_job(
    job_id: str,
    # 路径参数：任务ID
    svc: CrawlerService = Depends(get_crawler_service),
    # 注入爬虫服务实例
) -> schemas.DiscardResponse:
    # 返回类型注解
    # 1. 业务执行：调用服务执行丢弃操作（删除暂存文件）
    result = svc.discard_job(job_id)
    # 调用服务执行丢弃操作（删除暂存文件）

    # 2. 结果校验：判断操作是否失败
    if not result.get("ok"):
        # 判断操作是否失败
        raise HTTPException(404, detail=result.get("error", "job not found"))
        # 失败则抛出404

    # 3. DTO 转换返回
    return schemas.DiscardResponse(**result)
    # 成功则解包构造响应并返回
