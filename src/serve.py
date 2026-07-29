"""AIGameWorld Studio Web 服务（FastAPI 升级版）.

升级说明
--------
原来用标准库 ``http.server.HTTPServer`` + 2 个路由（/ 和 /pack/{id}）做 YAML
pack 查看器；现在升级成 FastAPI 以支持：

1. 完整的知识库 API（``/api/kb/*``，注册自 ``src.api.knowledge.kb_router``）
2. 知识库管理 UI（``/kb/*``）——上传/文档列表/搜索/任务
3. OpenAPI docs（``/docs``）/ ReDoc（``/redoc``）
4. 原有 2 个路由 **完全兼容** — Jinja 模板（index_yaml.html / pack.html）没变，
   CSS 变量、配色、渲染逻辑完全一致，用户看到的 UI 没变化。

外部调用
--------
直接启动 (CLI):
    aw-studio serve world-packs/custom --port 8888

作为模块:
    from src.serve import run
    run("world-packs/custom", port=8888)
"""

# __future__ 导入：新版本语法兼容
from __future__ import annotations

# 标准库导入
import argparse
import contextlib
import json
import time
from pathlib import Path
from typing import Any, Iterator

# 第三方库导入（uvicorn / yaml / FastAPI 生态）
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# 本地模块导入：API 路由 + 一次性迁移工具
from .api.crawler import crawler_router
from .api.knowledge import kb_router
from .api.knowledge.deps import _migrate_once

# ---------------------------------------------------------------------------
# Paths / 全局配置
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
TEMPLATES_DIR: Path = PROJECT_ROOT / "templates" / "serve"
SERVE_STATIC_DIR: Path = TEMPLATES_DIR / "_static"
SERVE_STATIC_DIR.mkdir(parents=True, exist_ok=True)

# 对应原来的 ViewerHandler.RARITY_COLORS
RARITY_COLORS: dict[str, str] = {
    "common": "#9ca3af",
    "uncommon": "#22c55e",
    "rare": "#3b82f6",
    "epic": "#a855f7",
    "legendary": "#f59e0b",
}

# 全局 Jinja（API 路由和老路由共用）
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Helpers (对应原来 _load_yaml / _load_yaml_all)
# ---------------------------------------------------------------------------

# 单个 YAML 文件安全读取：不存在或解析失败返回 None
def _load_yaml(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text("utf-8"))
    except Exception:
        return None


# 目录下所有 YAML 批量加载：glob *.yaml → 逐个 safe_load，跳过坏文件
def _load_yaml_all(dir_path: Path) -> list[dict]:
    if not dir_path.exists():
        return []
    out: list[dict] = []
    for f in sorted(dir_path.glob("*.yaml")):
        try:
            d = yaml.safe_load(f.read_text("utf-8"))
        except Exception:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# FastAPI app factory — 给 uvicorn / pytest / CLI 共用
# ---------------------------------------------------------------------------
SOURCE_DIR_GLOBAL: Path  # run() 时写入，供路由闭包读取


