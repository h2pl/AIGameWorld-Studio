"""Scrapy Spiders 包.

- :class:`TopicSearchSpider`：搜索模式 — 先用 DDGS 按主题关键词搜 URL，
  再批量抓取，并且可以按深度 follow 站内相关链接（用于同一主题下的
  系列条目，如 Fandom / 百度百科 / Wikipedia 的相关词条）。
- :class:`GenericCrawlSpider`：贴 URL 模式 — 给定一批 URL 直接抓取，
  支持 follow 链接到指定深度（默认 0 即只抓 seed）。
"""

from .generic import GenericCrawlSpider
from .topic_search import TopicSearchSpider

__all__ = ["GenericCrawlSpider", "TopicSearchSpider"]
