"""按给定种子 URL 列表抓取的通用 Spider（Scrapy 子进程模式）.

由 runner.run_generic_crawl() 注入：
- seed_urls / url_item_ids / url_titles
- follow_links / follow_depth
- allowed_domains（可选白名单）

Spider 内部会复用 :mod:`fetcher` 的 HTML→Markdown、robots 检查等逻辑，
对爬下来的响应再做一次转换（Scrapy Downloader 负责"拿到字节"，fetcher 负责
"字节 → 正文 Markdown"，解耦职责）。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import scrapy
from itemloaders.processors import TakeFirst
from scrapy.loader import ItemLoader
from scrapy.item import Field, Item

# 本模块 logger
_log = logging.getLogger(__name__)


# 爬虫产出的 Item：由 pipeline 消费写文件 + 写 DB
class CrawlerItem(Item):
    # 条目 ID（service 层创建时分配，spider 原样回传）
    item_id = Field(output_processor=TakeFirst())
    # job ID（整条 job 统一标识）
    job_id = Field(output_processor=TakeFirst())
    # 原始 URL
    url = Field(output_processor=TakeFirst())
    # 标题（可能为空字符串，pipeline 里按需要兜底）
    title = Field(output_processor=TakeFirst())
    # 作者（可选）
    author = Field(output_processor=TakeFirst())
    # 域名（service promote 时展示用）
    domain = Field(output_processor=TakeFirst())
    # 内容类型：html / pdf（fetcher._detect_content_type 返回值）
    content_type = Field(output_processor=TakeFirst())
    # 抓下来的正文大小（字节数，Markdown 字节）
    size = Field(output_processor=TakeFirst())
    # Markdown 正文（pipeline 写 {item_id}.md + 写 DB）
    content = Field(output_processor=TakeFirst())
    # 原始 HTML 字节（StagingWritePipeline 会存 .raw.html，后续离线再处理用）
    html_bytes = Field(output_processor=TakeFirst())
    # 正文里的外链附件列表：[(filename, bytes), ...]
    attachments = Field(output_processor=TakeFirst())
    # 抓取时间戳（ms），None 表示让 pipeline 自动填 time.time()*1000
    fetched_at = Field(output_processor=TakeFirst())
    # "ok" / "failed"，决定走 pipeline 哪个分支
    status = Field(output_processor=TakeFirst())
    # 失败原因（仅 status=="failed" 时用）
    error = Field(output_processor=TakeFirst())


# 按 URL 抓的通用 Spider
class GenericSpider(scrapy.Spider):
    # Spider 名（CrawlerProcess.crawl() 要对应上）
    name = "generic_crawl"

    # Scrapy 调用的构造入口（runner 传 **spider_kwargs）
    # 参数 job_id：所属 job
    # 参数 seed_urls：起始 URL 列表
    # 参数 url_item_ids：URL→item_id 映射（必选）
    # 参数 url_titles：URL→标题映射（可选）
    # 参数 allowed_domains：允许的域名白名单；None/空 表示不限制
    # 参数 follow_links：是否追链
    # 参数 follow_depth：追链深度（0=只抓种子页）
    # 参数 **kwargs：Scrapy 内部参数（name/crawler 等）
    def __init__(
        self,
        *,
        job_id: str,
        seed_urls: list[str],
        url_item_ids: dict[str, str],
        url_titles: Optional[dict[str, str]] = None,
        allowed_domains: Optional[list[str]] = None,
        follow_links: bool = False,
        follow_depth: int = 0,
        **kwargs,
    ):
        # Scrapy 父类初始化
        super().__init__(**kwargs)
        # 保存 job_id
        self.job_id = job_id
        # 允许域名白名单（Spider 内部判断；Scrapy offsite middleware 也会再判一次）
        self._allowed = set()
        # 初始化种子域名集合（用来限制追链时的 offsite）
        for u in seed_urls:
            # 解析 URL
            parsed = urlparse(u)
            # 有 netloc
            if parsed.netloc:
                # 加入允许的域名集合
                self._allowed.add(parsed.netloc.lower())
        # 调用方显式传了白名单 → 覆盖种子 URL 推导
        if allowed_domains:
            # 先清空
            self._allowed.clear()
            # 遍历显式白名单
            for d in allowed_domains:
                # 转小写加入
                if d:
                    self._allowed.add(d.lower())
        # allowed_domains 给 Scrapy offsite middleware 用
        self.allowed_domains = list(self._allowed) or None

        # seed URLs 复制一份（避免外部改 list）
        self._seed_urls: list[str] = [u for u in (seed_urls or []) if u]
        # URL→item_id 映射（复制）
        self._url_item_ids: dict[str, str] = dict(url_item_ids or {})
        # URL→标题（复制）
        self._url_titles: dict[str, str] = dict(url_titles or {})
        # 是否追链
        self._follow_links = bool(follow_links)
        # 追链深度下限 0
        self._follow_depth = max(0, int(follow_depth or 0))
        # 总条目数（= 种子 URL 数；追链时实际会更多，但 pipeline 只需要个下限）
        self.total_items = len(self._seed_urls)
        # Spider 自己的 logger（Scrapy 会注入，也可用 _log）
        self._spider_log = logging.getLogger(f"{__name__}.GenericSpider")

    # Scrapy 入口：yield 初始 Request
    # 返回 Iterable[scrapy.Request]：种子 URL 的 Request 列表
    def start_requests(self) -> Iterable[scrapy.Request]:
        # 导入 fetcher 的 robots 检查（延迟导入：Spider 真正跑起来才 import）
        from ..fetcher import _can_fetch, _rate_limit_init
        # 初始化 httpx.Client（用于 robots 检查）
        _rate_limit_init()

        # 遍历种子 URL
        for url in self._seed_urls:
            # 对应 item_id（没有就按 URL hash 兜底生成一个，保证 pipeline 不崩）
            item_id = self._url_item_ids.get(url)
            # 没 item_id：按 URL 构造一个（service 层应当都分配了，这里兜底）
            if not item_id:
                # 用内置 hash 再转十六进制（短）
                item_id = f"auto-{abs(hash(url)):x}"
            # 预填标题
            pre_title = self._url_titles.get(url) or ""
            # 构造初始 Request
            # callback=parse；meta 里带上下文（item_id/seed_url/title）
            req = scrapy.Request(
                # 目标 URL
                url=url,
                # 回调函数
                callback=self.parse,
                # 默认错误回调
                errback=self._errback_seed,
                # 传递上下文
                meta={
                    # 该 URL 的 item_id
                    "seed_item_id": item_id,
                    # 是否是种子 URL（不是追链）
                    "is_seed": True,
                    # 当前追链深度（种子 = 0）
                    "depth": 0,
                    # 预填标题
                    "pre_title": pre_title,
                },
                # 不滤重（同一个 URL 多次 submit 都处理）
                dont_filter=False,
                # 优先级高一点（种子先抓）
                priority=10,
            )
            # 产出 Request
            yield req

    # 种子 URL 请求失败（超时、DNS、5xx 超过重试次数等）
    # 参数 failure：Scrapy Twisted Failure 对象
    # 返回 CrawlerItem：标记为 failed 的 item
    def _errback_seed(self, failure) -> CrawlerItem:
        # 从 request 里拿 meta
        request = getattr(failure, "request", None)
        # 原始 URL
        url = str(request.url) if request else ""
        # meta
        meta = request.meta if request else {}
        # 关联 item_id
        item_id = meta.get("seed_item_id") or f"auto-{abs(hash(url)):x}"
        # 失败原因（取 Failure 的人类可读摘要，截断 300 字符）
        try:
            # Failure.getErrorMessage()
            msg = failure.getErrorMessage()
        except Exception:
            # 兜底：转字符串
            msg = str(failure)
        # 组装失败 Item（直接走 pipeline 写 DB，不走文件内容）
        loader = ItemLoader(item=CrawlerItem())
        # item_id
        loader.add_value("item_id", item_id)
        # job_id
        loader.add_value("job_id", self.job_id)
        # 原始 URL
        loader.add_value("url", url)
        # 抓失败：空标题
        loader.add_value("title", "")
        # 域名
        loader.add_value("domain", urlparse(url).netloc.lower())
        # 内容类型未知
        loader.add_value("content_type", "")
        # 大小 0
        loader.add_value("size", 0)
        # 正文空
        loader.add_value("content", "")
        # 状态 failed
        loader.add_value("status", "failed")
        # 错误信息（300 字符防溢出）
        loader.add_value("error", msg[:300])
        # 返回组装好的 Item
        return loader.load_item()

    # 解析响应：HTML→Markdown + 可选追链
    # 参数 response：Scrapy 响应对象（text/body 都有）
    # 返回 Iterable[CrawlerItem | scrapy.Request]：item 或继续 follow 的请求
    def parse(self, response: scrapy.http.Response) -> Iterable[Any]:
        # 延迟导入 fetcher 的转换函数
        from ..fetcher import (
            _detect_content_type,
            _can_fetch,
            _rate_limit_wait,
            _html_to_markdown,
        )

        # meta
        meta = response.meta
        # URL
        url = response.url
        # 域名
        domain = urlparse(url).netloc.lower()
        # 是种子 URL？
        is_seed = bool(meta.get("is_seed"))
        # 当前深度
        depth = int(meta.get("depth") or 0)
        # 种子时预填 item_id + title；追链时 item_id=None 由这里生成
        seed_item_id = meta.get("seed_item_id")
        # 预填标题
        pre_title = meta.get("pre_title") or ""

        # --- HTTP 状态码判断（4xx 一律算失败） ---
        # 4xx 状态码（不是 5xx，5xx 会被 Downloader 重试）
        if 400 <= response.status < 500:
            # 种子 URL：返回失败 item（保证 job 会计数）
            item_id = seed_item_id or f"auto-{abs(hash(url)):x}"
            # 组装失败 item
            loader = ItemLoader(item=CrawlerItem())
            loader.add_value("item_id", item_id)
            loader.add_value("job_id", self.job_id)
            loader.add_value("url", url)
            loader.add_value("title", "")
            loader.add_value("domain", domain)
            loader.add_value("content_type", "")
            loader.add_value("size", 0)
            loader.add_value("content", "")
            loader.add_value("status", "failed")
            loader.add_value("error", f"HTTP {response.status}")
            # 产出失败 item
            yield loader.load_item()
            # 结束解析
            return

        # --- 先做类型识别 & 转换（HTML/PDF） ---
        # 原始响应字节（二进制，避免 response.text 的编码异常）
        body_bytes = bytes(response.body or b"")
        # 响应头里的 Content-Type
        ct_header = response.headers.get("Content-Type", b"").decode("latin-1", errors="ignore")
        # 内容类型（html/pdf）
        content_type = _detect_content_type(body_bytes, ct_header)
        # 不是 HTML/PDF 直接丢掉（种子 URL 也要产一条 failed）
        if content_type not in ("html", "pdf"):
            # 是种子：失败 item；不是种子：静默丢
            if is_seed:
                # 生成 item_id
                item_id = seed_item_id or f"auto-{abs(hash(url)):x}"
                # 组装失败 item
                loader = ItemLoader(item=CrawlerItem())
                loader.add_value("item_id", item_id)
                loader.add_value("job_id", self.job_id)
                loader.add_value("url", url)
                loader.add_value("title", "")
                loader.add_value("domain", domain)
                loader.add_value("content_type", content_type or "")
                loader.add_value("size", len(body_bytes))
                loader.add_value("content", "")
                loader.add_value("status", "failed")
                loader.add_value("error", f"unsupported content type: {ct_header or 'unknown'}")
                # 产出
                yield loader.load_item()
            # 非种子页或 unsupported 都不追链了
            return

        # --- 正文转换：HTML/PDF → Markdown ---
        try:
            # PDF 分支：用 fetcher.pdf_to_markdown
            if content_type == "pdf":
                # 延迟导入 PDF 转 Markdown
                from ..fetcher import _pdf_to_markdown
                # 转 PDF
                title, author, markdown, attachments = _pdf_to_markdown(body_bytes)
                # 转换 OK
                ok = True
                # 错误信息空
                err_msg = None
            else:
                # HTML 分支：HTML→Markdown
                ok, title, author, markdown, attachments = _html_to_markdown(body_bytes, url)
                # 错误信息初始 None
                err_msg = None
                # 转换失败：给出错误信息
                if not ok:
                    # 拼接错误
                    err_msg = "html parse failed"
        except Exception as e:
            # 转换抛异常（parse 函数兜底失败）
            ok = False
            # 空标题
            title = ""
            # 空作者
            author = ""
            # 空正文
            markdown = ""
            # 空附件
            attachments = []
            # 错误消息
            err_msg = f"parse exception: {type(e).__name__}: {e}"

        # 标题合并：优先预填标题（搜索过来的 title 更准），没有就用解析的 title
        final_title = (pre_title or "").strip() or (title or "").strip()
        # Markdown 字节大小（UTF-8）
        size = len(markdown.encode("utf-8")) if markdown else 0

        # --- 组装成功/失败 item ---
        # item_id：种子用 seed_item_id；追链生成
        item_id = seed_item_id or f"auto-{abs(hash(url)):x}"
        # 组装 Item（哪怕 parse 失败也要走 pipeline 写一条记录，方便计数）
        loader = ItemLoader(item=CrawlerItem())
        # item_id
        loader.add_value("item_id", item_id)
        # job_id
        loader.add_value("job_id", self.job_id)
        # 原始 URL
        loader.add_value("url", url)
        # 最终标题
        loader.add_value("title", final_title)
        # 作者（可能空）
        loader.add_value("author", author or "")
        # 域名
        loader.add_value("domain", domain)
        # html / pdf
        loader.add_value("content_type", content_type)
        # Markdown 字节数
        loader.add_value("size", size)
        # 正文 Markdown
        loader.add_value("content", markdown)
        # 原始 HTML 字节（追链页也保留一份，方便离线重解析）
        loader.add_value("html_bytes", body_bytes)
        # 外链附件列表（图片等）
        loader.add_value("attachments", attachments)
        # 抓取时间戳（ms）：None 让 pipeline 填当前
        loader.add_value("fetched_at", None)

        # 成功/失败状态
        if ok:
            # 标记 ok
            loader.add_value("status", "ok")
            # 错误信息空
            loader.add_value("error", None)
        else:
            # 标记 failed
            loader.add_value("status", "failed")
            # 错误消息（截断 300 字符）
            loader.add_value("error", (err_msg or "parse failed")[:300])
        # 产出这个 item
        yield loader.load_item()

        # --- 追链：follow_links 开启且深度未超限、并且是 HTML（PDF 里的外链暂时不追） ---
        # 不开追链 / 已达最大深度 / PDF 不追链
        if (not self._follow_links) or depth >= self._follow_depth or content_type != "html":
            # 直接结束
            return

        # 取本页所有 <a href> 链接（Scrapy 内置 LinkExtractor 太重，我们用 response.follow 简单过滤）
        # 提取所有 href（selector 找 <a> 标签，非空 href）
        hrefs = response.css("a::attr(href)").getall()
        # 去重：同一页相同 href 不再重复 follow
        seen = set()
        # 限速：按域名等待一下（保护目标站）
        _rate_limit_wait(domain)
        # 遍历 href
        for href in hrefs:
            # 空 href 跳过
            if not href:
                continue
            # 处理 hash 片段（# 后的内容去掉，减少重复）
            hash_pos = href.find("#")
            # 去掉 hash
            if hash_pos >= 0:
                href = href[:hash_pos]
            # 再去空白（首尾）
            href = href.strip()
            # 空或只含 #
            if not href:
                continue
            # javascript: 协议跳过
            if href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            # 已见过该链接（同一页内去重）
            if href in seen:
                continue
            # 加入 seen
            seen.add(href)
            # 先拿到绝对 URL
            abs_url = response.urljoin(href)
            # 只允许 http/https
            if not (abs_url.startswith("http://") or abs_url.startswith("https://")):
                continue
            # 域名判断（allowed_domains 或与种子页同源）
            link_domain = urlparse(abs_url).netloc.lower()
            # 有限制且域名不在白名单 → 跳过
            if self.allowed_domains and link_domain not in self._allowed:
                continue
            # robots.txt 二次防御（Spider 自己再问一遍，Scrapy offsite middleware 是额外保护）
            try:
                # 拿 httpx client（全局单例，fetcher._http_client）
                from ..fetcher import _http_client
                # client 非空才判 robots
                if _http_client is not None and not _can_fetch(abs_url, _http_client):
                    # robots 不允许，跳过
                    continue
            except Exception:
                # robots 判断异常 → fail-open 允许抓
                pass
            # 生成追链 Request：callback=parse（递归处理同一套逻辑）
            req = response.follow(
                # 目标 URL（Scrapy 会帮做相对→绝对）
                abs_url,
                # 回调：同一个 parse
                callback=self.parse,
                # 种子错误回调同样可以复用（非种子失败我们不强制产 item）
                errback=None,
                # meta：深度+1，不是种子
                meta={
                    # 非种子
                    "is_seed": False,
                    # 深度递增
                    "depth": depth + 1,
                    # 追链没预填标题
                    "pre_title": "",
                },
                # 让 Scrapy 全局去重器帮我们过滤跨页重复
                dont_filter=False,
                # 深度越深优先级越低
                priority=max(0, 10 - depth - 1),
            )
            # 产出这个追链 Request
            yield req