def create_app(
    pack_dir: str | Path,
    chroma_path: str | Path = PROJECT_ROOT / "data" / "chroma",
    data_dir: str | Path = PROJECT_ROOT / "data",
) -> FastAPI:
    """构建 FastAPI 实例（所有路由 + 全局状态注入）."""
    global SOURCE_DIR_GLOBAL
    SOURCE_DIR_GLOBAL = (
        Path(pack_dir).expanduser().resolve()
        if Path(pack_dir).is_absolute()
        else (PROJECT_ROOT / pack_dir).resolve()
    )
    chroma_path = Path(chroma_path).expanduser().resolve()
    data_dir = Path(data_dir).expanduser().resolve()

    # 启动时一次性跑迁移（幂等），避免每个请求都去检查
    _migrate_once(PROJECT_ROOT)

    app = FastAPI(
        title="AIGameWorld Studio",
        description="World Pack Viewer + Knowledge Base Manager",
        version="0.2.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # 在 app.state 上挂常量，依赖注入 (deps.py) 可直接读
    app.state.PROJECT_ROOT = PROJECT_ROOT
    app.state.SOURCE_DIR = SOURCE_DIR_GLOBAL
    app.state.CHROMA_PATH = chroma_path
    app.state.DATA_DIR = data_dir

    # API: 知识库 (/api/kb/...)
    app.include_router(kb_router)

    # API: 爬虫 (/api/crawler/...) — 独立 service，不依赖 KB pipeline
    app.include_router(crawler_router)

    # -------------------------------------------------------------------
    # 轻量辅助 API：给前端 UI 的 pack 下拉选择器用
    # -------------------------------------------------------------------
    # GET /api/packs：扫描 SOURCE_DIR 下 pack 目录并返回 JSON 列表（供前端下拉选择器）
    @app.get("/api/packs", tags=["viewer"], summary="Pack 列表 (JSON)")
    async def api_pack_list(request: Request) -> list[dict[str, Any]]:
        src: Path = getattr(request.app.state, "SOURCE_DIR", SOURCE_DIR_GLOBAL)
        packs: list[dict[str, Any]] = []
        if src.exists():
            for d in sorted(src.iterdir()):
                if not d.is_dir() or not (d / "meta.yaml").exists():
                    continue
                meta = _load_yaml(d / "meta.yaml") or {}
                packs.append({
                    "id": d.name,
                    "name": meta.get("name", d.name),
                    "description": meta.get("description", ""),
                    "version": meta.get("version", ""),
                    "author": meta.get("author", ""),
                    "entities_count": max(0, sum(1 for _ in d.rglob("*.yaml")) - 1),
                })
        return packs

    # --- Web UI: 静态资源（可选） ---
    # serve/templates/_static 下的文件会映射到 /_static/
    try:
        app.mount("/_static", StaticFiles(directory=str(SERVE_STATIC_DIR)), name="static")
    except RuntimeError:
        pass  # 没有静态文件目录时忽略

    # -------------------------------------------------------------------
    # 原有兼容路由：/ + /pack/{id}
    # -------------------------------------------------------------------
    # GET /：兼容老版 Viewer，列出所有 pack 概览（index_yaml.html）
    @app.get("/", response_class=HTMLResponse, tags=["viewer"], summary="World Pack 列表（兼容）")
    async def viewer_index(request: Request) -> HTMLResponse:
        packs: list[dict[str, Any]] = []
        src: Path = getattr(request.app.state, "SOURCE_DIR", SOURCE_DIR_GLOBAL)
        if src.exists():
            for d in sorted(src.iterdir()):
                if not d.is_dir():
                    continue
                meta_path = d / "meta.yaml"
                if not meta_path.exists():
                    continue
                meta = _load_yaml(meta_path) or {}
                packs.append({
                    "id": d.name,
                    "name": meta.get("name", d.name),
                    "desc": meta.get("description", ""),
                    "total": max(0, sum(1 for _ in d.rglob("*.yaml")) - 1),
                })
        return templates.TemplateResponse(
            request=request,
            name="index_yaml.html",
            context={"packs": packs},
        )

    # GET /pack/{pack_id}：兼容老版 Viewer，展示单个 pack 所有实体（pack.html）
    @app.get(
        "/pack/{pack_id}",
        response_class=HTMLResponse,
        tags=["viewer"],
        summary="World Pack 详情（兼容）",
    )
    async def viewer_pack_detail(request: Request, pack_id: str) -> HTMLResponse:
        src: Path = getattr(request.app.state, "SOURCE_DIR", SOURCE_DIR_GLOBAL)
        pack_dir = src / pack_id
        if not pack_dir.is_dir() or not (pack_dir / "meta.yaml").exists():
            raise HTTPException(status_code=404, detail=f"pack not found: {pack_id}")

        meta = _load_yaml(pack_dir / "meta.yaml") or {}
        lore = _load_yaml_all(pack_dir / "lore")
        pcs = _load_yaml_all(pack_dir / "player_characters")
        actors = _load_yaml_all(pack_dir / "actors")
        items = _load_yaml_all(pack_dir / "items")
        scenes = _load_yaml_all(pack_dir / "scenes")
        objects = _load_yaml_all(pack_dir / "scene_objects")
        story = _load_yaml(pack_dir / "story_setup.yaml")

        return templates.TemplateResponse(
            request=request,
            name="pack.html",
            context={
                "pack_id": pack_id,
                "meta": meta,
                "lore": lore,
                "pcs": pcs,
                "actors": actors,
                "items": items,
                "scenes": scenes,
                "objects": objects,
                "story": story,
                "rarity_colors": RARITY_COLORS,
            },
        )

    # -------------------------------------------------------------------
    # 知识库 Web UI 路由（主题中心 · 单页应用，前端内置 Tab 切换）
    # -------------------------------------------------------------------
    _KB_HTML_PATH: Path = Path(__file__).resolve().parent / "web" / "kb.html"
    _KB_HTML_CACHE: list[bytes] = []  # 单元素列表，用于在闭包中缓存 bytes，避免 nonlocal 陷阱

    # 知识库 SPA 页面构造：缓存 HTML + 注入初始 tab（避免改 body）
    def _kb_page(request: Request, initial_tab: str) -> HTMLResponse:
        if not _KB_HTML_CACHE:
            _KB_HTML_CACHE.append(_KB_HTML_PATH.read_bytes())
        html = _KB_HTML_CACHE[0].decode("utf-8")
        # 在 <head> 末尾注入初始 tab 指示（避免改 body 造成问题）
        inject = f"<script>window.__KB_INITIAL_TAB__ = {json.dumps(initial_tab)};</script>"
        html = html.replace("</head>", inject + "\n</head>", 1)
        return HTMLResponse(content=html)

    # GET /kb：知识库首页 Tab — 搜索
    @app.get("/kb", response_class=HTMLResponse, tags=["kb-ui"], summary="知识库首页（搜索）")
    async def kb_home(request: Request) -> HTMLResponse:
        return _kb_page(request, "search")

    # GET /kb/upload：知识库 Tab — 文档上传
    @app.get("/kb/upload", response_class=HTMLResponse, tags=["kb-ui"], summary="知识库上传页面")
    async def kb_upload_page(request: Request) -> HTMLResponse:
        return _kb_page(request, "upload")

    # GET /kb/docs：知识库 Tab — 文档列表
    @app.get("/kb/docs", response_class=HTMLResponse, tags=["kb-ui"], summary="知识库文档列表")
    async def kb_docs_page(request: Request) -> HTMLResponse:
        return _kb_page(request, "docs")

    # GET /kb/jobs：知识库 Tab — 索引任务/审计日志
    @app.get("/kb/jobs", response_class=HTMLResponse, tags=["kb-ui"], summary="知识库任务/审计")
    async def kb_jobs_page(request: Request) -> HTMLResponse:
        return _kb_page(request, "jobs")

    # -------------------------------------------------------------------
    # 爬虫 Web UI（独立单页，与 KB pipeline 解耦）
    # -------------------------------------------------------------------
    _CRAWLER_HTML_PATH: Path = Path(__file__).resolve().parent / "web" / "crawler.html"
    _CRAWLER_HTML_CACHE: list[bytes] = []

    # 爬虫 SPA 页面构造：带 404 降级的 HTML 缓存
    def _crawler_page(request: Request) -> HTMLResponse:
        if not _CRAWLER_HTML_CACHE:
            if _CRAWLER_HTML_PATH.exists():
                _CRAWLER_HTML_CACHE.append(_CRAWLER_HTML_PATH.read_bytes())
            else:
                return HTMLResponse(content="crawler.html not found", status_code=404)
        return HTMLResponse(content=_CRAWLER_HTML_CACHE[0].decode("utf-8"))

    # GET /crawler：爬虫管理页（搜索/爬取/promote 一体化）
    @app.get("/crawler", response_class=HTMLResponse, tags=["crawler-ui"], summary="爬虫管理页")
    async def crawler_page(request: Request) -> HTMLResponse:
        return _crawler_page(request)

    # GET /health：健康检查（返回运行时目录 + 时间戳）
    @app.get("/health", tags=["health"], summary="健康检查")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "aw-studio-serve",
            "ts_ms": int(time.time() * 1000),
            "source_dir": str(SOURCE_DIR_GLOBAL),
            "chroma_path": str(chroma_path),
            "data_dir": str(data_dir),
        }

    return app


