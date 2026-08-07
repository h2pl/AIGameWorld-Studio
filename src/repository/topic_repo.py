"""TopicRepository — knowledge_topic 数据访问.

对标主项目 backend/src/repository/ 的分层契约：一个领域实体一个 repo 文件。
"""

from __future__ import annotations

import json
from typing import Any

from ..utils.sqlite_store import SQLiteStore


class TopicRepository:
    """knowledge_topic 数据访问，建立在 SQLiteStore 之上."""

    def __init__(self, store: SQLiteStore):
        # 注入底层 SQLiteStore
        self._store = store

    def topic_exists(self, topic_slug: str) -> bool:
        # 判断主题是否已存在（按 topic slug 唯一键）
        row = self._store.fetch_one("SELECT topic FROM knowledge_topic WHERE topic = ?", (topic_slug,))
        return row is not None

    def upsert_topic(
        self,
        *,
        topic_slug: str,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        created_by: str = "ui",
    ) -> None:
        # 序列化标签为 JSON，并取当前时间
        display_name = name or topic_slug
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        now_str = SQLiteStore.now_str()

        if self.topic_exists(topic_slug):
            set_parts: list[str] = []
            params: list[Any] = []
            if name is not None:
                set_parts.append("name = ?")
                params.append(display_name)
            if description is not None:
                set_parts.append("description = ?")
                params.append(description)
            if tags is not None:
                set_parts.append("tags_json = ?")
                params.append(tags_json)
            if status is not None:
                set_parts.append("status = ?")
                params.append(status)
            if set_parts:
                set_parts.append("updated_at = ?")
                params.append(now_str)
                params.append(topic_slug)
                self._store.execute(
                    f"UPDATE knowledge_topic SET {', '.join(set_parts)} WHERE topic = ?",
                    tuple(params),
                )
        else:
            self._store.execute(
                """
                INSERT INTO knowledge_topic
                    (id, topic, name, description, tags_json, status, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    SQLiteStore.new_id(),
                    topic_slug,
                    display_name,
                    description or "",
                    tags_json,
                    status or "active",
                    created_by,
                    now_str,
                    now_str,
                ),
            )

    def get_topic(self, topic_slug: str) -> dict | None:
        # 查询单个主题，并聚合其分块数
        row = self._store.fetch_one(
            """
            SELECT t.id, t.topic, t.name, t.description, t.tags_json, t.status,
                   t.created_at, t.updated_at, t.ext_json,
                   (SELECT COUNT(*) FROM kb_chunk c WHERE c.topic_id = t.topic) AS chunks_in_collection
              FROM knowledge_topic t
             WHERE t.topic = ?
            """,
            (topic_slug,),
        )
        return _parse_topic_row(row) if row else None

    def list_topics(self, *, include_archived: bool = False) -> list[dict]:
        # 默认排除 archived 主题；返回时附带各主题分块数
        where = "" if include_archived else "WHERE status != 'archived'"
        rows = self._store.fetch_all(
            f"""
            SELECT t.id, t.topic, t.name, t.description, t.tags_json, t.status,
                   t.created_at, t.updated_at, t.ext_json,
                   (SELECT COUNT(*) FROM kb_chunk c WHERE c.topic_id = t.topic) AS chunks_in_collection
              FROM knowledge_topic t
             {where}
             ORDER BY t.updated_at DESC
            """
        )
        return [_parse_topic_row(r) for r in rows]

    def delete_topic(self, topic_slug: str) -> int:
        # 物理删除主题（调用方需先清理其下文档/分块）
        return self._store.execute("DELETE FROM knowledge_topic WHERE topic = ?", (topic_slug,))


def _parse_topic_row(r: dict) -> dict:
    d = dict(r)
    # 解析 JSON 字段为 Python 对象，解析失败降级为空值
    try:
        d["tags"] = json.loads(d.get("tags_json") or "[]")
    except Exception:
        d["tags"] = []
    d.pop("tags_json", None)
    try:
        d["ext_json"] = json.loads(d.get("ext_json") or "{}")
    except Exception:
        d["ext_json"] = {}
    return d
