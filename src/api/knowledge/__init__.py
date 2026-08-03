"""知识库路由包 / Knowledge API router.

在 serve.py 的 FastAPI 实例上 ``include_router(kb_router)`` 即可挂所有路由。
"""

# ---- 导入依赖 ----
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from ...services.knowledge.manager import KnowledgeManager

# ---- 子模块导入 ----
from . import schemas
from .deps import get_knowledge_manager, require_topic_id
from .documents import router as _docs_router
from .jobs import router as _jobs_router
from .search import router as _search_router
from .stats import router as _stats_router

# ---- 顶层路由初始化 ----
kb_router = APIRouter(prefix="/api/kb", tags=["knowledge"])
# 汇总挂载所有子路由（统计/文档/任务/搜索）
kb_router.include_router(_stats_router)
kb_router.include_router(_docs_router)
kb_router.include_router(_jobs_router)
kb_router.include_router(_search_router)

# ---- 常量定义 ----
# 允许上传的 source_type 枚举集合
SOURCE_TYPES = {"lore", "documents", "images", "videos"}


# ---- 路由: 直接导入本地文件（一步到位） ----
@kb_router.post(
    "/{topic_id}/ingest-files",
    response_model=schemas.IngestFilesResponse,
    status_code=202,
    summary="直接导入本地文件（一步到位）",
    description=(
        "传入本地文件路径列表，后台异步执行：读取 → 切块 → BGE-M3 嵌入 → SQLite+Qdrant 双写。\n"
        "立刻返回 202，前端轮询 GET /{topic_id}/jobs 查看进度。\n"
        "这是本地应用的主流程，不需要先 upload 再 index 的两步操作。"
    ),
)
async def ingest_files(
    body: schemas.IngestFilesRequest,
    background_tasks: BackgroundTasks,
    topic_id: str = Depends(require_topic_id),
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> schemas.IngestFilesResponse:
    # 1. 校验：所有文件必须存在
    missing = [p for p in body.file_paths if not Path(p).exists()]
    if missing:
        raise HTTPException(400, detail=f"文件不存在: {missing[:3]}")

    file_paths = [Path(p) for p in body.file_paths]

    # 2. 创建异步任务记录
    r = kb.index_async(topic_id, force=False)
    job_id = r.get("job_id") or ""

    # 3. 注册后台执行（调 ingest_files，一步到位）
    background_tasks.add_task(
        kb._run_ingest_files_job,
        topic_id,
        job_id,
        file_paths,
        chunk_size=body.chunk_size,
        chunk_overlap=body.chunk_overlap,
    )

    return schemas.IngestFilesResponse(
        ok=True,
        topic_id=topic_id,
        doc_ids=[],
        chunks=0,
        elapsed=0.0,
        error=None,
    )


# ---- 路由: 上传文件 ----
@kb_router.post(
    "/{topic_id}/upload",
    summary="上传文件 → 写入 knowledge/{source_type}/ 目录",
    description=(
        "FormData 里放一个 file 字段即可；query 参数里指定 source_type / prefix；\n"
        "保存后返回文件位置，不会立刻索引（由前端决定何时 POST /{topic_id}/index）"
    ),
)
async def upload_file(
    request: Request,
    topic_id: str = Depends(require_topic_id),
    source_type: Annotated[str, Form()] = "documents",
    prefix: Annotated[str, Form()] = "",
    file: UploadFile = File(...),
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> dict[str, Any]:
    # 1. 参数校验：检查 source_type 合法性
    if source_type not in SOURCE_TYPES:
        raise HTTPException(400, detail=f"source_type 必须是 {sorted(SOURCE_TYPES)}")
    # 2. 参数校验：检查 file 是否为空
    if file is None or file.filename is None or file.filename == "":
        raise HTTPException(400, detail="file 字段不能为空")
    # 3. 业务执行：读取文件字节流
    data = await file.read()
    try:
        # 4. DTO 转换返回：调用管理器保存上传字节
        return kb.save_uploaded_bytes(topic_id, data, source_type, file.filename, prefix=prefix)
    except Exception as e:
        # 5. 异常处理：保存失败统一返回 500
        raise HTTPException(500, detail=f"保存失败: {e}")


# ---- 路由: 粘贴文本上传 ----
@kb_router.post(
    "/{topic_id}/upload-text",
    summary="粘贴文本直接保存到 knowledge/{source_type}/ 目录",
    description="Body 里直接放纯文本（默认视为 utf-8 Markdown），query 指定 source_type / file_name / prefix",
)
async def upload_text(
    request: Request,
    topic_id: str = Depends(require_topic_id),
    source_type: str = "documents",
    prefix: str = "",
    file_name: str = "pasted.md",
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> dict[str, Any]:
    # 1. 参数校验：检查 source_type 合法性
    if source_type not in SOURCE_TYPES:
        raise HTTPException(400, detail=f"source_type 必须是 {sorted(SOURCE_TYPES)}")
    # 2. 业务执行：读取请求 body 字节
    body_bytes = await request.body()
    # 3. 参数校验：检查 body 是否为空
    if not body_bytes:
        raise HTTPException(400, detail="请求 body 不能为空")
    # 4. 编码转换：字节流转 utf-8 文本
    text = body_bytes.decode("utf-8")
    # 5. DTO 转换返回：调用管理器保存上传文本
    return kb.save_uploaded_text(topic_id, text, source_type, file_name=file_name, prefix=prefix)


# ---- 路由: 软删单个文档 ----
@kb_router.delete(
    "/{topic_id}/documents/{document_id}",
    summary="软删单个文档（SQLite 标记 deleted + 同步删 Chroma chunks）",
)
async def soft_delete_document(
    document_id: str,
    topic_id: str = Depends(require_topic_id),
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> Any:
    # 1. 参数校验：检查 document_id 是否为空
    if not document_id.strip():
        raise HTTPException(400, detail="document_id 不能为空")
    # 2. 业务执行：调用管理器软删文档
    r = kb.document_delete(topic_id, document_id)
    # 3. 结果校验：判断是否删除失败
    if not r.get("ok"):
        return JSONResponse(status_code=500, content=r)
    # 4. 成功返回
    return r


# ---- 路由分组: 主题注册表 API ----


@kb_router.get(
    "/topics",
    response_model=schemas.TopicListResponse,
    summary="主题列表",
    description="返回所有已注册的主题，含每个主题的 chunk 数量摘要。",
)
def list_topics(
    include_archived: bool = False,
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> schemas.TopicListResponse:
    # 1. 业务查询：调用管理器获取主题列表
    topics = kb.list_topics(include_archived=include_archived)
    # 2. DTO 转换：dict 列表转 Pydantic TopicResponse 列表
    items = [schemas.TopicResponse(**t) for t in topics]
    # 3. 返回组装好的响应
    return schemas.TopicListResponse(total=len(items), topics=items)


@kb_router.get(
    "/topics/{topic_id}",
    response_model=schemas.TopicResponse,
    summary="获取单个主题详情",
    description="按 topic_id 查找主题，找不到返回 404。",
)
def get_topic(
    topic_id: str = Depends(require_topic_id),
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> schemas.TopicResponse:
    # 1. 业务查询：调用管理器获取主题详情
    t = kb.get_topic(topic_id)
    # 2. 存在性校验：找不到返回 404
    if t is None:
        raise HTTPException(404, detail=f"主题 {topic_id} 不存在")
    # 3. DTO 转换返回
    return schemas.TopicResponse(**t)


@kb_router.post(
    "/topics",
    response_model=schemas.TopicResponse,
    summary="创建或更新主题",
    description="topic_id 已存在则更新 name/description/tags/status，不存在则新建。",
)
def create_or_update_topic(
    body: schemas.TopicCreateRequest,
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> schemas.TopicResponse:
    # 1. 业务执行：调用管理器确保主题存在（不存在则创建，存在则更新）
    t = kb.ensure_topic(
        body.topic_id,
        name=body.name,
        description=body.description,
        tags=body.tags,
        status=body.status,
    )
    # 2. DTO 转换返回
    return schemas.TopicResponse(**t)


@kb_router.delete(
    "/topics/{topic_id}",
    summary="删除主题",
    description="删除主题注册表记录（不删除 Chroma collection 和 SQLite 文档数据）。",
)
def delete_topic(
    topic_id: str = Depends(require_topic_id),
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> dict[str, Any]:
    # 1. 业务执行：调用管理器删除主题注册表记录
    r = kb.delete_topic(topic_id)
    # 2. 成功返回：附上 ok 标记 + 原始结果
    return {"ok": True, **r}


# ---- 路由分组: World-Topic 绑定 API ----


@kb_router.get(
    "/bindings",
    response_model=schemas.BindingListResponse,
    summary="绑定列表",
    description="返回所有 world-topic 绑定关系，可按 topic_id 过滤。",
)
def list_bindings(
    topic_id: str | None = None,
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> schemas.BindingListResponse:
    # 1. 业务查询：调用管理器获取绑定列表（可按 topic_id 过滤）
    bindings = kb.list_bindings(topic_id=topic_id)
    # 2. DTO 转换返回
    return schemas.BindingListResponse(total=len(bindings), bindings=bindings)


@kb_router.get(
    "/bindings/{world_id}",
    summary="查询单个 world 的绑定",
    description="返回该 world_id 当前绑定的主题，找不到返回 404。",
)
def get_world_binding(
    world_id: str,
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> dict[str, Any]:
    # 1. 参数校验：检查 world_id 是否为空
    if not world_id or not world_id.strip():
        raise HTTPException(400, detail="world_id 不能为空")
    # 2. 业务查询：调用管理器获取 world 绑定
    b = kb.get_world_binding(world_id.strip())
    # 3. 存在性校验：找不到返回 404
    if b is None:
        raise HTTPException(404, detail=f"world {world_id} 暂无绑定")
    # 4. 成功返回
    return b


@kb_router.post(
    "/bindings",
    response_model=schemas.BindingResponse,
    summary="绑定 world 到主题",
    description="已存在绑定则更新 topic_id 和 priority，否则新建。",
)
def bind_world_topic(
    body: schemas.BindingRequest,
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> schemas.BindingResponse:
    # 1. 业务执行：调用管理器绑定 world 到主题
    r = kb.bind_world_topic(body.world_id, body.topic_id, priority=body.priority)
    # 2. DTO 转换返回：组装响应对象
    return schemas.BindingResponse(
        ok=True,
        world_id=body.world_id,
        topic_id=body.topic_id,
        priority=body.priority,
        removed_rows=r.get("removed_rows"),
    )


@kb_router.delete(
    "/bindings/{world_id}",
    summary="解绑 world",
    description="删除该 world_id 的所有绑定关系，返回删除行数。",
)
def unbind_world(
    world_id: str,
    kb: KnowledgeManager = Depends(get_knowledge_manager),
) -> dict[str, Any]:
    # 1. 参数校验：检查 world_id 是否为空
    if not world_id or not world_id.strip():
        raise HTTPException(400, detail="world_id 不能为空")
    # 2. 业务执行：调用管理器解绑 world
    r = kb.unbind_world(world_id.strip())
    # 3. 成功返回：附上 ok 标记 + 关键信息
    return {
        "ok": True,
        "world_id": world_id.strip(),
        "removed_rows": r.get("removed_rows", 0),
    }


# ---- 模块公开导出 ----
__all__ = ["kb_router"]
