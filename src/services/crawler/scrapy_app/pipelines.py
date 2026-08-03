"""Scrapy pipelines：

1. 把抓取条目写入暂存区文件（``_metadata.json`` + ``{item_id}.md``）；
2. 回写 crawler_item 表的 fetched/content/error 状态；
3. 统计 done_items，job 结束时标 crawler_job done。
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

# Scrapy 加载本模块时相对 import 点数容易数错（pipelines 在 scrapy_app 下，store 在 crawler 下是 3 个点），
# 用绝对 import 更稳：把项目根加到 sys.path，再用 from src.services.crawler.xxx import。
if not hasattr(sys, "_ag_project_root_injected"):
    _pr = (
        _Path(__file__).resolve().parents[3]
    )  # pipelines → scrapy_app → crawler → services → src → 项目根（4 次 parents）
    if str(_pr) not in sys.path:
        sys.path.insert(0, str(_pr))
    sys._ag_project_root_injected = True

import json
import logging
import time
from pathlib import Path
from typing import Any

# 模块级 logger
_log = logging.getLogger(__name__)


# 把字符串路径转换为 Path 对象
# 参数 p：字符串路径或 Path
# 返回 Path：转换后的 Path 对象
def _as_path(p: str | Path) -> Path:
    # 已经是 Path 直接返回
    if isinstance(p, Path):
        return p
    # 否则用 str() 包一遍再转
    return Path(str(p))


# Pipeline 1：把每个 item 的正文和元信息写 staging 目录
# 优先级：300
class StagingWritePipeline:
    # Pipeline 初始化，spider 开始时只会被构造一次
    def __init__(self, settings):
        # settings 里写好的 STAGING_DIR
        self._staging_dir = _as_path(settings.get("STAGING_DIR"))
        # 目标 job_id（做防御校验用）
        self._job_id = settings.get("JOB_ID")
        # staging 目录必须存在
        self._staging_dir.mkdir(parents=True, exist_ok=True)

    # Scrapy 会用 from_crawler 构造 pipeline；没配的话用默认 __init__ 也行
    # 参数 crawler：Scrapy Crawler 实例
    # 返回 StagingWritePipeline：构造好的 pipeline 实例
    @classmethod
    def from_crawler(cls, crawler):
        # 用 settings 构造实例
        return cls(crawler.settings)

    # 每个 item 都会走一遍这里；Scrapy 约定必须返回 item（或抛 DropItem）
    # 参数 item：spider yield 的 CrawlerItem
    # 参数 spider：当前 spider
    # 返回 CrawlerItem：原样透出给后续 pipeline
    def process_item(self, item: Any, spider) -> Any:
        # 该 item 所属 job_id
        item_job_id = item.get("job_id")
        # 防御：item job_id 和 settings 不一致时跳过（但不能丢数据，先记日志）
        if self._job_id and item_job_id and self._job_id != item_job_id:
            # 不匹配记 warning
            _log.warning(
                "StagingWritePipeline job mismatch (settings=%s item=%s), skip write file",
                self._job_id,
                item_job_id,
            )
            # 不写文件但继续过后续 pipeline
            return item

        # item_id 必须存在，否则没法命名文件
        item_id = item.get("item_id")
        # item_id 缺失
        if not item_id:
            # 没法写文件，返回
            return item

        # --- 写 _metadata.json（append 模式，失败就记日志不中断爬取） ---
        # metadata 文件路径
        meta_file = self._staging_dir / "_metadata.jsonl"
        # 把字典里不能序列化的转成普通类型（datetime 啥的我们没有，简单兜底）
        try:
            # item 里的字段
            serializable = {
                # item ID
                "item_id": item.get("item_id"),
                # job ID
                "job_id": item.get("job_id"),
                # 原始 URL
                "url": item.get("url"),
                # 标题
                "title": item.get("title"),
                # 作者
                "author": item.get("author"),
                # 域名
                "domain": item.get("domain"),
                # 内容类型
                "content_type": item.get("content_type"),
                # 抓下来的字节数
                "size": item.get("size"),
                # 落盘的相对文件名（给 service.py promote 用）
                "content_file": f"{item_id}.md",
                # 抓取时间（yyyy-MM-dd HH:mm:ss）
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            # metadata 以 UTF-8 追加写一行 JSON
            with meta_file.open("a", encoding="utf-8") as f:
                # 一行一条
                f.write(json.dumps(serializable, ensure_ascii=False) + "\n")
        except Exception:
            # metadata 写失败记 warning
            _log.exception("write metadata failed: item_id=%s", item_id)

        # --- 写 {item_id}.md（正文文件，失败跳过） ---
        # 正文文件路径
        content_file = self._staging_dir / f"{item_id}.md"
        # HTML 原始内容（保留，后续如需再处理）
        html_bytes: bytes = item.get("html_bytes") or b""
        # 正文 markdown
        markdown: str = item.get("content") or ""
        # 正文里的外链附件：[(filename, bytes), ...]
        attachments: list[tuple[str, bytes]] = item.get("attachments") or []

        # markdown 和 html 都有（我们只写 md）
        try:
            # 写 Markdown，UTF-8
            content_file.write_text(markdown, encoding="utf-8")
        except Exception:
            # 写 md 失败记 warning
            _log.exception("write content md failed: item_id=%s", item_id)

        # 保留原始 HTML 供将来离线再处理（可选，不影响 promote）
        # 有 HTML 字节
        if html_bytes:
            # HTML 文件路径
            html_file = self._staging_dir / f"{item_id}.raw.html"
            try:
                # 二进制写 HTML
                html_file.write_bytes(html_bytes)
            except Exception:
                # HTML 写失败不影响 md
                pass

        # 把附件写到 attachments/{item_id}/（保留原始文件名）
        # 有附件才创建目录
        if attachments:
            # 该 item 的附件子目录
            att_dir = self._staging_dir / "attachments" / str(item_id)
            # 确保目录存在
            att_dir.mkdir(parents=True, exist_ok=True)
            # 遍历每个附件
            for fname, fbytes in attachments:
                # 文件名不安全则跳过（防御路径穿越）
                safe_fname = Path(fname).name
                # 过滤后文件名仍为空
                if not safe_fname:
                    # 跳过这个附件
                    continue
                try:
                    # 写附件文件
                    (att_dir / safe_fname).write_bytes(fbytes)
                except Exception:
                    # 写附件失败
                    _log.exception("write attachment failed: item_id=%s name=%s", item_id, safe_fname)

        # 完成，item 原样透传给下一个 pipeline
        return item


# Pipeline 2：SQLite 回写 item + 统计 + 标 job 完成
# 优先级：600（写文件之后再写 DB，避免 DB 成功但文件失败导致状态不一致）
class SQLiteWritePipeline:
    # 构造：拿 settings + 预导入 DB 函数
    # 参数 settings：Scrapy settings 对象
    def __init__(self, settings):
        # 模块日志
        self._log = logging.getLogger(f"{__name__}.SQLiteWritePipeline")
        # settings 里指定的 DB 路径（runner.py 注入）
        self._db_path = settings.get("DB_PATH")
        # 当前 job_id（settings 注入）
        self._job_id = settings.get("JOB_ID")
        # staging_dir（用于诊断日志）
        self._staging_dir = settings.get("STAGING_DIR")
        # 统计：已成功的 item 数
        self._done_count = 0
        # 统计：总条目数（从 Spider 开信号回调拿到，或者 process_item 时累加）
        self._total_items: int | None = None
        # 持有独立 SQLiteStore（子进程模式下，每个 pipeline 独立打开连接也行）
        self._store = None
        # 延迟导入（import 失败不影响 Scrapy 跑起来，只是不回写 DB）
        try:
            # 导入 SQLiteStore 类
            from src.utils.sqlite_store import SQLiteStore  # 延迟导入（绝对 import，避免点数错）
        except Exception as e:
            self._log.warning("SQLiteWritePipeline disabled: import SQLiteStore failed: %s", e)
            self._store = None
            self._SQLiteStore = None
        else:
            self._SQLiteStore = SQLiteStore
            # store 层函数：逐个 try/except，不存在的函数设 None，不影响其他功能
            _import_failed = False
            try:
                from src.services.crawler.store import create_job as _cjob
            except Exception as e:
                self._log.warning("import create_job failed: %s", e)
                _cjob = None
                _import_failed = True
            try:
                from src.services.crawler.store import get_job as _gjob
            except Exception as e:
                self._log.warning("import get_job failed: %s", e)
                _gjob = None
                _import_failed = True
            try:
                from src.services.crawler.store import update_job_status as _ujstat
            except Exception as e:
                self._log.warning("import update_job_status failed: %s", e)
                _ujstat = None
                _import_failed = True
            try:
                from src.services.crawler.store import create_item as _citem
            except Exception as e:
                self._log.warning("import create_item failed: %s", e)
                _citem = None
                _import_failed = True
            try:
                from src.services.crawler.store import update_item_fetched as _uifetched
            except Exception as e:
                self._log.warning("import update_item_fetched failed: %s", e)
                _uifetched = None
                _import_failed = True
            try:
                from src.services.crawler.store import mark_item_failed as _mifailed
            except Exception:
                # mark_item_failed 在某些版本的 store.py 里可能不存在（不是核心功能），跳过即可
                _mifailed = None

            if _import_failed:
                # 只有关键函数导入失败才禁用 pipeline（mark_item_failed 失败不影响）
                if not (_cjob and _gjob and _ujstat and _citem and _uifetched):
                    self._log.warning("SQLiteWritePipeline disabled: critical store functions missing")
                    self._store = None
                    self._SQLiteStore = None
                else:
                    # 只是 mark_item_failed 缺了，继续用
                    self._fn_create_job = _cjob
                    self._fn_get_job = _gjob
                    self._fn_update_job_status = _ujstat
                    self._fn_create_item = _citem
                    self._fn_update_item_fetched = _uifetched
                    self._fn_mark_item_failed = _mifailed
                    if self._db_path:
                        try:
                            self._store = SQLiteStore(Path(self._db_path))
                        except Exception as e:
                            self._log.warning("SQLiteWritePipeline disabled: open DB failed: %s", e)
                            self._store = None
                            self._SQLiteStore = None
            else:
                self._fn_create_job = _cjob
                self._fn_get_job = _gjob
                self._fn_update_job_status = _ujstat
                self._fn_create_item = _citem
                self._fn_update_item_fetched = _uifetched
                self._fn_mark_item_failed = _mifailed
                if self._db_path:
                    try:
                        self._store = SQLiteStore(Path(self._db_path))
                    except Exception as e:
                        self._log.warning("SQLiteWritePipeline disabled: open DB failed: %s", e)
                        self._store = None
                        self._SQLiteStore = None

    # Scrapy from_crawler：构造实例 + 绑定信号
    # 参数 crawler：Scrapy Crawler 实例
    # 返回 SQLiteWritePipeline：构造好的实例
    @classmethod
    def from_crawler(cls, crawler):
        # 实例化
        pipe = cls(crawler.settings)
        # 绑定 spider_opened：拿总数 + 把 job 标 running
        crawler.signals.connect(pipe.on_spider_opened, signal=__import__("scrapy").signals.spider_opened)
        # 绑定 spider_closed：最终把 job 标 done/failed
        crawler.signals.connect(pipe.on_spider_closed, signal=__import__("scrapy").signals.spider_closed)
        # 返回实例
        return pipe

    # Spider 刚启动回调
    # 参数 spider：当前 Spider 实例
    # 返回 None
    def on_spider_opened(self, spider) -> None:
        # store 没配就跳过
        if not self._store or not self._fn_get_job:
            return
        # 先把 job 标为 running（service 可能已经写过，再次覆盖没事）
        try:
            # 标 job running
            self._fn_update_job_status(self._store, self._job_id, "running", finished=False)
        except Exception:
            # 标 job 状态失败，不影响后续
            self._log.exception("mark job running failed")

        # 从 Spider 里拿总条目数（如果有的话）
        # （GenericSpider / TopicSearchSpider 都暴露了 total_items 属性）
        total = getattr(spider, "total_items", None)
        # total 是正整数
        if isinstance(total, int) and total > 0:
            # 存下来，后面计数用
            self._total_items = total
        else:
            # Spider 没给 total，就从 DB 里读 job.total_items（兜底）
            try:
                # 查 job 记录
                job = self._fn_get_job(self._store, self._job_id) or {}
                # 取 total_items
                t = job.get("total_items")
                # 正整数
                if isinstance(t, int) and t > 0:
                    # 存下来
                    self._total_items = t
            except Exception:
                # 查询失败也无所谓
                pass

    # item 必经之路：回写 crawler_item.fetched_status/content/error
    # 参数 item：spider yield 过来的 CrawlerItem（带 status/error/content）
    # 参数 spider：当前 Spider
    # 返回 CrawlerItem：原样透出
    def process_item(self, item: Any, spider) -> Any:
        # store 不可用就直接返回
        if not self._store:
            return item

        # 从 item 里拿关键字段
        item_id = item.get("item_id")
        # item 没 id 不处理
        if not item_id:
            return item
        # item 属于哪个 job（可能和 self._job_id 不同；我们写回它自己的 job）
        item_job_id = item.get("job_id") or self._job_id
        # 抓取状态（ok/failed，None 视为 ok）
        item_status = item.get("status") or "ok"
        # 错误消息（仅 failed 时用）
        error_msg = item.get("error") or None

        # 统一用 update_item_fetched（store.py 里 mark_item_failed 不存在）
        # 成功: status="fetched", 失败: status="failed" + error_msg
        try:
            # 目标内容类型
            ct = item.get("content_type") or ""
            # 文件路径 & 大小：PDF 类型用 attachments 下的真实 PDF 文件，其它用 {item_id}.md
            if self._staging_dir:
                if ct == "pdf":
                    # PDF：主文件指向 attachments/{item_id}/source.pdf（由 StagingWritePipeline 写入）
                    pdf_path = Path(self._staging_dir) / "attachments" / str(item_id) / "source.pdf"
                    rel_or_abs = str(pdf_path)
                    # PDF 文件大小：取 html_bytes 的长度（即原始响应字节数）
                    html_bytes: bytes = item.get("html_bytes") or b""
                    sz = len(html_bytes)
                else:
                    # HTML/其它：主文件是正文 Markdown
                    rel_or_abs = str(Path(self._staging_dir) / f"{item_id}.md")
                    sz = int(item.get("size") or 0)
            else:
                rel_or_abs = None
                sz = int(item.get("size") or 0)
            # sha256：staging 阶段暂时不计算（promote 时再算），留空
            sha = None

            db_status = "failed" if item_status == "failed" else "fetched"
            db_error = str(error_msg)[:500] if error_msg else None

            self._fn_update_item_fetched(
                # SQLiteStore
                self._store,
                # item ID
                item_id,
                # 内容类型（必填）
                content_type=ct,
                # 文件路径（staging 下 {item_id}.md）
                file_path=rel_or_abs,
                # 字节大小
                file_size=sz,
                # sha256（staging 阶段暂空）
                sha256=sha,
                # 标题（非 None 才会更新）
                title=item.get("title") or None,
                # 错误消息（仅失败时有值）
                error_msg=db_error,
                # DB 状态 fetched/failed
                status=db_status,
            )
        except Exception:
            # DB 写失败记日志
            self._log.exception("update_item_fetched failed: item_id=%s", item_id)

        # --- 自增 done_count + 随时把 job.done_items 同步到 DB ---
        # 成功抓取才计入 done_count（失败也计入 done_count 吗？失败的条也算"处理完"，便于 job 结束判断）
        # 两种情况都计数（因为该 item 已被 scheduler 出队，不会再处理）
        self._done_count += 1
        # 有 update_job_status 就同步 done_items 计数
        if self._fn_update_job_status:
            try:
                # 仅更新 done_items，不改 status/finished
                self._fn_update_job_status(
                    # store
                    self._store,
                    # job ID
                    item_job_id,
                    # 状态保持 running
                    status="running",
                    # 未 finished
                    finished=False,
                    # done 计数
                    done_items=self._done_count,
                )
            except Exception:
                # 同步 done 失败不影响爬取
                self._log.exception("sync done_items failed: job=%s count=%d", item_job_id, self._done_count)
        # 透出 item
        return item

    # Spider 关闭回调：把 job 标为 done（若有失败条则标 failed）
    # 参数 spider：Spider 实例
    # 参数 reason：Scrapy 关闭原因字符串（'finished' 表示正常结束）
    # 返回 None
    def on_spider_closed(self, spider, reason: str) -> None:
        # store 不可用就直接关 store（如果有）
        if not self._store:
            # 有关闭函数就关
            if self._SQLiteStore:
                try:
                    # 关闭连接
                    self._store.close()
                except Exception:
                    pass
            return

        # Scrapy 正常结束？其它原因（例如 Ctrl+C）视为 failed
        # 先按 reason 粗判
        final_status = "done" if reason == "finished" else "failed"

        # --- 防御：拿一下 job 记录，看看总条目数 ---
        # 有 get_job 就查
        if self._fn_get_job:
            try:
                # 查 job
                job = self._fn_get_job(self._store, self._job_id) or {}
                # 读 total_items
                total = job.get("total_items")
                # total 存在且正整数、且没 Spider 上报的 total 时，用 DB 里的
                if isinstance(total, int) and total > 0 and not self._total_items:
                    self._total_items = total
            except Exception:
                # 查询 job 失败
                pass

        # --- 再按 item 处理数 vs 总数决定 done/failed ---
        # 有总数，且最终处理数 < 总数（说明有条目丢了或 Spider 启动就崩）
        if self._total_items and self._done_count < self._total_items:
            # 标 failed（失败消息带上实际/总数）
            if final_status == "done":
                # 改成 failed
                final_status = "failed"

        # --- 真正写 DB：最终状态 + finished_at ---
        if self._fn_update_job_status:
            try:
                # 最终 update（finished=True）
                self._fn_update_job_status(
                    # store
                    self._store,
                    # job ID
                    self._job_id,
                    # 最终状态
                    status=final_status,
                    # 结束时间戳
                    finished=True,
                    # 最终 done 计数（避免最后几条被 process_item 最后一次更新覆盖）
                    done_items=self._done_count,
                )
            except Exception:
                # 最终状态写入失败
                self._log.exception("mark job final status failed: job=%s", self._job_id)

        # --- 关 store 连接 ---
        try:
            # 关闭 SQLite 连接
            self._store.close()
        except Exception:
            # 关闭失败不影响 Scrapy 退出
            pass
