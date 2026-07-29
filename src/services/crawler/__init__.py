"""Crawler service package — 独立爬虫模块（与 KB pipeline 解耦）.

对外只暴露 ``CrawlerService``，调用方传入 SQLiteStore + project_root 即可用。
不依赖 KnowledgeManager / 向量库 —— 只负责搜/抓/存本地暂存区。
"""

from __future__ import annotations

from .service import CrawlerService
from .search import SearchResult

__all__ = ["CrawlerService", "SearchResult"]