# ---------------------------------------------------------------------------
# Lifespan / 入口
# ---------------------------------------------------------------------------

# FastAPI lifespan 上下文管理器：目前占位，后续可放异步 worker 初始化
@contextlib.contextmanager
def _serve_lifespan(app: FastAPI) -> Iterator[None]:
    """FastAPI lifespan hook（目前只做占位，以后放异步 worker / 队列初始化）."""
    yield


# uvicorn 启动入口：打印访问链接 → create_app → uvicorn.run
def run(
    pack_dir: str = "world-packs/custom",
    host: str = "127.0.0.1",
    port: int = 8888,
    chroma_path: str | Path = PROJECT_ROOT / "data" / "chroma",
    data_dir: str | Path = PROJECT_ROOT / "data",
    *,
    log_level: str = "warning",
    access_log: bool = False,
) -> None:
    """启动 Studio Web 服务.

    与旧版 ``aw-studio serve`` CLI 完全兼容：参数顺序、默认值都不变。
    """
    root = str(pack_dir)
    print(f"YAML Packs 目录: {(Path(pack_dir) if Path(pack_dir).is_absolute() else (PROJECT_ROOT / pack_dir)).resolve()}")
    print(f"Studio Web UI : http://{host}:{port}")
    print(f"  · Pack 列表 : http://{host}:{port}/")
    print(f"  · 知识库 UI : http://{host}:{port}/kb (upload/docs/search/jobs)")
    print(f"  · 爬虫 UI   : http://{host}:{port}/crawler (search/crawl/promote)")
    print(f"  · API 文档  : http://{host}:{port}/docs (Swagger) / /redoc")
    print()

    app = create_app(pack_dir=root, chroma_path=chroma_path, data_dir=data_dir)
    # 启动
    uvicorn.run(
        app,
        host=host,
        port=int(port),
        log_level=log_level,
        access_log=access_log,
    )


