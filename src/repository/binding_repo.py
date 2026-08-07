"""BindingRepository — world_topic_binding 数据访问.

对标主项目 backend/src/repository/ 的分层契约：一个领域实体一个 repo 文件。
"""

from __future__ import annotations

from ..utils.sqlite_store import SQLiteStore


class BindingRepository:
    """world_topic_binding 数据访问，建立在 SQLiteStore 之上."""

    def __init__(self, store: SQLiteStore):
        # 注入底层 SQLiteStore
        self._store = store

    def list_bindings(self, *, topic_slug: str | None = None) -> list[dict]:
        # 按主题过滤（不传则列出全部），按优先级倒序
        if topic_slug:
            return self._store.fetch_all(
                "SELECT world_id, topic_id AS topic, priority FROM world_topic_binding"
                " WHERE topic_id = ? ORDER BY priority DESC",
                (topic_slug,),
            )
        return self._store.fetch_all(
            "SELECT world_id, topic_id AS topic, priority FROM world_topic_binding ORDER BY priority DESC"
        )

    def get_world_binding(self, world_id: str) -> dict | None:
        # 查询某个世界当前绑定的知识库主题
        return self._store.fetch_one(
            "SELECT world_id, topic_id AS topic, priority FROM world_topic_binding WHERE world_id = ?",
            (world_id,),
        )

    def bind_world_topic(self, world_id: str, topic_slug: str, *, priority: int = 0) -> int:
        # 先解绑该世界已有绑定，再写入新的（保证一对一）
        removed = self._store.execute("DELETE FROM world_topic_binding WHERE world_id = ?", (world_id,))
        self._store.execute(
            """
            INSERT INTO world_topic_binding (id, world_id, topic_id, priority)
            VALUES (?, ?, ?, ?)
            """,
            (SQLiteStore.new_id(), world_id, topic_slug, priority),
        )
        return removed

    def unbind_world(self, world_id: str) -> int:
        # 解除世界与知识库主题的绑定
        return self._store.execute("DELETE FROM world_topic_binding WHERE world_id = ?", (world_id,))
