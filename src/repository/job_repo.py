"""JobRepository — kb_index_job 数据访问.

对标主项目 backend/src/repository/ 的分层契约：一个领域实体一个 repo 文件。
"""

from __future__ import annotations

from typing import Any

from ..utils.sqlite_store import SQLiteStore


class JobRepository:
    """kb_index_job 数据访问，建立在 SQLiteStore 之上."""

    def __init__(self, store: SQLiteStore):
        # 注入底层 SQLiteStore
        self._store = store

    def has_running_job(self, topic_slug: str) -> str | None:
        # 查询该主题是否已有 pending/running 任务，避免并发重复导入
        row = self._store.fetch_one(
            "SELECT id FROM kb_index_job WHERE topic_id = ? AND status IN ('pending', 'running') LIMIT 1",
            (topic_slug,),
        )
        return row["id"] if row else None

    def create_index_job(self, *, job_id: str, topic_slug: str, mode: str, created_by: str) -> None:
        # 创建导入任务（初始 pending，document_id 留空表示整库导入）
        self._store.execute(
            """
            INSERT INTO kb_index_job
                (id, topic_id, document_id, mode, status, progress, created_by, created_at)
            VALUES (?, ?, NULL, ?, 'pending', 0, ?, ?)
            """,
            (job_id, topic_slug, mode, created_by, SQLiteStore.now_str()),
        )

    def update_index_job(
        self,
        job_id: str,
        status: str,
        *,
        started_at: str | None = None,
        finished_at: str | None = None,
        file_total: int | None = None,
        file_done: int | None = None,
        chunk_total: int | None = None,
        progress: int | None = None,
        error_msg: str | None = None,
    ) -> None:
        # 动态拼装需要更新的字段（仅更新传入的非空字段）
        set_parts: list[str] = ["status = ?"]
        params: list[Any] = [status]
        if started_at is not None:
            set_parts.append("started_at = ?")
            params.append(started_at)
        if finished_at is not None:
            set_parts.append("finished_at = ?")
            params.append(finished_at)
        if file_total is not None:
            set_parts.append("file_total = ?")
            params.append(file_total)
        if file_done is not None:
            set_parts.append("file_done = ?")
            params.append(file_done)
        if chunk_total is not None:
            set_parts.append("chunk_total = ?")
            params.append(chunk_total)
        if progress is not None:
            set_parts.append("progress = ?")
            params.append(progress)
        if error_msg is not None:
            set_parts.append("error_msg = ?")
            params.append(error_msg)
        params.append(job_id)
        # 执行更新（set_parts 由上面动态决定）
        self._store.execute(f"UPDATE kb_index_job SET {', '.join(set_parts)} WHERE id = ?", tuple(params))

    def job_list(self, topic_slug: str, *, limit: int = 50) -> list[dict]:
        # 按主题查询最近的导入任务列表（时间倒序）
        return self._store.fetch_all(
            """
            SELECT id, topic_id, mode, status, progress,
                   file_total, file_done, chunk_total, error_msg,
                   started_at, finished_at, created_at
              FROM kb_index_job
             WHERE topic_id = ?
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (topic_slug, limit),
        )
