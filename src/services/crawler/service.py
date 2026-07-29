"""CrawlerService — 爬虫编排器（搜 / 抓 / 存本地暂存区）.

**架构边界**：本 service 只负责"搜+抓+存文件到 .staging/"，**不碰向量库、不写 kb_document**。
promote（暂存→knowledge/documents/）是文件搬运动作，也不触发 index；
真正入库由 KB pipeline 的 /api/kb/{topic}/index 独立完成 —— 两个功能完全解耦。

Scrapy 集成（v2）
----------------
原实现是同步 httpx + BeautifulSoup 单线程顺序抓取，一次只能抓几个 URL；
v2 改为 :mod:`~src.services.crawler.scrapy_app.runner` 在后台线程启动
``scrapy.crawler.CrawlerProcess``，基于 Scrapy 企业级调度器：

* 高并发（CONCURRENT_REQUESTS 默认 8，可按主题调大到 16~32）
* 自动去重（RFPDupeFilter）
* 自动重试（RETRY_TIMES 默认 2 次，覆盖 5xx/429/408）
* 单域限速（DOWNLOAD_DELAY 默认 1s，避免被 Fandom / 百科等站封）
* Robots.txt 合规（ROBOTSTXT_OBEY=True）

前台 API 立即返回 ``job_id``，后台由 Scrapy 驱动 Pipeline 更新
``crawler_item`` 与 ``crawler_job.done_items``，Extension 在
spider_opened/spider_closed 时把 job 状态置为 ``running`` / ``done`` / ``failed``。

原 ``_fetch_all`` 同步实现仍保留作为回退（若 Scrapy import 失败时
自动降级），确保不会因为 scrapy 包问题导致整个服务不可用。

暂存区位置
----------
``knowledge-bases/{topic_id}/.staging/crawler/{job_id}/``
放在 knowledge/ 目录之外，确保 KB pipeline 的 rglob 永远扫不到。
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import httpx

from ...utils.sqlite_store import SQLiteStore
from . import store as db
from .fetcher import FetchedFile, fetch_url
from .search import SearchResult, web_search

# 模块级 logger 实例
_log = logging.getLogger(__name__)

# 暂存区相对 topic_dir 的子路径
STAGING_SUBDIR = ".staging/crawler"

# Scrapy 并发 / 速率默认 —— 兼顾速度与被封风险
_SCRAPY_DEFAULTS = dict(
    # 全局并发请求数上限
    concurrent_requests=8,
    # 单域名并发请求数上限
    concurrent_per_domain=2,
    # 同域名连续请求之间的最小延迟（秒）
    download_delay=1.0,
    # Scrapy 日志级别
    log_level="INFO",
)


# 检测 Scrapy 及其 runner 是否可导入
# 返回 bool：True 表示 Scrapy 可用，False 表示不可用（将触发降级同步路径）
def _scrapy_available() -> bool:
    # 测试 scrapy 主包是否可用
    try:
        import scrapy  # noqa: F401
        # 测试自定义 runner 是否可导入
        from .scrapy_app import runner  # noqa: F401
        # 全部导入成功，Scrapy 可用
        return True
    except Exception:
        # 任何导入异常都视为不可用
        # 返回 False 触发降级同步路径
        return False


# 独立爬虫服务类：一个实例可服务多个 topic，封装搜索/抓取/暂存/promote/查询 API
class CrawlerService:
    """独立爬虫服务。一个实例可服务多个 topic。"""

    # CrawlerService 构造函数
    # 参数 store：SQLiteStore 实例，用于持久化 job/item
    # 参数 project_root：项目根目录路径，用于定位 topic 目录和 DB
    def __init__(self, store: SQLiteStore, project_root: Path):
        # 注入 SQLiteStore 依赖，用于持久化 job/item
        self._store = store
        # 项目根目录绝对路径
        self._root = Path(project_root).resolve()
        # 启动时一次性检测 Scrapy 是否可用
        self._use_scrapy = _scrapy_available()
        # Scrapy 不可用时记录警告日志
        if not self._use_scrapy:
            _log.warning("Scrapy 不可用，降级使用同步抓取（一次抓一个 URL）。可 `uv add scrapy` 启用高性能模式。")

    # ------------------------------------------------------------------
    # 路径工具
    # ------------------------------------------------------------------

    # 获取某个 topic 的根目录路径
    # 参数 topic_id：主题 ID
    # 返回 Path：{project_root}/knowledge-bases/{topic_id}
    def topic_dir(self, topic_id: str) -> Path:
        # 拼接 knowledge-bases/{topic_id}
        return self._root / "knowledge-bases" / topic_id

    # 获取某次 crawl job 的暂存目录，不存在则自动创建
    # 参数 topic_id：主题 ID
    # 参数 job_id：抓取任务 ID
    # 返回 Path：暂存目录绝对路径
    def staging_dir(self, topic_id: str, job_id: str) -> Path:
        # 拼接完整暂存路径
        d = self.topic_dir(topic_id) / STAGING_SUBDIR / job_id
        # 递归创建目录，已存在不报错
        d.mkdir(parents=True, exist_ok=True)
        # 返回 Path 对象
        return d

    # 获取 promote 后的知识库目录，不存在则自动创建
    # 参数 topic_id：主题 ID
    # 参数 source_type：来源类型子目录名（documents/lore 等）
    # 返回 Path：知识库目录绝对路径
    def knowledge_dir(self, topic_id: str, source_type: str) -> Path:
        # 拼接 knowledge/{source_type}
        d = self.topic_dir(topic_id) / "knowledge" / source_type
        # 确保目录存在
        d.mkdir(parents=True, exist_ok=True)
        # 返回 Path 对象
        return d

    # 将绝对路径转为相对项目根的相对路径（POSIX 分隔符 /）
    # 参数 abs_path：绝对路径 Path 对象
    # 返回 str：相对路径字符串，使用 / 分隔；不在根下则返回原路径
    def _rel(self, abs_path: Path) -> str:
        try:
            # 转相对路径并统一用 /
            return str(abs_path.relative_to(self._root)).replace("\\", "/")
        except ValueError:
            # 不在 _root 下的路径（异常情况）
            # 直接返回原绝对路径字符串
            return str(abs_path)

    # 获取 SQLite 数据库文件的绝对路径
    # 返回 Path：DB 文件路径；优先从 store 取 db_path 属性，否则按约定默认 data/studio.db
    def _db_path(self) -> Path:
        # 优先从 store 实例取 db_path 属性
        p = getattr(self._store, "db_path", None)
        # store 上有 db_path 属性
        if p is not None:
            # 转为 Path 返回
            return Path(p)
        # 兜底：按项目约定推
        # 默认 data/studio.db
        return (self._root / "data" / "studio.db").resolve()

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------

    # 封装 web_search 为字典列表返回
    # 参数 query：搜索关键词
    # 参数 max_results：最大返回条数
    # 返回 list[dict]：每个 dict 包含 title/url/snippet
    def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        # 调用底层搜索实现
        results = web_search(query, max_results=max_results)
        # 每个 SearchResult 转 dict 返回
        return [r.to_dict() for r in results]

    # ------------------------------------------------------------------
    # 抓取：贴 URL 模式
    # ------------------------------------------------------------------

    # 批量抓取给定 URL → 落到暂存区，返回 job_id
    # 参数 topic_id：主题 ID
    # 参数 urls：待抓取的 URL 列表
    # 参数 source_type：promote 时的目标子目录名
    # 参数 created_by：调用来源标识（审计用）
    # 返回 str：新创建的 job_id；Scrapy 可用时立即返回（后台异步），同步时抓取完才返回
    def crawl_urls(
        self,
        topic_id: str,
        urls: list[str],
        *,
        source_type: str = "documents",
        created_by: str = "ui",
    ) -> str:
        """批量抓取给定 URL → 落到暂存区，返回 job_id.

        Scrapy 可用时：后台线程异步抓，API 立即返回；job 状态由 Scrapy Extension
        + Pipeline 回写为 pending → running → done/failed。
        """
        # 用于去重的 URL 集合
        seen: set[str] = set()
        # 去重后的 URL 列表（保持原顺序）
        unique_urls: list[str] = []
        # 遍历输入的 URL 列表
        for u in urls:
            # 去除首尾空白
            u = u.strip()
            # URL 非空且未出现过
            if u and u not in seen:
                # 标记为已见
                seen.add(u)
                # 加入结果列表
                unique_urls.append(u)

        # 生成新的 job 唯一 ID
        job_id = SQLiteStore.new_id()
        # 创建并获取该 job 的暂存目录
        staging = self.staging_dir(topic_id, job_id)
        # 转为相对路径存入库
        staging_rel = self._rel(staging)

        # 先一次性把 crawler_item 全建好（分配 id，Scrapy Pipeline 用这些 id 回写）
        # URL → item_id 映射表，供 Pipeline 查
        url_item_ids: dict[str, str] = {}
        # 待批量插入的 item 元数据列表
        items_meta: list[dict[str, Any]] = []
        # 为每个去重后的 URL 分配 item_id
        for i, u in enumerate(unique_urls):
            # 生成 item 唯一 ID
            item_id = SQLiteStore.new_id()
            # 记录映射
            url_item_ids[u] = item_id
            # 组装 item 元数据
            items_meta.append({
                # item 主键
                "id": item_id,
                # 所属 job ID
                "job_id": job_id,
                # 目标 URL
                "url": u,
                # 标题留空，抓取后回填
                "title": "",
                # 初始状态 pending
                "status": "pending",
            })

        # 在 crawler_job 表创建 job 记录
        db.create_job(
            self._store,
            job_id=job_id,
            # 所属 topic
            topic_id=topic_id,
            # 模式：贴 URL 批量抓取
            mode="urls",
            # URL 模式无搜索 query
            query=None,
            # promote 时目标子目录名
            source_type=source_type,
            # 暂存目录相对路径
            staging_dir=staging_rel,
            # 总 URL 条数
            total_items=len(unique_urls),
            # 调用来源标识
            created_by=created_by,
        )
        # 批量插入 item（保持顺序）
        # 逐条写入 crawler_item 表
        for it in items_meta:
            db.create_item(
                self._store,
                item_id=it["id"],
                job_id=it["job_id"],
                url=it["url"],
                title=it["title"],
            )

        # 写 _meta.json（保留兼容原实现的调试信息）
        # 组装元数据字典
        meta = {
            # job ID
            "job_id": job_id,
            # topic ID
            "topic_id": topic_id,
            # 抓取模式
            "mode": "urls",
            # 去重后的 URL 列表
            "urls": unique_urls,
            # URL→item_id 映射
            "url_item_ids": url_item_ids,
            # 使用的抓取引擎
            "engine": "scrapy" if self._use_scrapy else "sync-httpx",
            # 调用来源
            "created_by": created_by,
        }
        # 写入 JSON 文件
        (staging / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # Scrapy 可用：走异步后台线程
        if self._use_scrapy:
            # 后台异步，立即返回 job_id
            # 启动 Scrapy 的 generic URL 爬虫
            self._launch_scrapy_urls(
                job_id=job_id,
                staging=staging,
                urls=unique_urls,
                url_item_ids=url_item_ids,
            )
        else:
            # Scrapy 不可用：走同步回退路径（原实现）
            # 标记为运行中，记录开始时间
            db.update_job_status(self._store, job_id, "running", started=True)
            # 同步顺序抓取所有 URL
            self._fetch_all(topic_id, job_id, staging, unique_urls, source_type)
        # 返回新创建的 job_id
        return job_id

    # ------------------------------------------------------------------
    # 抓取：搜索模式（搜索 + 抓取一条龙）
    # ------------------------------------------------------------------

    # 搜索 + 抓取一条龙服务入口，返回 job_id
    # 参数 topic_id：主题 ID
    # 参数 query：搜索关键词
    # 参数 max_results：最大搜索结果条数
    # 参数 source_type：promote 目标子目录名
    # 参数 created_by：调用来源标识
    # 返回 str：job_id；Scrapy 可用时立即返回（搜索+抓取都在后台），同步时全部完成才返回
    def crawl_search(
        self,
        topic_id: str,
        query: str,
        *,
        max_results: int = 5,
        source_type: str = "documents",
        created_by: str = "ui",
    ) -> str:
        """搜索 + 抓取一条龙，返回 job_id.

        Scrapy 可用时：由 ``TopicSearchSpider`` 在 init 阶段自行 DDGS 搜索并建
        crawler_item 记录，避免前台阻塞；前台 API 几乎立即返回。
        """
        # 生成 job 唯一 ID
        job_id = SQLiteStore.new_id()
        # 创建暂存目录
        staging = self.staging_dir(topic_id, job_id)
        # 暂存目录相对路径
        staging_rel = self._rel(staging)

        # Scrapy 可用：搜索也放到 Spider 里异步做
        if self._use_scrapy:
            # total_items 先用 max_results 占位；TopicSearchSpider 会按实际搜索条数 UPDATE
            # 先建 job 记录（item 稍后由 Spider 创建）
            db.create_job(
                self._store,
                job_id=job_id,
                topic_id=topic_id,
                # 模式：搜索 + 抓取
                mode="search",
                # 保存搜索关键词
                query=query,
                source_type=source_type,
                staging_dir=staging_rel,
                # 先用 max_results 占位
                total_items=int(max_results),
                created_by=created_by,
            )
            # 组装 _meta.json 内容
            meta = {
                "job_id": job_id,
                "topic_id": topic_id,
                "mode": "search",
                "query": query,
                "max_results": max_results,
                "engine": "scrapy",
                "created_by": created_by,
            }
            # 写入元数据
            (staging / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            # 启动 Scrapy 搜索爬虫
            self._launch_scrapy_search(
                job_id=job_id,
                staging=staging,
                query=query,
                max_results=max_results,
            )
            # 立即返回 job_id，不等待搜索+抓取完成
            return job_id

        # --- Scrapy 不可用时走原同步路径 ---
        # 同步执行 DDGS 搜索
        results: list[SearchResult] = web_search(query, max_results=max_results)
        # 抽取搜索结果中的 URL 列表
        urls = [r.url for r in results]
        # 建立 URL→标题映射（抓取前就已知标题）
        url_titles = {r.url: r.title for r in results}

        # 创建 job 记录，total_items 用实际搜索到的条数
        db.create_job(
            self._store,
            job_id=job_id,
            topic_id=topic_id,
            mode="search",
            query=query,
            source_type=source_type,
            staging_dir=staging_rel,
            # 实际搜索结果数
            total_items=len(urls),
            created_by=created_by,
        )
        # 同步模式下 _meta.json 存更完整的信息
        meta = {
            "job_id": job_id,
            "topic_id": topic_id,
            "mode": "search",
            "query": query,
            "urls": urls,
            # 完整搜索结果用于调试
            "search_results": [r.to_dict() for r in results],
            "engine": "sync-httpx",
            "created_by": created_by,
        }
        # 写入元数据
        (staging / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        # 标记运行中
        db.update_job_status(self._store, job_id, "running", started=True)
        # 同步顺序抓取
        self._fetch_all(topic_id, job_id, staging, urls, source_type, url_titles=url_titles)
        # 全部抓完后返回 job_id
        return job_id

    # ------------------------------------------------------------------
    # Scrapy 启动（私有）
    # ------------------------------------------------------------------

    # 启动 Scrapy 贴 URL 模式爬虫（GenericSpider）
    # 参数 job_id：任务 ID
    # 参数 staging：暂存目录 Path
    # 参数 urls：起始 URL 列表
    # 参数 url_item_ids：URL 与 item_id 的映射表
    # 返回 None：启动失败时会记录日志并把 DB 中 job 置为 failed
    def _launch_scrapy_urls(
        self,
        *,
        job_id: str,
        staging: Path,
        urls: list[str],
        url_item_ids: dict[str, str],
    ) -> None:
        # 延迟导入，Scrapy 不可用时也能 import service 模块
        try:
            from .scrapy_app import runner

            # 先把状态标记为 pending（Extension 的 spider_opened 会再改成 running）
            # 置为 pending 等待 Spider 启动
            db.update_job_status(self._store, job_id, "pending")
            # 调用 runner 启动 GenericSpider 后台线程
            runner.run_generic_crawl(
                job_id=job_id,
                staging_dir=staging,
                # 把 db 路径传给 Scrapy 进程/线程
                db_path=self._db_path(),
                # 起始 URL 列表
                urls=urls,
                # URL 与 item_id 的映射
                url_item_ids=url_item_ids,
                # 不限制域名（用户贴的 URL 可能跨域）
                allowed_domains=None,
                # 贴 URL 模式默认不追链
                follow_links=False,
                # 追链深度为 0
                follow_depth=0,
                # 展开并发/延迟等默认参数
                **_SCRAPY_DEFAULTS,
            )
        except Exception as e:
            # Scrapy 启动失败（极端情况）
            # 记录异常栈
            _log.exception("启动 Scrapy (urls) 失败，回退到同步抓取")
            # 标记 job 失败
            db.update_job_status(
                self._store, job_id, "failed", finished=True,
                # 写入失败原因
                error_msg=f"ScrapyLaunchFailed: {type(e).__name__}: {e}",
            )

    # 启动 Scrapy 搜索模式爬虫（TopicSearchSpider）
    # 参数 job_id：任务 ID
    # 参数 staging：暂存目录 Path
    # 参数 query：搜索关键词
    # 参数 max_results：搜索结果上限条数
    # 返回 None：启动失败时记录日志并将 DB 中 job 置为 failed
    def _launch_scrapy_search(
        self,
        *,
        job_id: str,
        staging: Path,
        query: str,
        max_results: int,
    ) -> None:
        # 延迟导入 runner
        try:
            from .scrapy_app import runner

            # 先置 pending
            db.update_job_status(self._store, job_id, "pending")
            # 启动 TopicSearchSpider（内部先 DDGS 搜索再抓）
            runner.run_topic_crawl(
                job_id=job_id,
                staging_dir=staging,
                db_path=self._db_path(),
                # 搜索关键词
                query=query,
                # 搜索结果上限
                max_results=max_results,
                # 不限制域名
                allowed_domains=None,
                # 默认不追链
                follow_links=False,
                follow_depth=0,
                # 并发/速率默认值
                **_SCRAPY_DEFAULTS,
            )
        except Exception as e:
            # 启动异常
            # 记录异常
            _log.exception("启动 Scrapy (search) 失败")
            # 标记 job 失败
            db.update_job_status(
                self._store, job_id, "failed", finished=True,
                error_msg=f"ScrapyLaunchFailed: {type(e).__name__}: {e}",
            )

    # ------------------------------------------------------------------
    # 内部：批量抓取（同步回退路径，保留与原实现一致）
    # ------------------------------------------------------------------

    # 同步批量抓取所有 URL（Scrapy 不可用时的回退实现）
    # 参数 topic_id：主题 ID
    # 参数 job_id：任务 ID
    # 参数 staging：暂存目录 Path
    # 参数 urls：待抓取 URL 列表
    # 参数 source_type：来源类型子目录名
    # 参数 url_titles：可选的 URL→标题映射（搜索模式下已知标题）
    # 返回 None：结果通过 DB 写入，无返回值
    def _fetch_all(
        self,
        topic_id: str,
        job_id: str,
        staging: Path,
        urls: list[str],
        source_type: str,
        *,
        url_titles: dict[str, str] | None = None,
    ) -> None:
        # URL 列表为空，直接标记 done
        if not urls:
            # 0 条完成
            db.update_job_status(self._store, job_id, "done", done_items=0, finished=True)
            # 提前结束
            return

        # item 可能已在 crawl_urls 里建好；如果没建（crawl_search sync 路径），这里补
        # 查询已存在的 item，按 URL 建索引
        existing = {it["url"]: it for it in db.list_items(self._store, job_id)}
        # crawler_item 表还没记录（crawl_search 同步路径）
        if not existing:
            # 为每个 URL 创建 item
            for i, url in enumerate(urls):
                # 从搜索结果标题里取，没有就空
                title = (url_titles or {}).get(url, "")
                db.create_item(
                    self._store,
                    item_id=SQLiteStore.new_id(),
                    job_id=job_id,
                    url=url,
                    title=title,
                )
            # 重新从 DB 取含 id 的完整列表
            items = db.list_items(self._store, job_id)
        else:
            # 保持与 urls 同序
            # 按 urls 输入顺序重排 items
            items = []
            # 遍历输入的 URL 顺序
            for u in urls:
                # 该 URL 有对应的 item
                if u in existing:
                    # 加入结果列表
                    items.append(existing[u])

        # 延迟导入避免循环引用
        from .fetcher import _HEADERS, _TIMEOUT

        # 复用的 httpx 客户端
        client = httpx.Client(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        # 成功抓取计数
        done = 0
        try:
            # 顺序遍历每个 item 抓取
            for i, item in enumerate(items):
                # 调用单 URL 抓取函数
                fetched: FetchedFile = fetch_url(item["url"], staging, i, client=client)
                # 抓取失败分支
                if fetched.content_type == "failed":
                    # 写回失败信息
                    db.update_item_fetched(
                        self._store,
                        item["id"],
                        content_type="failed",
                        file_path=None,
                        file_size=0,
                        sha256=None,
                        # 有标题就用，否则保留旧的
                        title=fetched.title or item.get("title") or None,
                        # 记录失败原因
                        error_msg=fetched.error,
                        # 状态置 failed
                        status="failed",
                    )
                else:
                    # 抓取成功分支
                    # 写回抓取结果
                    db.update_item_fetched(
                        self._store,
                        item["id"],
                        # markdown/pdf/...
                        content_type=fetched.content_type,
                        # 落盘绝对路径
                        file_path=fetched.file_path,
                        # 文件字节数
                        file_size=fetched.file_size,
                        # 内容哈希
                        sha256=fetched.sha256,
                        # 优先用抓取到的标题
                        title=fetched.title or item.get("title") or None,
                    )
                    # 成功计数 +1
                    done += 1
            # 全部完成，置 done
            db.update_job_status(self._store, job_id, "done", done_items=done, finished=True)
        except Exception as e:
            # 顶层兜底异常（理论上 fetch_url 内部已 catch，这里防遗漏）
            # 记录异常栈
            _log.exception("crawl job %s failed", job_id)
            # 标记 job 失败
            db.update_job_status(
                self._store,
                job_id,
                "failed",
                # 保留已成功的条数
                done_items=done,
                # 失败原因
                error_msg=f"{type(e).__name__}: {e}",
                # 标记结束时间
                finished=True,
            )
        finally:
            # 无论成功失败都关闭 httpx 客户端
            client.close()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    # 分页列出 job，可按 topic 过滤
    # 参数 topic_id：可选的主题过滤；None 表示列出全部
    # 参数 limit：最大返回条数，默认 50
    # 参数 offset：分页偏移量，默认 0
    # 返回 list[dict]：job 字典列表，按创建时间倒序
    def list_jobs(self, topic_id: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        # 直接透传 store 查询
        return db.list_jobs(self._store, topic_id=topic_id, limit=limit, offset=offset)

    # 查询单个 job 详情（含 items）
    # 参数 job_id：任务 ID
    # 返回 dict | None：job 字典（含 items 字段）；不存在则返回 None
    def get_job(self, job_id: str) -> dict[str, Any] | None:
        # 先查 job 主记录
        job = db.get_job(self._store, job_id)
        # job 不存在
        if job is None:
            # 返回 None
            return None
        # 把该 job 的所有 item 也塞进结果
        job["items"] = db.list_items(self._store, job_id)
        # 返回完整 job 详情
        return job

    # 列出 job 的所有 item（含预览文本）
    # 参数 job_id：任务 ID
    # 返回 list[dict]：item 字典列表，每个包含 preview 字段（前 500 字预览）
    def list_job_items(self, job_id: str) -> list[dict[str, Any]]:
        # 从 DB 取 item 列表
        items = db.list_items(self._store, job_id)
        # 逐个补充预览字段
        for it in items:
            # 生成前 500 字预览
            it["preview"] = self._read_preview(it)
        # 返回带预览的 items
        return items

    # 读取单个 item 的预览文本（前 500 字）
    # 参数 item：item 字典，包含 file_path 等字段
    # 返回 str：预览文本；MD 文件去掉 frontmatter 后截断，PDF/其它返回类型和大小提示
    def _read_preview(self, item: dict[str, Any]) -> str:
        # 取文件路径
        fp = item.get("file_path")
        # 没有路径（失败 item 等）
        if not fp:
            # 返回空
            return ""
        # 转为 Path 对象
        p = Path(fp)
        # 文件不存在或不是普通文件
        if not p.exists() or not p.is_file():
            # 返回空
            return ""
        try:
            # Markdown 文件：读文本去掉 frontmatter 后截断
            if p.suffix.lower() == ".md":
                # UTF-8 读取，失败字符用占位符
                txt = p.read_text(encoding="utf-8", errors="replace")
                # 检测是否有 YAML frontmatter
                if txt.startswith("---"):
                    # 查找结束分隔符位置
                    end = txt.find("\n---", 3)
                    # 找到结束标记
                    if end != -1:
                        # 去掉 frontmatter 段
                        txt = txt[end + 4:].lstrip()
                # 只取前 500 字符作预览
                return txt[:500]
            # PDF 二进制：不打开，显示大小提示
            elif p.suffix.lower() == ".pdf":
                # 返回提示字符串
                return f"[PDF binary, {p.stat().st_size} bytes]"
            else:
                # 其它类型文件：统一显示类型和大小
                return f"[{p.suffix} file, {p.stat().st_size} bytes]"
        except Exception:
            # 任何读取异常都静默返回空
            return ""

    # ------------------------------------------------------------------
    # Promote / Discard（与原实现完全一致，不动）
    # ------------------------------------------------------------------

    # 把暂存文件搬到知识库目录（promote）
    # 参数 job_id：任务 ID
    # 参数 item_ids：可选的 item_id 白名单；None 表示 promote 所有成功抓取的 item
    # 返回 dict：包含 ok/job_id/topic_id/source_type/promoted_count/skipped_count/promoted/skipped 等字段
    def promote_job(self, job_id: str, item_ids: list[str] | None = None) -> dict[str, Any]:
        # 查 job 主记录
        job = db.get_job(self._store, job_id)
        # job 不存在
        if job is None:
            # 返回错误
            return {"ok": False, "error": "job not found"}
        # 已废弃的 job 不能 promote
        if job.get("status") == "discarded":
            return {"ok": False, "error": "job already discarded"}

        # 取出所属 topic
        topic_id = job["topic_id"]
        # 目标子目录类型
        source_type = job.get("source_type") or "documents"
        # 确保目标目录存在
        target_dir = self.knowledge_dir(topic_id, source_type)

        # 取该 job 的全部 item
        items = db.list_items(self._store, job_id)
        # 指定了部分 item 的白名单
        if item_ids is not None:
            # 转 set 加速查找
            id_set = set(item_ids)
            # 过滤保留白名单内 item
            items = [it for it in items if it["id"] in id_set]

        # 成功 promote 的条目列表
        promoted: list[dict[str, Any]] = []
        # 被跳过的条目列表
        skipped: list[dict[str, Any]] = []
        # 遍历待 promote 的 item
        for it in items:
            # 仅处理 fetched 状态（成功抓取）
            if it["status"] not in ("fetched",):
                # 记录跳过原因
                skipped.append({"item_id": it["id"], "url": it["url"], "reason": f"status={it['status']}"})
                # 下一条
                continue
            # 构造源文件 Path
            src = Path(it["file_path"]) if it["file_path"] else None
            # 源文件路径缺失或文件不存在
            if src is None or not src.exists():
                # 记录跳过
                skipped.append({"item_id": it["id"], "url": it["url"], "reason": "file missing"})
                # 下一条
                continue
            # 初始目标路径：同名
            target = target_dir / src.name
            # 目标路径已存在同名文件，需重命名避免覆盖
            if target.exists():
                # 拆分文件名和扩展名
                stem, suf = target.stem, target.suffix
                # 重命名计数器
                i = 1
                # 循环直到找到不冲突的名字
                while target.exists():
                    # 拼 stem-1.ext, stem-2.ext...
                    target = target_dir / f"{stem}-{i}{suf}"
                    # 计数器递增
                    i += 1
            try:
                # 执行文件移动（跨盘也能工作）
                shutil.move(str(src), str(target))
            except Exception as e:
                # 移动失败（权限、磁盘满等）
                # 记录失败原因
                skipped.append({"item_id": it["id"], "url": it["url"], "reason": f"move failed: {e}"})
                # 本条失败，继续下一条
                continue
            # DB 标记 promoted，存新路径
            db.mark_item_promoted(self._store, it["id"], new_path=self._rel(target))
            # 加入成功列表，供 API 响应使用
            promoted.append({
                "item_id": it["id"],
                "url": it["url"],
                # 最终文件名
                "file_name": target.name,
                # 最终绝对路径
                "target_path": str(target),
            })
        # 返回 promote 结果汇总
        return {
            "ok": True,
            "job_id": job_id,
            "topic_id": topic_id,
            "source_type": source_type,
            # 成功条数
            "promoted_count": len(promoted),
            # 跳过条数
            "skipped_count": len(skipped),
            "promoted": promoted,
            "skipped": skipped,
        }

    # 废弃 job：删除暂存文件 + 置 discarded 状态
    # 参数 job_id：任务 ID
    # 返回 dict：包含 ok/job_id/staging_removed 标记
    def discard_job(self, job_id: str) -> dict[str, Any]:
        # 查 job 是否存在
        job = db.get_job(self._store, job_id)
        # job 不存在
        if job is None:
            # 返回错误
            return {"ok": False, "error": "job not found"}
        # 取暂存目录相对路径
        staging_rel = job.get("staging_dir", "")
        # 拼成绝对路径
        staging_abs = self._root / staging_rel if staging_rel else None
        # 标记是否成功删除目录（0 否 1 是）
        removed_files = 0
        # 暂存目录存在才删
        if staging_abs and staging_abs.exists():
            try:
                # 递归删除整个暂存目录
                shutil.rmtree(staging_abs)
                # 删除成功标记
                removed_files = 1
            except Exception as e:
                # 删除失败（权限占用等）
                # 只记录警告不中断
                _log.warning("discard_job: rmtree %s failed: %s", staging_abs, e)
        # 所有 item 状态标记 discarded
        db.mark_job_items_discarded(self._store, job_id)
        # job 状态置 discarded 并标记结束
        db.update_job_status(self._store, job_id, "discarded", finished=True)
        # 返回结果和是否真的删了目录
        return {"ok": True, "job_id": job_id, "staging_removed": removed_files == 1}
