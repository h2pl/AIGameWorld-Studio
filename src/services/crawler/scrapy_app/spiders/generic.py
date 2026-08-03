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

import sys
from pathlib import Path as _Path

# 本模块被 Scrapy 动态加载时相对 import 的点数容易数错（spiders/generic → crawler/fetcher 是 3 个点），
# 这里用绝对 import：把项目根加到 sys.path，再用 from src.services.crawler.xxx import。
if not hasattr(sys, "_ag_project_root_injected"):
    _pr = (
        _Path(__file__).resolve().parents[4]
    )  # generic → spiders → scrapy_app → crawler → services → src → 项目根（5层往上，4 次 parents）
    if str(_pr) not in sys.path:
        sys.path.insert(0, str(_pr))
    sys._ag_project_root_injected = True

import logging
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

import scrapy
from itemloaders.processors import TakeFirst
from scrapy.item import Field, Item
from scrapy.loader import ItemLoader

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
class GenericCrawlSpider(scrapy.Spider):
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
        url_titles: dict[str, str] | None = None,
        allowed_domains: list[str] | None = None,
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
        # 实例化日志（stderr 直接打，避免 Scrapy 的 logger 配置吞掉）
        import sys as _sys_init_g

        print(
            f"[GenericSpider.__init__] instance created: "
            f"job_id={self.job_id!r} seed_urls={len(self._seed_urls)} "
            f"db_path={getattr(self, '_db_path_str', None)!r} allowed_domains={self.allowed_domains!r}",
            file=_sys_init_g.stderr,
            flush=True,
        )

    # Scrapy 入口：yield 初始 Request
    # 返回 Iterable[scrapy.Request]：种子 URL 的 Request 列表
    def start_requests(self) -> Iterable[scrapy.Request]:
        # Scrapy 自带：
        #   - ROBOTSTXT_OBEY=True 的 RobotsTxtMiddleware
        #   - DOWNLOAD_DELAY + AutoThrottle settings
        # 所以这里不再依赖 fetcher 的 _rate_limit_init / _http_client 做额外控制。
        import sys as _sys_g

        reqs: list[scrapy.Request] = []
        print(f"[generic.start_requests] ENTER: {len(self._seed_urls)} seed urls", file=_sys_g.stderr, flush=True)
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
            reqs.append(req)
        print(f"[generic.start_requests] returning list of {len(reqs)} requests", file=_sys_g.stderr, flush=True)
        return reqs

    # 种子 URL 请求失败（超时、DNS、5xx 超过重试次数等）
    # 参数 failure：Scrapy Twisted Failure 对象
    # 返回 CrawlerItem：标记为 failed 的 item
    def _errback_seed(self, failure) -> CrawlerItem:
        import sys as _sys_err

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
        print(
            f"[generic._errback_seed] ENTER: url={url!r} msg={msg[:100]!r}",
            file=_sys_err.stderr,
            flush=True,
        )
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
        item = loader.load_item()
        print(
            f"[generic._errback_seed] YIELD item: item_id={item.get('item_id')!r} "
            f"status={item.get('status')!r} content_len={len(item.get('content') or '')}",
            file=_sys_err.stderr,
            flush=True,
        )
        return item

    # 解析响应：HTML→Markdown + 可选追链
    # 参数 response：Scrapy 响应对象（text/body 都有）
    # 返回 Iterable[CrawlerItem | scrapy.Request]：item 或继续 follow 的请求
    def parse(self, response: scrapy.http.Response) -> Iterable[Any]:
        import sys as _sys_parse

        # 延迟导入 fetcher 的 HTML→Markdown 转换（其它函数要么不存在，要么由 Scrapy 自身机制替代）
        from src.services.crawler.fetcher import _html_to_markdown

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

        print(
            f"[generic.parse] ENTER status={response.status} url={url!r} "
            f"body_len={len(response.body or b'')} is_seed={is_seed}",
            file=_sys_parse.stderr,
            flush=True,
        )

        # --- 先拿原始响应字节（可能后面被 httpx 降级覆盖） ---
        body_bytes: bytes = bytes(response.body or b"")
        # HTTP 响应头（降级后可能也要更新，所以这里先记变量）
        ct_header: str = response.headers.get("Content-Type", b"").decode("latin-1", errors="ignore")

        # --- HTTP 状态码判断：403/429/451/5xx 等 Scrapy TLS 指纹常被挡的情况，一律降级 httpx 直连 ---
        # 之前只针对 PDF 降级，但百度百科/灰机 Wiki/NGA 论坛等对 Scrapy TLS 指纹一律 403，
        # 用 httpx + http2 + Chrome  UA headers 基本都能正常拿到 HTML。
        bad_status = response.status >= 400
        # 值得降级的状态码：403(禁)/404(有时 Cloudflare 错挂)/429(限流)/451(法律) + 5xx
        _should_fallback_status = response.status in {403, 404, 429, 451, 500, 502, 503, 504}
        # 另外如果 response.body 极短（≤ 200 bytes 且是 HTML 但像 WAF challenge）也降级
        _body_too_small = not bad_status and len(body_bytes) <= 2048 and ct_header.lower().startswith("text/")

        if bad_status and _should_fallback_status:
            print(
                f"[generic.parse] URL got HTTP {response.status}, 尝试降级 httpx 直连抓取: {url[:100]}",
                file=_sys_parse.stderr,
                flush=True,
            )
            try:
                import httpx as _httpx

                from src.services.crawler.fetcher import _HEADERS, _TIMEOUT

                with _httpx.Client(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True, http2=True) as client2:
                    resp2 = client2.get(url)
                    try:
                        resp2.raise_for_status()
                    except Exception as _he:
                        print(
                            f"[generic.parse] 降级 httpx 也失败: {type(_he).__name__}: {_he}",
                            file=_sys_parse.stderr,
                            flush=True,
                        )
                        raise
                    fallback_bytes = bytes(resp2.content or b"")
                    fallback_ct = resp2.headers.get("Content-Type", "")
                    # 只要是 HTML 或 PDF 任意一种就 OK（其他类型后面会统一处理）
                    fallback_ct_lower = fallback_ct.lower()
                    is_html = (
                        "html" in fallback_ct_lower
                        or "xml" in fallback_ct_lower
                        or fallback_ct_lower.startswith("text/")
                        or (
                            len(fallback_bytes) >= 20
                            and (
                                fallback_bytes.lstrip()[:9].lower().startswith(b"<!doctype")
                                or b"<html" in fallback_bytes[:2048].lower()
                            )
                        )
                    )
                    is_pdf = "pdf" in fallback_ct_lower or fallback_bytes[:4] == b"%PDF"
                    if not (is_html or is_pdf):
                        print(
                            f"[generic.parse] 降级 httpx 拿到的不是 HTML/PDF "
                            f"(ct={fallback_ct!r} head={fallback_bytes[:12]!r} len={len(fallback_bytes)})",
                            file=_sys_parse.stderr,
                            flush=True,
                        )
                        raise RuntimeError("fallback response is neither HTML nor PDF")
                    print(
                        f"[generic.parse] 降级 httpx 成功！拿到 {len(fallback_bytes)} bytes "
                        f"type={'pdf' if is_pdf else 'html'}",
                        file=_sys_parse.stderr,
                        flush=True,
                    )
                    body_bytes = fallback_bytes
                    ct_header = fallback_ct
                    bad_status = False
            except Exception as _fb:
                print(
                    f"[generic.parse] 降级 httpx 最终失败，记为 failed: {type(_fb).__name__}: {_fb}",
                    file=_sys_parse.stderr,
                    flush=True,
                )
                bad_status = True

        if bad_status:
            # 4xx/5xx：种子 URL 返回失败 item（保证 job 会计数）；非种子静默丢
            if is_seed:
                item_id = seed_item_id or f"auto-{abs(hash(url)):x}"
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
                yield loader.load_item()
            return

        # --- 类型识别 & 转换（HTML/PDF） ---
        # 注：body_bytes / ct_header 已在上方降级分支可能被覆盖
        # --- 内容类型判断（_detect_content_type 在 fetcher 里不存在，用简单本地实现） ---
        ct_lower = ct_header.lower()
        if "pdf" in ct_lower or (body_bytes[:4] == b"%PDF"):
            content_type = "pdf"
        elif "html" in ct_lower or "xml" in ct_lower or ct_lower.startswith("text/"):
            content_type = "html"
        else:
            # 最后兜底：按 body 内容猜
            try:
                head_snippet = body_bytes[:1024].decode("utf-8", errors="ignore").lstrip().lower()
            except Exception:
                head_snippet = ""
            if head_snippet.startswith("<!doctype html") or "<html" in head_snippet:
                content_type = "html"
            else:
                content_type = "other"
        print(
            f"[generic.parse] content_type detect: ct_header={ct_header!r} "
            f"body_head={body_bytes[:16]!r} => content_type={content_type!r}",
            file=_sys_parse.stderr,
            flush=True,
        )
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
            # PDF 分支：_pdf_to_markdown 在 fetcher 里不存在 → 这里先留空解析，
            # 原始 PDF 字节保存在 html_bytes 字段，promote 后 KB pipeline 会用专业 PDFReader 解析。
            if content_type == "pdf":
                ok = True
                # PDF 标题从 URL 最后一段取（后续可人工改）
                _parts = url.rstrip("/").rsplit("/", 1)
                title = _parts[-1] if len(_parts) == 2 and _parts[-1] else url
                author = ""
                markdown = ""  # 正文不解析，留给 KB pipeline
                attachments = [{"filename": "source.pdf", "bytes": body_bytes}]  # 原始 PDF 字节放 attachments
                err_msg = None
            else:
                # HTML 分支：HTML→Markdown
                # fetcher._html_to_markdown 返回 2 元组 (title, markdown)
                try:
                    # _html_to_markdown 第一个参数是 str，body_bytes 是 bytes → 先 decode
                    try:
                        html_text = body_bytes.decode("utf-8", errors="replace")
                    except Exception:
                        html_text = body_bytes.decode("latin-1", errors="replace")
                    print(
                        f"[generic.parse] HTML decode ok, text_len={len(html_text)} calling _html_to_markdown...",
                        file=_sys_parse.stderr,
                        flush=True,
                    )
                    title, markdown = _html_to_markdown(html_text, url)
                    ok = bool(markdown) or bool(title)
                    print(
                        f"[generic.parse] _html_to_markdown done: ok={ok} title={title[:40]!r} md_len={len(markdown)}",
                        file=_sys_parse.stderr,
                        flush=True,
                    )
                except Exception as _he:
                    ok = False
                    title = ""
                    markdown = ""
                    err_msg = f"html parse failed: {type(_he).__name__}: {_he}"
                    print(
                        f"[generic.parse] _html_to_markdown EXCEPTION: {err_msg}",
                        file=_sys_parse.stderr,
                        flush=True,
                    )
                else:
                    author = ""
                    attachments = []
                    err_msg = None
                    if not ok:
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
        item = loader.load_item()
        # 调试：打印 item 的关键字段值（不是 loader 的输出，而是真正 item dict）
        print(
            f"[generic.parse] YIELD item: item_id={item.get('item_id')!r} "
            f"status={item.get('status')!r} content_type={item.get('content_type')!r} "
            f"title={str(item.get('title') or '')[:40]!r} "
            f"size={item.get('size')!r} content_len={len(item.get('content') or '')} "
            f"html_bytes_type={type(item.get('html_bytes')).__name__} "
            f"html_bytes_len={len(item.get('html_bytes') or b'')}",
            file=_sys_parse.stderr,
            flush=True,
        )
        yield item

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
            # 注：robots.txt 由 Scrapy 自带的 RobotsTxtMiddleware 负责（ROBOTSTXT_OBEY=True）
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
