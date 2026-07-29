"""API 层总入口包.

FastAPI 子路由按领域分两个子包：
- ``api.knowledge``：知识库（上传/索引/检索/主题/绑定）
- ``api.crawler``：爬虫（提交搜索/URL 抓取任务、查 job、查 items）

在 ``serve.py`` 里 ``from src.api.knowledge import kb_router`` 和
``from src.api.crawler import crawler_router`` 后直接
``app.include_router(...)`` 即可，不需要再 import 子模块。
"""

# ---- 包级配置 ----
# 这里故意不做 re-export：避免加载 FastAPI 相关代码时触发依赖链
# （serve.py 会显式 import 需要的 router）
# 注：各子路由在各自的 __init__.py 中暴露 router 入口
