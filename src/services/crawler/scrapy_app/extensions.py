"""Scrapy 扩展 — spider 开/关时同步 ``crawler_job`` 状态.

使用 SQLiteStore（每 job 独立连接，避免与主线程共享同一个 sqlite3 连接）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from scrapy import signals

_log = logging.getLogger(__name__)


class JobStatusExtension:
    """在 spider 生命周期中同步 crawler_job.status.

    spider_opened → ``running``（started_at 写一次）
    spider_closed → ``done`` 或 ``failed``（finished_at 写一次）

    实际 ``done_items`` 计数由 Pipeline 每次成功时自行 UPDATE。
    """

    def __init__(self, settings, crawler=None):
        s = settings
        self.crawler = crawler
        self._job_id: str = s.get("JOB_ID", "")
        self._db_path: str = s.get("DB_PATH", "")
        self._store: Any = None  # 懒加载

    @classmethod
    def from_crawler(cls, crawler):
        ext = cls(crawler.settings, crawler=crawler)
        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        return ext

    # -------- 内部 --------
    def _get_store(self):
        if self._store is None and self._db_path:
            from ....utils.sqlite_store import SQLiteStore

            self._store = SQLiteStore(Path(self._db_path))
        return self._store

    # -------- 信号 --------
    # Scrapy 2.17: spider 参数将被移除，用 *args/**kwargs 兼容
    def spider_opened(self, *args, **kwargs):
        if not self._job_id:
            return
        store = self._get_store()
        if store is None:
            return
        try:
            from .. import store as db

            db.update_job_status(store, self._job_id, "running", started=True)
        except Exception as e:
            _log.warning("mark job running failed: %s", e)

    # Scrapy 2.17: spider/reason 参数将被移除，用 *args/**kwargs 兼容
    def spider_closed(self, *args, **kwargs):
        if not self._job_id:
            return
        store = self._get_store()
        if store is None:
            return
        try:
            from .. import store as db

            # 兼容新旧签名：旧版是 (spider, reason)，新版可能位置不同
            spider = args[0] if args else kwargs.get("spider")
            reason = args[1] if len(args) > 1 else kwargs.get("reason", "finished")
            sp = spider or (self.crawler.spider if self.crawler else None)

            # reason 由 Scrapy 给出：finished=正常；shutdown/cancel/其它=异常
            ok_reasons = {"finished"}
            # spider 可能设置了自定义 _error_count 等字段（可选）
            err = getattr(sp, "_close_error", None) if sp else None
            if err:
                db.update_job_status(
                    store, self._job_id, "failed", finished=True, error_msg=str(err)
                )
            elif reason in ok_reasons:
                db.update_job_status(store, self._job_id, "done", finished=True)
            else:
                db.update_job_status(
                    store, self._job_id, "failed", finished=True, error_msg=f"reason={reason}"
                )
        except Exception as e:
            _log.warning("mark job closed failed: %s", e)
        finally:
            if self._store is not None:
                try:
                    self._store.close()
                except Exception:
                    pass
                self._store = None
