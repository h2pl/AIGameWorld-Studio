"""Crawler API router package.

挂在 /api/crawler 前缀下，与 /api/kb 平级。
serve.py 里 ``app.include_router(crawler_router)`` 即可。
"""

# ---- 导入依赖 ----
from __future__ import annotations

from fastapi import APIRouter

# ---- 子路由导入 ----
# 所有爬虫路由集中在 routes.py（提交抓取任务、查 job 详情、查 item 列表等）
from .routes import router as _routes_router

# ---- 顶层路由定义 ----
# 定义 crawler 顶层 APIRouter：统一前缀 + 统一 tags，OpenAPI 文档分组清晰
crawler_router = APIRouter(prefix="/api/crawler", tags=["crawler"])
# 把子路由（任务/items）挂到这个顶层 router
crawler_router.include_router(_routes_router)

# ---- 模块公开导出 ----
__all__ = ["crawler_router"]
