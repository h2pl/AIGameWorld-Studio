"""AuditRepository — kb_audit 数据访问.

对标主项目 backend/src/repository/ 的分层契约：一个领域实体一个 repo 文件。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..utils.sqlite_store import SQLiteStore

_log = logging.getLogger(__name__)


class AuditRepository:
    """kb_audit 数据访问，建立在 SQLiteStore 之上.

    用于记录导入/检索/任务等关键操作的审计轨迹，便于问题排查与合规追溯。
    """

    def __init__(self, store: SQLiteStore):
        # 注入底层 SQLiteStore，复用其 execute/fetch 能力
        self._store = store

    def audit(
        self,
        op: str,
        *,
        actor: str = "system",
        topic_slug: str | None = None,
        document_id: str | None = None,
        job_id: str | None = None,
        query_text: str | None = None,
        top_k: int | None = None,
        filters: dict | None = None,
        result_summary: Any = None,
        error: str | None = None,
    ) -> None:
        try:
            # 生成审计记录主键与时间戳
            audit_id = SQLiteStore.new_id()
            now_str = SQLiteStore.now_str()
            # 将结构化字段序列化为 JSON 文本存储
            filters_json = json.dumps(filters, ensure_ascii=False) if filters else None
            result_json = json.dumps(result_summary, ensure_ascii=False) if result_summary else None
            self._store.execute(
                """
                INSERT INTO kb_audit
                    (id, op, actor, topic_id, document_id, job_id,
                     query_text, top_k, filters_json, result_json, error_msg, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    op,
                    actor,
                    topic_slug,
                    document_id,
                    job_id,
                    query_text,
                    top_k,
                    filters_json,
                    result_json,
                    error,
                    now_str,
                ),
            )
        except Exception:
            # 审计写入失败不应阻断主流程，仅记录异常
            _log.exception("audit write failed: op=%s", op)

    def audit_list(self, topic_slug: str, *, limit: int = 100) -> list[dict]:
        # 按主题查询最近的审计记录（按时间倒序）
        return self._store.fetch_all(
            """
            SELECT id, op, actor, topic_id, document_id, job_id,
                   query_text, top_k, filters_json, result_json, error_msg, created_at
              FROM kb_audit
             WHERE topic_id = ?
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (topic_slug, limit),
        )
