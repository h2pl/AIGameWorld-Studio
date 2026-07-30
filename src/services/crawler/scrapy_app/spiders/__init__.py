"""Scrapy Spiders 包.

- :class:`TopicSearchSpider`：搜索模式 — 先用 DDGS 按主题关键词搜 URL，
  再批量抓取，并且可以按深度 follow 站内相关链接（用于同一主题下的
  系列条目，如 Fandom / 百度百科 / Wikipedia 的相关词条）。
- :class:`GenericCrawlSpider`：贴 URL 模式 — 给定一批 URL 直接抓取，
  支持 follow 链接到指定深度（默认 0 即只抓 seed）。
"""
# Spiders 包说明 / Spiders Package
# - topic_search.py: TopicSearchSpider — 关键词搜索 + PDF 优先抓取
#   - content_type_priority: "all" / "pdf_prefer" / "pdf_only"
#   - 搜索 URL 过滤：黑名单域名 + 主题相关性 + 标题关键词过滤
# - generic.py: GenericCrawlSpider — 给定 URL 列表直接抓取
#   - PDF 分支：保存原始 PDF bytes → attachments/source.pdf
#   - HTML 分支：trafilatura 抽取 Markdown + BeautifulSoup fallback
#   - 403/404/429/5xx 自动降级到 httpx（绕过 TLS 指纹

from .generic import GenericCrawlSpider
from .topic_search import TopicSearchSpider

__all__ = ["GenericCrawlSpider", "TopicSearchSpider"]
