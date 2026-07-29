"""通用 SQLite 存储（薄封装）：

- 自动建库（文件不存在时创建 + 父目录）；
- 线程安全的 execute / executemany / fetch 封装；
- crawler 表初始化（crawler_job / crawler_item / crawler_topic 三张表）；
- 所有写操作都在内部 commit，失败自动 rollback，避免主流程 try/except。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

# 模块级 logger
_log = logging.getLogger(__name__)


# 轻量 SQLite 封装类
class SQLiteStore:
    # 构造：打开或创建 DB
    # 参数 path：SQLite 文件路径
    # 参数 auto_init_crawler：是否自动初始化 crawler 相关表
    # 参数 pragmas：可选的 PRAGMA 列表（默认 WAL / 外键 / 同步级别）
    def __init__(
        self,
        path: str | Path,
        *,
        auto_init_crawler: bool = True,
        pragmas: Optional[Iterable[str]] = None,
    ):
        # 路径转 Path
        self._path = Path(path)
        # 父目录不存在就创建
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 线程锁（同一连接多线程写必须串行；读也加锁，简单安全）
        self._lock = threading.RLock()
        # 打开 SQLite（check_same_thread=False：跨线程访问 OK，但所有访问都走锁）
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        # SQLite 连接 row_factory：让 fetch* 返回字典（可选，默认保持 tuple 兼容调用方）
        self._conn.row_factory = sqlite3.Row

        # 默认 pragmas
        default_pragmas = [
            # WAL：更好的并发读（多 FastAPI worker / 子进程）
            "PRAGMA journal_mode=WAL;",
            # 同步 NORMAL：WAL 模式下性能 + 断电安全平衡
            "PRAGMA synchronous=NORMAL;",
            # 缓存大小 = 64MB（更大的查询缓存，减少磁盘 IO）
            "PRAGMA cache_size=-65536;",
            # 外键约束启用（删除 job 时可级联删 item）
            "PRAGMA foreign_keys=ON;",
            # 忙超时 = 30s（多进程并发写时，排队等待最多 30s 再报 SQLITE_BUSY）
            "PRAGMA busy_timeout=30000;",
        ]
        # 应用 pragmas
        pragmas_to_apply = list(pragmas) if pragmas is not None else default_pragmas
        # 逐条执行（WAL 等失败不影响使用）
        for p in pragmas_to_apply:
            try:
                # 执行单条 PRAGMA
                self._conn.execute(p)
            except Exception:
                # 记 debug 日志（某些平台不支持 WAL 也没问题）
                _log.debug("apply pragma skipped: %s", p)
        # 提交 PRAGMA 结果（部分 PRAGMA 是持久性的）
        try:
            # commit
            self._conn.commit()
        except Exception:
            # commit 失败忽略
            pass

        # 可选：初始化 crawler_job / crawler_item / crawler_topic
        if auto_init_crawler:
            # 执行建表 SQL（幂等）
            self._init_crawler_schema()

    # 初始化 crawler 三张表（IF NOT EXISTS）
    # 返回 None
    def _init_crawler_schema(self) -> None:
        # 加锁写
        with self._lock:
            # 先建 topic 表（被 crawler_job 引用）
            # 先执行 DDL（多条写在一个 executescript 也行，分开写更清晰）
            # crawler_topic：topic_id → 元信息（标题等）
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crawler_topic (
                    id          TEXT PRIMARY KEY,
                    title       TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    created_at  INTEGER NOT NULL DEFAULT 0,
                    updated_at  INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # crawler_job：每次爬取任务
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crawler_job (
                    id          TEXT PRIMARY KEY,
                    topic_id    TEXT NOT NULL,
                    query       TEXT,
                    mode        TEXT NOT NULL DEFAULT 'urls',
                    status      TEXT NOT NULL DEFAULT 'pending',
                    source_type TEXT NOT NULL DEFAULT 'documents',
                    total_items INTEGER NOT NULL DEFAULT 0,
                    done_items  INTEGER NOT NULL DEFAULT 0,
                    staging_dir TEXT NOT NULL DEFAULT '',
                    created_by  TEXT NOT NULL DEFAULT 'ui',
                    error_msg   TEXT,
                    created_at  INTEGER NOT NULL DEFAULT 0,
                    started_at  INTEGER,
                    finished_at INTEGER
                )
                """
            )
            # crawler_item：每个 URL 一条
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crawler_item (
                    id              TEXT PRIMARY KEY,
                    job_id          TEXT NOT NULL,
                    topic_id        TEXT NOT NULL DEFAULT '',
                    source_url      TEXT NOT NULL,
                    source_type     TEXT NOT NULL DEFAULT 'web',
                    fetched_status  TEXT NOT NULL DEFAULT 'pending',
                    promoted_status TEXT NOT NULL DEFAULT 'pending',
                    title           TEXT NOT NULL DEFAULT '',
                    author          TEXT NOT NULL DEFAULT '',
                    domain          TEXT NOT NULL DEFAULT '',
                    content_type    TEXT NOT NULL DEFAULT '',
                    size_bytes      INTEGER NOT NULL DEFAULT 0,
                    content         TEXT NOT NULL DEFAULT '',
                    error_msg       TEXT,
                    created_at      INTEGER NOT NULL DEFAULT 0,
                    fetched_at      INTEGER,
                    promoted_at     INTEGER
                )
                """
            )
            # 建立常用索引（加速 UI 查询）
            # 按 topic_id 列 job
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crawler_job_topic_id
                ON crawler_job (topic_id)
                """
            )
            # 按 job_id 列 item
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crawler_item_job_id
                ON crawler_item (job_id)
                """
            )
            # 按 (topic_id, promoted_status) 过滤已发布文档
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crawler_item_topic_promoted
                ON crawler_item (topic_id, promoted_status)
                """
            )
            # 按 (source_url, job_id) 做去重（可选）
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crawler_item_url_job
                ON crawler_item (source_url, job_id)
                """
            )
            # 全部 DDL 完成后提交
            self._conn.commit()

    # 执行单条写 SQL（INSERT/UPDATE/DELETE），自动提交 + 失败回滚
    # 参数 sql：SQL 字符串（可用 ? 占位符）
    # 参数 params：参数元组
    # 返回 int：影响行数
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        # 加锁串行化
        with self._lock:
            try:
                # 执行 SQL
                cur = self._conn.execute(sql, params)
                # 提交
                self._conn.commit()
                # 返回影响行数
                return cur.rowcount if cur.rowcount is not None else 0
            except Exception:
                # 执行失败：先回滚（避免后续写入被打开的事务卡住）
                try:
                    # rollback
                    self._conn.rollback()
                except Exception:
                    # rollback 失败忽略
                    pass
                # 把异常向上抛（调用方 try/except 决定是否记日志）
                raise

    # 执行 executemany（批量写），自动提交 + 回滚
    # 参数 sql：SQL 字符串
    # 参数 seq_of_params：参数序列（list[tuple]）
    # 返回 int：影响行数（部分执行失败返回 -1，回滚整批）
    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]) -> int:
        # 加锁
        with self._lock:
            try:
                # 执行批量写
                cur = self._conn.executemany(sql, seq_of_params)
                # 提交
                self._conn.commit()
                # 返回影响行数
                return cur.rowcount if cur.rowcount is not None else 0
            except Exception:
                # 失败回滚整批
                try:
                    # rollback
                    self._conn.rollback()
                except Exception:
                    # rollback 失败忽略
                    pass
                # 向上抛
                raise

    # 执行查询 SQL，拿首行（无结果返回 None）
    # 参数 sql：SELECT 语句
    # 参数 params：参数元组
    # 返回 Optional[dict]：Row 转 dict，没结果 None
    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict[str, Any]]:
        # 加锁
        with self._lock:
            try:
                # 执行 select
                cur = self._conn.execute(sql, params)
                # 取第一行
                row = cur.fetchone()
            except Exception:
                # 失败向上抛
                raise
        # row 空 → None
        if row is None:
            return None
        # 转 dict（sqlite3.Row 本身是映射，但上层 isinstance(dict) 判断可能 False，所以明确转 dict）
        return {k: row[k] for k in row.keys()}

    # 执行查询 SQL，拿所有行（空结果返回 []）
    # 参数 sql：SELECT 语句
    # 参数 params：参数元组
    # 返回 list[dict]：每行一个 dict
    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        # 加锁
        with self._lock:
            try:
                # 执行
                cur = self._conn.execute(sql, params)
                # 全部行
                rows = cur.fetchall()
            except Exception:
                # 失败抛出
                raise
        # 每行转 dict
        out: list[dict[str, Any]] = []
        # 遍历
        for row in rows:
            # Row → dict
            out.append({k: row[k] for k in row.keys()})
        # 返回列表
        return out

    # 关闭 SQLite 连接（幂等）
    # 返回 None
    def close(self) -> None:
        # 加锁
        with self._lock:
            # 连接还开着
            if self._conn is not None:
                try:
                    # 关闭
                    self._conn.close()
                except Exception:
                    # 关失败忽略
                    pass
                # 置 None，防重复关
                self._conn = None
