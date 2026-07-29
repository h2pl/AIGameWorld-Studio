"""主题搜索式爬取 Spider.

流程：
1. 用 search.web_search(query) 拿 Top N 搜索结果；
2. 把结果 URL 一条一条建 crawler_item 记录（如果没建过）；
3. 把每个 URL 当种子 Request 交给 Scrapy Downloader（复用 GenericSpider 的 parse 逻辑，
   这里我们直接 import GenericSpider 并把 URL 映射好后复用它的 start_requests 太复杂，
   所以在 parse 层独立实现：response 处理逻辑与 GenericSpider.parse 保持一致）。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import scrapy
from itemloaders.processors import TakeFirst
from scrapy.loader import ItemLoader

# 复用 GenericSpider 里定义的 CrawlerItem，避免重复声明
from .generic import CrawlerItem

# 模块级 logger
_log = logging.getLogger(__name__)


# 搜索 → 抓取 组合 Spider
class TopicSearchSpider(scrapy.Spider):
    # Spider 名（CrawlerProcess.crawl() 要对上）
    name = "topic_search"

    # Scrapy 调用的构造入口（runner.run_topic_crawl 传）
    # 参数 job_id：所属 job
    # 参数 query：搜索关键词
    # 参数 max_results：搜索结果条数上限
    # 参数 store_db_path：SQLite DB 路径（用来建 crawler_item）
    # 参数 allowed_domains：可选域名白名单
    # 参数 follow_links：是否追链
    # 参数 follow_depth：追链深度
    # 参数 **kwargs：Scrapy 内部参数（name/crawler 等）
    def __init__(
        self,
        *,
        job_id: str,
        query: str,
        max_results: int = 10,
        store_db_path: Optional[str] = None,
        allowed_domains: Optional[list[str]] = None,
        follow_links: bool = False,
        follow_depth: int = 0,
        **kwargs,
    ):
        # Scrapy 父类初始化
        super().__init__(**kwargs)
        # 保存 job_id
        self.job_id = job_id
        # 搜索关键词
        self._query = str(query or "").strip()
        # 搜索结果条数上限（下限 1，默认 10）
        self._max_results = max(1, int(max_results or 10))
        # DB 路径（字符串形式）
        self._db_path_str = store_db_path
        # 追链开关
        self._follow_links = bool(follow_links)
        # 追链深度（下限 0）
        self._follow_depth = max(0, int(follow_depth or 0))

        # 允许的域名白名单
        self._allowed: set[str] = set()
        # 传了白名单 → 直接用
        if allowed_domains:
            # 遍历白名单
            for d in allowed_domains:
                # 非空
                if d:
                    # 转小写加入
                    self._allowed.add(d.lower())
        # 把 allowed_domains 交给 Scrapy offsite middleware
        self.allowed_domains = list(self._allowed) or None

        # 预加载：初始化搜索结果列表（空，start_requests 里再真搜）
        self._results: list[Any] = []
        # 预加载：URL → item_id 映射（建 item 之后记下来，parse 用）
        self._url_item_ids: dict[str, str] = {}
        # URL → 标题映射（搜索结果的 title 更准，parse 时优先用）
        self._url_titles: dict[str, str] = {}
        # 总条目数（start_requests 结束后再填）
        self.total_items = 0
        # Spider 独立日志
        self._spider_log = logging.getLogger(f"{__name__}.TopicSearchSpider")

    # 入口：先搜，再为每个结果建 item，再 yield Request
    # 返回 Iterable[scrapy.Request]：每个搜索结果 URL 的 Request
    def start_requests(self) -> Iterable[scrapy.Request]:
        # 先做搜索
        from ..search import SearchResult, web_search
        # 调用 web_search（任何异常都返回空列表）
        results = web_search(self._query, max_results=self._max_results)
        # 结果存下来（方便后面统计用）
        self._results = list(results or [])
        # 空结果 → 啥 Request 也不产，spider 很快结束（pipeline.on_spider_closed 把 job 标 done）
        if not self._results:
            # 打印空结果日志
            self._spider_log.warning("topic_search: 0 results for query=%s", self._query)
            # 总条目 0
            self.total_items = 0
            # 结束
            return

        # --- 为每个搜索结果建 crawler_item（service 层已经建过的就复用） ---
        # 有 DB 路径才去建
        if self._db_path_str:
            # 延迟导入 store 函数
            try:
                # 导入 SQLiteStore
                from .......utils.sqlite_store import SQLiteStore
                # 导入 store 层函数
                from ..store import create_item, get_job, get_item
            except Exception:
                # store 导入失败：跳过建 item，后面按 URL 自动生成 item_id
                SQLiteStore = None
            else:
                # store 导入成功：先打开 DB 连接
                dbp = SQLiteStore(Path(self._db_path_str))
                # 先读一下 job 的 topic_id（create_item 需要）
                topic_id = ""
                # 读 job 记录
                try:
                    # 查 job
                    job = get_job(dbp, self.job_id) or {}
                    # topic_id
                    topic_id = job.get("topic_id") or ""
                except Exception:
                    # 读取失败也没关系，create_item 的 topic_id 允许空
                    pass

                # 遍历搜索结果
                for r in self._results:
                    # SearchResult 的 url 属性
                    url = getattr(r, "url", None)
                    # URL 为空跳过
                    if not url:
                        continue
                    # URL 标题
                    title = getattr(r, "title", None) or ""
                    # 先查 DB 里有没这个 item_id（按 job_id+url 找不到就新建）
                    try:
                        # 尝试根据 URL 反查 item（store 没提供专门函数，用 list_items 全扫太浪费）
                        # 我们直接 create_item：冲突时 store 会返回已存在那条（不重复建）
                        item = create_item(
                            # store
                            dbp,
                            # 所属 job
                            job_id=self.job_id,
                            # 主题 ID（供后续 promote 用）
                            topic_id=topic_id,
                            # 来源 URL
                            source_url=url,
                            # 标题（搜索结果 title 先填）
                            title=title[:200] if title else "",
                            # 来源：搜索
                            source_type="web",
                        )
                        # item 建成功 → 记 item_id
                        if item:
                            self._url_item_ids[url] = item["id"]
                    except Exception:
                        # 建 item 失败（DB 锁、权限）：不影响搜索结果本身，只是没 item_id
                        pass
                # 关 DB
                try:
                    dbp.close()
                except Exception:
                    pass
        # url_titles 从搜索结果补全（标题比页面解析的 title 更贴近搜索意图）
        for r in self._results:
            # URL
            url = getattr(r, "url", None)
            # 标题
            title = getattr(r, "title", None) or ""
            # 有 URL 才记
            if url:
                self._url_titles[url] = title

        # --- 然后按 GenericSpider 同样的方式产生 Request ---
        # 延迟导入 fetcher 的 robots 检查 & rate limit 初始化
        from ..fetcher import _can_fetch, _rate_limit_init
        # 初始化 httpx.Client（用于 robots）
        _rate_limit_init()

        # 初始化 httpx client 变量（供 _can_fetch 用）
        from ..fetcher import _http_client
        # 总条目数 = 实际搜索结果数
        self.total_items = len(self._results)
        # 遍历每个搜索结果
        for r in self._results:
            # 拿 URL
            url = getattr(r, "url", None)
            # 没有 URL 跳过
            if not url:
                continue
            # 拿 item_id（没建过就按 URL hash 自动生成）
            item_id = self._url_item_ids.get(url) or f"auto-{abs(hash(url)):x}"
            # 预填标题
            pre_title = self._url_titles.get(url) or ""
            # robots 检查（fail-open 原则）
            try:
                # client 非空且明确禁止 → 跳过
                if _http_client is not None and not _can_fetch(url, _http_client):
                    # 记日志
                    self._spider_log.info("robots deny: %s", url)
                    # 跳过这个 Request
                    continue
            except Exception:
                # robots 检查异常 → 放行
                pass

            # 构造 Request（与 GenericSpider.start_requests 结构一致）
            req = scrapy.Request(
                # 目标 URL
                url=url,
                # 回调：和 GenericSpider 同样的 parse 入口（下面实现）
                callback=self.parse,
                # 错误回调：种子失败要产 item
                errback=self._errback_seed,
                # 上下文
                meta={
                    # item_id
                    "seed_item_id": item_id,
                    # 是种子 Request
                    "is_seed": True,
                    # 深度 0
                    "depth": 0,
                    # 预填标题
                    "pre_title": pre_title,
                },
                # 不滤重
                dont_filter=False,
                # 优先级：搜索结果先抓
                priority=10,
            )
            # 产出 Request
            yield req

    # 种子 URL 请求失败（搜索结果第一条就挂）→ 产一条失败 item，保证 job 计数完整
    # 参数 failure：Twisted Failure 对象
    # 返回 CrawlerItem：失败标记的 item
    def _errback_seed(self, failure) -> CrawlerItem:
        # 复用 GenericSpider 里现成的逻辑，直接 import 过来调
        from .generic import GenericSpider
        # 临时实例化一个 GenericSpider（只用来调 _errback_seed）
        # （更干净的方式是把这个函数抽到模块级，但保持结构简单先这么写）
        dummy = GenericSpider(
            # 传 job_id
            job_id=self.job_id,
            # 空 seed（不需要）
            seed_urls=[],
            # 空映射
            url_item_ids={},
        )
        # 调到 GenericSpider 的失败处理
        return dummy._errback_seed(failure)

    # 解析响应：完全复用 GenericSpider.parse（代码相同）
    # 参数 response：Scrapy Response
    # 返回 Iterable[CrawlerItem | scrapy.Request]：item 或追链请求
    def parse(self, response: scrapy.http.Response) -> Iterable[Any]:
        # 为了减少重复，直接调 GenericSpider 的 parse（因为 parse 是 instance method，
        # 只要 self 上有 _follow_links / _follow_depth / _allowed / allowed_domains / job_id 即可）
        # 直接把 GenericSpider 的 parse 绑定到当前 self 上执行
        # 注意：GenericSpider.parse 依赖 self._follow_links 等属性，我们在 __init__ 里都定义了，名字也一致
        # 这里用 unbound method 调用：把 self 作为第一个参数传进去
        from .generic import GenericSpider
        # 以 TopicSearchSpider 作为 self，执行 GenericSpider 的 parse
        return GenericSpider.parse(self, response)
