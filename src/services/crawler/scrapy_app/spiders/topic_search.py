"""主题搜索式爬取 Spider.

流程：
1. 用 search.web_search(query) 拿 Top N 搜索结果；
2. 把结果 URL 一条一条建 crawler_item 记录（如果没建过）；
3. 把每个 URL 当种子 Request 交给 Scrapy Downloader（复用 GenericSpider 的 parse 逻辑，
   这里我们直接 import GenericSpider 并把 URL 映射好后复用它的 start_requests 太复杂，
   所以在 parse 层独立实现：response 处理逻辑与 GenericSpider.parse 保持一致）。
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

# Scrapy 加载本模块时相对 import 点数容易数错（spiders/topic_search → crawler/search 是 3 个点），
# 用绝对 import 更稳：把项目根加到 sys.path，再用 from src.services.crawler.xxx import。
if not hasattr(sys, "_ag_project_root_injected"):
    _pr = (
        _Path(__file__).resolve().parents[4]
    )  # topic_search → spiders → scrapy_app → crawler → services → src → 项目根（5 层往上，4 次 parents）
    if str(_pr) not in sys.path:
        sys.path.insert(0, str(_pr))
    sys._ag_project_root_injected = True

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import scrapy

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
    # 参数 content_type_priority：内容类型优先级 — "all"(默认) / "pdf_prefer" / "pdf_only"
    #   - all: 搜所有类型，不额外过滤
    #   - pdf_prefer: 先用 query + " filetype:pdf" 搜索，不足则回退到普通搜索；结果按 PDF 链接优先排序
    #   - pdf_only: 只搜带 filetype:pdf 的结果，不足则直接返回能搜到的数量
    # 参数 **kwargs：Scrapy 内部参数（name/crawler 等）
    def __init__(
        self,
        *,
        job_id: str,
        query: str,
        max_results: int = 10,
        store_db_path: str | None = None,
        allowed_domains: list[str] | None = None,
        follow_links: bool = False,
        follow_depth: int = 0,
        content_type_priority: str = "all",
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
        # 内容类型优先级（白名单化，非法值回退 all）
        ct = (content_type_priority or "all").lower()
        self._content_type_priority = ct if ct in ("all", "pdf_prefer", "pdf_only") else "all"

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

        # 实例化日志（stderr 直接打，避免 Scrapy 的 logger 配置吞掉）
        import sys as _sys_init

        print(
            f"[TopicSearchSpider.__init__] instance created: "
            f"job_id={self.job_id!r} query={self._query!r} max_results={self._max_results} "
            f"db_path={self._db_path_str!r} allowed_domains={self.allowed_domains!r}",
            file=_sys_init.stderr,
            flush=True,
        )

    # 判断搜索结果是否像 PDF 链接（用于排序 & 过滤）
    # 返回 int：分数越高越可能是 PDF；0 表示不像
    def _score_result_pdfness(self, r: Any) -> int:
        score = 0
        url = (getattr(r, "url", None) or "").lower()
        title = (getattr(r, "title", None) or "").lower()
        snippet = (getattr(r, "snippet", None) or "").lower()
        # URL 直接 .pdf 结尾 → 强信号
        if url.endswith(".pdf"):
            score += 100
        # URL 含 pdf 关键字（如 /pdf/ /download-pdf 等）
        if "pdf" in url:
            score += 20
        # 标题含 "pdf" / "电子书" / "电子书" / "下载"
        if "pdf" in title:
            score += 30
        if any(kw in title for kw in ("电子书", "电子书", "电子版", "下载")):
            score += 10
        # 摘要含 "pdf" / "电子书"
        if "pdf" in snippet:
            score += 15
        return score

    # 入口：先搜，再为每个结果建 item，再 yield Request
    # 返回 Iterable[scrapy.Request]：每个搜索结果 URL 的 Request
    def start_requests(self) -> Iterable[scrapy.Request]:
        import sys as _sys

        ct_prio = self._content_type_priority
        print(
            f"[topic_search.start_requests] ENTER: query={self._query!r} max_results={self._max_results} "
            f"job_id={self.job_id} content_type_priority={ct_prio}",
            file=_sys.stderr,
            flush=True,
        )
        from src.services.crawler.search import SearchResult, web_search

        # ====== 阶段 1：按 content_type_priority 执行搜索 ======
        raw_results: list[SearchResult] = []
        try:
            if ct_prio in ("pdf_prefer", "pdf_only"):
                # PDF 优先/仅 PDF：先搜 "query filetype:pdf"
                pdf_query = f"{self._query} filetype:pdf"
                print(
                    f"[topic_search.start_requests] pdf-priority: 1st pass query={pdf_query!r}",
                    file=_sys.stderr,
                    flush=True,
                )
                raw_results = list(web_search(pdf_query, max_results=self._max_results) or [])
                print(
                    f"[topic_search.start_requests] 1st pass (filetype:pdf) got {len(raw_results)} results",
                    file=_sys.stderr,
                    flush=True,
                )

                if ct_prio == "pdf_prefer" and len(raw_results) < self._max_results:
                    # pdf_prefer：不够就补一轮普通搜索
                    need = self._max_results - len(raw_results)
                    print(
                        f"[topic_search.start_requests] pdf_prefer: 补 {need} 条普通搜索结果",
                        file=_sys.stderr,
                        flush=True,
                    )
                    try:
                        extra = list(web_search(self._query, max_results=need * 2) or [])
                        # 去重（按 URL）
                        seen_urls = {r.url for r in raw_results}
                        for er in extra:
                            if getattr(er, "url", None) and er.url not in seen_urls:
                                raw_results.append(er)
                                seen_urls.add(er.url)
                                if len(raw_results) >= self._max_results:
                                    break
                    except Exception as _e2:
                        print(
                            f"[topic_search.start_requests] 普通补搜异常（忽略）：{_e2}", file=_sys.stderr, flush=True
                        )
            else:
                # all：普通搜索
                raw_results = list(web_search(self._query, max_results=self._max_results) or [])
        except Exception as _e:
            import traceback as _tb

            print(
                f"[topic_search.start_requests] web_search RAISED: {type(_e).__name__}: {_e}",
                file=_sys.stderr,
                flush=True,
            )
            _tb.print_exc(file=_sys.stderr)
            raw_results = []

        # ====== 阶段 2：垃圾结果过滤（官方首页 / 电商 / 视频站 / SF / 营销页等） ======
        # 说明：必应中文搜索的前 5-8 条经常是 wow.blizzard.cn / battlenet.com.cn / 电商页等非资料页。
        # 这里用黑名单域名 + 明显无资料意义的标题关键词提前过滤掉，让真正的资料（百科/论坛翻译/Wiki/文库）排前面。
        _BLACKLIST_DOMAINS = (
            # 暴雪官方营销首页（纯介绍，非资料文本/PDF）
            "wow.blizzard.cn",
            "worldofwarcraft.blizzard.com",
            "app-wow-blizzardcn.com.cn",
            "battlenet.com.cn",
            "blizzard.com",
            # 视频/直播平台（纯视频，不提供文本/PDF 下载）
            "bilibili.com",
            "youtube.com",
            "youtu.be",
            "douyin.com",
            "iesdouyin.com",
            "kuaishou.com",
            "huya.com",
            "douyu.com",
            "youku.com",
            # 电商平台（卖实体书，不提供下载/文本）
            "suning.com",
            "taobao.com",
            "tmall.com",
            "jd.com",
            "dangdang.com",
            "amazon.cn",
            "amazon.com",
            "pinduoduo.com",
            "kaola.com",
            # 私服 / 广告嫌疑域名（含 "wowsf"、"sf" 等的自建域名也在下面标题关键词过滤）
            "791680.com",
            # 明显的短链接 / 追踪页 / 已知垃圾域名
            "aydvjch.cc",
            "omqvggglz.com",
            "anzixin888.com",
            "longcat.chat",
            "manus.im",
            "missav365.sbs",
            "moomoo.com",
            "mew.design",
            "byzhihuo.com",
            "ds6r63epm75a1.cloudfront.net",
            "hanime2.net",
            "trae.cn",
            "midjourney.com",
            "ck444.ai",
            "dailianqun.com",
            "fang83.com",
            "95262.com",
            "zuixinpa.com",
            "archive.org",  # 偶尔有 djvu/txt，但搜索结果里大多不相关
            "google.com",  # 搜索首页
            "gemini.google.com",
            "tsinghua.edu.cn",  # 常出 404 错误页
            "crazyhome2000.com",
            "als1004.space",
            "51shipin.com",
            "xueqiu.com",
            "jianshu.com",  # 简书常 SEO 无关内容
            "44pas.com",  # 色情/同人小说站
            "bbxxxx.com",
            "bbxxxxxx.com",
            "smzdm.com",  # 什么值得买（推荐/分享而非资料）
            "ifeng.com",  # 新闻站 h5 页
            "toutiao.com",  # 今日头条话题页（单句无资料）
            "opearly.com",
            "yuanqibang.com",
            "aecolor.com",
            "xn22a.com",
            "hangxunzj.com",
            "dailiantong.com",
            "sctiehua.com",
            "yzsfkj.com",
            "jxcyhb.com",
            "233so.com",
            "steamzg.com",  # 小叽资源：游戏下载非资料
            "hkm168.com",
            "cyhome.net",
            "db.178.com",  # NGA 纯 NPC 数据库页，非世界观文本
            "9game.cn",  # 手游下载
            "26cu.com",
            "ali213.net",  # 游侠攻略页
            "jb51.net",  # 脚本之家攻略页
            "dcmdx.com",
            "yzz.cn",  # 叶子猪（也做攻略，但有时有背景资料，暂时保留）
            "pinterest.com",  # 图片墙（只有 pin 无全文）
        )
        _BLACKLIST_TITLE_KEYWORDS = (
            # 官方下载/营销页
            "官方网站",
            "内容更新现已上线",
            "免费试玩",
            "欢迎访问",
            # 电商
            "正版",
            "全新",
            "购买",
            "包邮",
            "价格",
            "图片:",
            "新华书店",
            # 私服/广告
            "私服",
            "外挂",
            "一条龙",
            "代练",
            # 视频
            "【Full Movie",
            "【Full movie",
            "Full Movie",
            "全集電影",
            "403 - Operations too frequent",
            "错误信息",
            "AI... | 51视频网",
        )
        _filtered: list = []
        for r in raw_results:
            url = (getattr(r, "url", None) or "").lower()
            title = (getattr(r, "title", None) or "").lower()
            # 域名黑名单命中
            if any(d in url for d in _BLACKLIST_DOMAINS):
                continue
            # 标题垃圾关键词（必须 URL 也不像 PDF 才过滤，避免误杀标题里有 "官方网站" 的文库页）
            pdf_like = (".pdf" in url) or ("/pdf" in url) or ("pdf" in title)
            if not pdf_like and any(kw.lower() in title for kw in _BLACKLIST_TITLE_KEYWORDS):
                continue
            _filtered.append(r)
        raw_results = _filtered

        # ====== 阶段 2.5：主题相关性过滤（只保留与 query 主题相关的结果） ======
        # 说明：DDGS + Bing 中文搜索常返回与 query 主体无关的 "卷一/卷二/全集/PDF下载" SEO 页
        # （如资本论、郭沫若全集、高达设定、SLG 补档等）。
        # 这里从原始 query 里抽取核心主题关键词（去除 "PDF"/"下载"/"第一卷" 等通用限定词），
        # 标题 / 摘要 / URL 至少命中 1 个主题词才保留。
        _STOP_KW = (
            "pdf",
            "下载",
            "电子书",
            "电子版",
            "合集",
            "全集",
            "中文版",
            "中文",
            "详细介绍",
            "介绍",
            "第一卷",
            "第二卷",
            "第三卷",
            "第四卷",
            "卷一",
            "卷二",
            "卷三",
            "filetype:pdf",
            "filetype",
            "官方小说",
            "官方",
            "小说",
            "设定集",
            "艺术画册",
            "原画集",
            "世界观",
            "种族",
            "地理",
            "历史剧情",
            "剧情",
            "故事背景",
            "国家势力",
            "地图",
            "燃烧军团",
            "巫妖王之怒",
            "上古之战",
        )
        # 构造主题关键词：先把 query 拆成单字/词，去掉停用词
        _raw_q = (self._query or "").lower()
        # 用 split 拆 + 保留 2 字以上中文片段（简单中文分词）
        import re as _re

        _tokens = [t for t in _re.split(r"[\s\+\-,\.\!\?\(\)\[\]【】《》，。；：！？、/\\_]", _raw_q) if t]
        # 主题词：>=2 字且不在 STOP 里的 token；另外补一些魔兽世界强相关的固有同义词
        _theme_kw = [t for t in _tokens if len(t) >= 2 and t not in _STOP_KW]
        # 强相关同义词（魔兽世界主题独有，通用型爬虫这里不硬编码也行，但本次任务是魔兽专门跑一次，加一下提高命中率）
        if any("魔兽" in t for t in _tokens) or any("warcraft" in t for t in _tokens):
            _theme_kw.extend(
                [
                    "魔兽世界",
                    "魔兽",
                    "warcraft",
                    "艾泽拉斯",
                    "wow",
                    "联盟",
                    "部落",
                    "兽人",
                    "人类",
                    "暗夜精灵",
                    "亡灵",
                    "侏儒",
                    "矮人",
                    "牛头人",
                    "巨魔",
                    "血精灵",
                    "德莱尼",
                    "熊猫人",
                    "燃烧军团",
                    "巫妖王",
                    "上古之战",
                    "编年史",
                    "暴风城",
                    "奥格瑞玛",
                    "炉石",
                    "暴雪",
                    "blizzard",
                ]
            )
        # 去重 + 转小写
        _theme_kw = list(dict.fromkeys(k.lower() for k in _theme_kw if k))
        if _theme_kw:
            _filtered2: list = []
            for r in raw_results:
                _u = (getattr(r, "url", None) or "").lower()
                _t = (getattr(r, "title", None) or "").lower()
                _s = (getattr(r, "snippet", None) or "").lower()
                if any(k in _t or k in _s or (len(k) >= 3 and k in _u) for k in _theme_kw):
                    _filtered2.append(r)
            # 注意：如果过滤后 0 条，为了不让整个 job 空跑，放宽为不过滤（可能 query 本身就是生僻词）
            if _filtered2:
                raw_results = _filtered2

        # ====== 阶段 3：排序 & 过滤 ======
        if ct_prio != "all":
            # PDF 优先：按 pdf 分数从高到低排序（真正 PDF 链接排最前）
            scored = [(self._score_result_pdfness(r), r) for r in raw_results]
            scored.sort(key=lambda x: x[0], reverse=True)
            # pdf_only：过滤掉 score=0 的（完全不像 PDF）
            if ct_prio == "pdf_only":
                scored = [(s, r) for s, r in scored if s > 0]
            raw_results = [r for _, r in scored]

        # 控制总数
        if len(raw_results) > self._max_results:
            raw_results = raw_results[: self._max_results]

        # 结果存下来（方便后面统计用）
        self._results = list(raw_results or [])
        print(
            f"[topic_search.start_requests] final result list: {len(self._results)} items", file=_sys.stderr, flush=True
        )
        for i, _r in enumerate(self._results):
            score = self._score_result_pdfness(_r)
            print(
                f"  [{i + 1}] score={score:3d} | {getattr(_r, 'title', '')[:70]} | {getattr(_r, 'url', '')}",
                file=_sys.stderr,
                flush=True,
            )
        # 空结果 → 啥 Request 也不产，spider 很快结束（pipeline.on_spider_closed 把 job 标 done）
        if not self._results:
            self._spider_log.warning("topic_search: 0 results for query=%s ct_prio=%s", self._query, ct_prio)
            self.total_items = 0
            return []

        # --- 为每个搜索结果建 crawler_item（service 层已经建过的就复用） ---
        # 有 DB 路径才去建
        if self._db_path_str:
            try:
                from src.services.crawler.store import create_item
                from src.utils.sqlite_store import SQLiteStore
            except Exception:
                SQLiteStore = None
            else:
                dbp = SQLiteStore(Path(self._db_path_str))
                # 遍历搜索结果
                for r in self._results:
                    url = getattr(r, "url", None)
                    if not url:
                        continue
                    title = getattr(r, "title", None) or ""
                    try:
                        # 注意：store.create_item(item_id, job_id, url, title) — 参数名与定义严格对齐
                        # 生成一个新的 item_id（create_item 需要显式传，因为 id 是 PRIMARY KEY）
                        try:
                            new_id = SQLiteStore.new_id()
                        except Exception:
                            # SQLiteStore 没 new_id：用 URL hash 兜底
                            new_id = f"auto-{abs(hash(url)):x}"
                        item = create_item(
                            dbp,
                            item_id=new_id,
                            job_id=self.job_id,
                            url=url,
                            title=title[:200] if title else "",
                        )
                        if item:
                            self._url_item_ids[url] = item["id"]
                    except Exception:
                        # 建 item 失败（DB 锁、权限、唯一约束冲突）：不影响，按 URL hash 生成也行
                        pass
                try:
                    dbp.close()
                except Exception:
                    pass
        # url_titles 从搜索结果补全（标题比页面解析的 title 更贴近搜索意图）
        for r in self._results:
            url = getattr(r, "url", None)
            title = getattr(r, "title", None) or ""
            if url:
                self._url_titles[url] = title

        # --- 然后按 GenericSpider 同样的方式产生 Request ---
        self.total_items = len(self._results)
        reqs: list[scrapy.Request] = []
        for r in self._results:
            url = getattr(r, "url", None)
            if not url:
                continue
            item_id = self._url_item_ids.get(url) or f"auto-{abs(hash(url)):x}"
            pre_title = self._url_titles.get(url) or ""
            req = scrapy.Request(
                url=url,
                callback=self.parse,
                errback=self._errback_seed,
                meta={
                    "seed_item_id": item_id,
                    "is_seed": True,
                    "depth": 0,
                    "pre_title": pre_title,
                },
                dont_filter=False,
                priority=10,
            )
            print(
                f"[topic_search.start_requests] APPEND Request: {req.url} (item_id={item_id})",
                file=_sys.stderr,
                flush=True,
            )
            reqs.append(req)

        print(f"[topic_search.start_requests] returning list of {len(reqs)} requests", file=_sys.stderr, flush=True)
        return reqs

    # 种子 URL 请求失败（搜索结果第一条就挂）→ 产一条失败 item，保证 job 计数完整
    # 参数 failure：Twisted Failure 对象
    # 返回 CrawlerItem：失败标记的 item
    def _errback_seed(self, failure) -> CrawlerItem:
        # 复用 GenericCrawlSpider 里现成的逻辑，直接 import 过来调
        from .generic import GenericCrawlSpider

        # 临时实例化一个 GenericCrawlSpider（只用来调 _errback_seed）
        # （更干净的方式是把这个函数抽到模块级，但保持结构简单先这么写）
        dummy = GenericCrawlSpider(
            # 传 job_id
            job_id=self.job_id,
            # 空 seed（不需要）
            seed_urls=[],
            # 空映射
            url_item_ids={},
        )
        # 调到 GenericCrawlSpider 的失败处理
        return dummy._errback_seed(failure)

    # 解析响应：完全复用 GenericCrawlSpider.parse（代码相同）
    # 参数 response：Scrapy Response
    # 返回 Iterable[CrawlerItem | scrapy.Request]：item 或追链请求
    def parse(self, response: scrapy.http.Response) -> Iterable[Any]:
        # 为了减少重复，直接调 GenericCrawlSpider 的 parse（因为 parse 是 instance method，
        # 只要 self 上有 _follow_links / _follow_depth / _allowed / allowed_domains / job_id 即可）
        # 直接把 GenericCrawlSpider 的 parse 绑定到当前 self 上执行
        # 注意：GenericCrawlSpider.parse 依赖 self._follow_links 等属性，我们在 __init__ 里都定义了，名字也一致
        # 这里用 unbound method 调用：把 self 作为第一个参数传进去
        from .generic import GenericCrawlSpider

        # 以 TopicSearchSpider 作为 self，执行 GenericCrawlSpider 的 parse
        return GenericCrawlSpider.parse(self, response)
