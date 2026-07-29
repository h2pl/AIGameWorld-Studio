"""自定义 Downloader Middlewares.

目前仅提供 :class:`BrowserUserAgentMiddleware`，把默认 Scrapy UA 替换成
真实 Chrome UA（避免 Fandom / 百科 / 知乎等站点直接 403）。
"""

from __future__ import annotations

import logging

from scrapy import signals

_log = logging.getLogger(__name__)

# 与现有 fetcher.py 保持一致，方便对比
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class BrowserUserAgentMiddleware:
    """所有请求统一用 Chrome UA.

    没有按池轮换（目前不需要；同一个 job 全用一个 UA 反而更「像一个人」）。
    """

    def __init__(self, user_agent: str = _BROWSER_UA, crawler=None):
        self.user_agent = user_agent
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler):
        mw = cls(
            user_agent=crawler.settings.get("CRAWLER_UA", _BROWSER_UA),
            crawler=crawler,
        )
        crawler.signals.connect(mw.spider_opened, signal=signals.spider_opened)
        return mw

    def spider_opened(self, spider=None, *args, **kwargs):
        # Scrapy 2.17: spider 参数可能不被传入，用 self.crawler.spider 兜底
        sp = spider or (self.crawler.spider if self.crawler else None)
        sp_name = sp.name if sp else "?"
        _log.info("[spider=%s] User-Agent = %s", sp_name, self.user_agent)

    # Scrapy 2.17: spider 参数将被移除，用 *args/**kwargs 兼容新旧版本
    def process_request(self, request, *args, **kwargs):
        if not request.headers.get("User-Agent"):
            request.headers["User-Agent"] = self.user_agent
        return None