# 独立参数解析器：供 python -m src.serve 直接调用（CLI serve 子命令另用 argparse）
def _build_arg_parser() -> argparse.ArgumentParser:
    """供 ``python -m src.serve`` 和 CLI 共用的参数解析器."""
    p = argparse.ArgumentParser(
        prog="aw-studio-serve",
        description="AIGameWorld Studio Web Server：World Pack Viewer + 知识库管理",
    )
    # === 数据源路径参数 ===
    p.add_argument(
        "--packs-dir",
        default="world-packs/custom",
        help="YAML World Pack 目录（默认: world-packs/custom）",
    )
    # === 网络监听参数 ===
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址（默认 127.0.0.1；局域网访问用 0.0.0.0）",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8888,
        help="监听端口（默认 8888）",
    )
    # === 持久化路径参数 ===
    p.add_argument(
        "--chroma-path",
        default=str(PROJECT_ROOT / "data" / "chroma"),
        help="ChromaDB 向量库目录",
    )
    p.add_argument(
        "--data-dir",
        default=str(PROJECT_ROOT / "data"),
        help="SQLite 元数据 (studio.db) 所在目录",
    )
    # === 日志参数 ===
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="打印 uvicorn access log + info log",
    )
    return p


if __name__ == "__main__":
    # 支持直接：uv run python -m src.serve --port 5173
    parser = _build_arg_parser()
    args = parser.parse_args()

    print("=" * 64)
    print("  AIGameWorld Studio  启动中...")
    print("=" * 64)
    run(
        pack_dir=args.packs_dir,
        host=args.host,
        port=int(args.port),
        chroma_path=args.chroma_path,
        data_dir=args.data_dir,
        log_level="info" if args.verbose else "warning",
        access_log=bool(args.verbose),
    )

