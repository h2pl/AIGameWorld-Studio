"""Crawler service package — 独立爬虫模块（与 KB pipeline 解耦）.

对外只暴露 ``CrawlerService``，调用方传入 SQLiteStore + project_root 即可用。
不依赖 KnowledgeManager / 向量库 —— 只负责搜/抓/存本地暂存区。
"""
# Crawler 模块入口 / Crawler Package Entry
# - service.py:  CrawlerService（submit_crawl / promote_job / get_job_status）
# - search.py:   联网搜索封装（DDGS + Bing fallback）
# - scrapy_app/: Scrapy 实现（spiders + pipelines + runner + settings）
# - fetcher.py:  单 URL 同步抓取（简易场景，绕过 Scrapy）
# - store.py:    crawler_job / crawler_item 表读写封装

from __future__ import annotations

from .search import SearchResult
from .service import CrawlerService

__all__ = ["CrawlerService", "SearchResult"]
