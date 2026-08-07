"""DocumentRepository — kb_document / kb_chunk 数据访问.

对标主项目 backend/src/repository/ 的分层契约：一个领域实体一个 repo 文件。
文档与分块强耦合（chunk 总是挂在 document 下），故放在同一 repo。
"""

from __future__ import annotations

import json
from typing import Any

from ..utils.sqlite_store import SQLiteStore


class DocumentRepository:
    """kb_document / kb_chunk 数据访问，建立在 SQLiteStore 之上."""

    def __init__(self, store: SQLiteStore):
        self._store = store

    # ── 表结构兼容（幂等补列） ────────────────────────────────────
    def ensure_kb_chunk_page_column(self) -> None:
        """幂等给 kb_chunk 加 page_number 列（SQLite 不支持 ADD COLUMN IF NOT EXISTS）."""
        try:
            self._store.execute("ALTER TABLE kb_chunk ADD COLUMN page_number INTEGER")
        except Exception:
            pass

    # ── kb_document ───────────────────────────────────────────────
    def insert_document(
        self,
        *,
        doc_id: str,
        topic_id: str,
        title: str,
        source_type: str,
        file_name: str,
        file_path: str,
        file_size: int,
        sha256: str,
        content_type: str,
        created_by: str,
    ) -> None:
        # 插入文档主记录（version=1, status='uploaded'）
        self._store.execute(
            """
            INSERT INTO kb_document
                (id, topic_id, title, source_type, file_name, file_path,
                 file_size, sha256, content_type, version, status,
                 created_by, related_packs, tags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'uploaded', ?, '[]', '[]')
            """,
            (
                doc_id,
                topic_id,
                title,
                source_type,
                file_name,
                file_path,
                file_size,
                sha256,
                content_type,
                created_by,
            ),
        )

    def document_list(
        self,
        topic_slug: str,
        *,
        limit: int = 200,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[dict]:
        # 组装查询条件（默认排除已删除文档）
        where = "WHERE topic_id = ?"
        params: list[Any] = [topic_slug]
        if not include_deleted:
            where += " AND status != 'deleted'"

        rows = self._store.fetch_all(
            f"""
            SELECT d.id, d.topic_id AS topic, d.title, d.source_type, d.content_type,
                   d.file_name, d.file_path, d.file_size, d.version, d.status,
                   d.tags_json, d.created_at, d.updated_at, d.ext_json,
                   (SELECT COUNT(*) FROM kb_chunk c WHERE c.document_id = d.id) AS chunk_count
              FROM kb_document d
             {where}
             ORDER BY d.updated_at DESC
             LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        )
        return [_parse_doc_row(r) for r in rows]

    def soft_delete_document(self, doc_id: str, topic_slug: str, now_str: str) -> None:
        # 事务内：标记文档删除并清理其下分块
        with self._store.transaction():
            self._store.execute(
                """
                UPDATE kb_document
                   SET status = 'deleted', updated_at = ?, deleted_at = ?
                 WHERE id = ? AND topic_id = ?
                """,
                (now_str, now_str, doc_id, topic_slug),
            )
            self._store.execute("DELETE FROM kb_chunk WHERE document_id = ?", (doc_id,))

    def chunk_ids_of_document(self, doc_id: str, topic_slug: str) -> list[str]:
        # 查询文档下所有分块 id（用于批量删除向量）
        rows = self._store.fetch_all(
            "SELECT id FROM kb_chunk WHERE document_id = ? AND topic_id = ?",
            (doc_id, topic_slug),
        )
        return [r["id"] for r in rows]

    def get_document_meta(
        self, topic_slug: str, file_path: str, *, status: str = "done", sha256: str | None = None
    ) -> dict | None:
        """查 kb_document（按 topic + file_path），可选版本/哈希过滤，用于文件级去重."""
        # 优先按 sha256 精确去重，否则仅按路径匹配
        if sha256 is not None:
            return self._store.fetch_one(
                """
                SELECT id FROM kb_document
                 WHERE topic_id = ? AND file_path = ? AND status = ? AND sha256 = ?
                 ORDER BY version DESC LIMIT 1
                """,
                (topic_slug, str(file_path), status, sha256),
            )
        return self._store.fetch_one(
            """
            SELECT id, status
              FROM kb_document
             WHERE topic_id = ? AND file_path = ? AND status = ?
             ORDER BY version DESC LIMIT 1
            """,
            (topic_slug, str(file_path), status),
        )

    def done_sha256_set(self, topic_slug: str) -> set[str]:
        """返回该 topic 已 done 文档的 sha256 集合，供文件级 ingest 去重."""
        # 汇总已成功导入文档的哈希
        rows = self._store.fetch_all(
            "SELECT sha256 FROM kb_document WHERE topic_id = ? AND status = 'done' AND sha256 IS NOT NULL",
            (topic_slug,),
        )
        return {r["sha256"] for r in rows}

    # ── kb_chunk 计数 / 文本兜底 ──────────────────────────────────
    def count_chunks(self, topic_slug: str) -> int:
        # 统计主题下总分块数
        row = self._store.fetch_one("SELECT COUNT(*) c FROM kb_chunk WHERE topic_id = ?", (topic_slug,))
        return int(row["c"]) if row else 0

    def chunk_text_preview(self, chunk_id: str) -> str:
        # 取分块文本预览（向量缺失时的文本兜底）
        row = self._store.fetch_one("SELECT text_preview FROM kb_chunk WHERE id = ?", (chunk_id,))
        return (row or {}).get("text_preview") or ""

    def delete_chunks_by_document(self, doc_id: str) -> None:
        # 按文档删除分块（物理删除）
        self._store.execute("DELETE FROM kb_chunk WHERE document_id = ?", (doc_id,))

    def soft_delete_documents_by_topic(self, topic_slug: str, now_str: str) -> int:
        # 标记该主题全部文档为已删除
        return self._store.execute(
            """
            UPDATE kb_document
               SET status     = 'deleted',
                   updated_at = ?,
                   deleted_at = ?
             WHERE topic_id   = ?
               AND status    != 'deleted'
            """,
            (now_str, now_str, topic_slug),
        )

    def delete_chunks_by_topic(self, topic_slug: str) -> None:
        # 清空主题下所有分块（重建索引前调用）
        self._store.execute("DELETE FROM kb_chunk WHERE topic_id = ?", (topic_slug,))

    # ── 跨表聚合统计（原本内联在 service 层，下沉到 repo 符合分层契约） ──
    def doc_stats(self, topic_slug: str) -> dict:
        """知识库聚合统计：文档数 / 分块数 / 按来源类型分布 / 成功任务数."""
        # 聚合分块数、文档数、来源分布与成功任务数
        total_chunks = self.count_chunks(topic_slug)
        doc_count = self._store.fetch_one(
            "SELECT COUNT(*) c FROM kb_document WHERE topic_id = ? AND status != 'deleted'",
            (topic_slug,),
        )["c"]
        by_type_rows = self._store.fetch_all(
            """
            SELECT d.source_type, COUNT(*) c
              FROM kb_chunk c
              JOIN kb_document d ON c.document_id = d.id
             WHERE c.topic_id = ?
             GROUP BY d.source_type
            """,
            (topic_slug,),
        )
        successful_jobs = self._store.fetch_one(
            "SELECT COUNT(*) c FROM kb_index_job WHERE topic_id = ? AND status = 'done'",
            (topic_slug,),
        )["c"]
        return {
            "total_chunks": total_chunks,
            "documents": int(doc_count),
            "by_source_type": {r["source_type"] or "unknown": r["c"] for r in by_type_rows},
            "successful_jobs": int(successful_jobs),
        }

    def doc_status_detail(self, topic_slug: str) -> dict:
        """CLI status 子命令所需的聚合明细（done / 其它 / 删除 文档数 + 分块 + 最近文档）."""
        # 分别统计 done / 其它 / 删除 文档数
        done = self._store.fetch_one(
            "SELECT COUNT(*) c FROM kb_document WHERE topic_id = ? AND status = 'done'",
            (topic_slug,),
        )["c"]
        deleted = self._store.fetch_one(
            "SELECT COUNT(*) c FROM kb_document WHERE topic_id = ? AND status = 'deleted'",
            (topic_slug,),
        )["c"]
        parsing = self._store.fetch_one(
            "SELECT COUNT(*) c FROM kb_document WHERE topic_id = ? AND status NOT IN ('done','deleted')",
            (topic_slug,),
        )["c"]
        chunks = self.count_chunks(topic_slug)
        recent_docs: list[dict] = []
        if int(done) > 0:
            rows = self._store.fetch_all(
                """
                SELECT id, version, title, file_name, file_size, updated_at
                  FROM kb_document
                 WHERE topic_id = ? AND status = 'done'
                 ORDER BY updated_at DESC
                 LIMIT 10
                """,
                (topic_slug,),
            )
            for r in rows:
                recent_docs.append(
                    {
                        "id": r["id"],
                        "version": r["version"],
                        "title": r["title"],
                        "file_name": r["file_name"],
                        "file_size_kb": int((r["file_size"] or 0) / 1024),
                    }
                )
        return {
            "documents_done": int(done),
            "documents_other": int(parsing),
            "documents_deleted": int(deleted),
            "chunks_total": chunks,
            "recent_docs": recent_docs,
        }


def _parse_doc_row(r: dict) -> dict:
    d = dict(r)
    # 解析 JSON 字段，失败降级为空集合
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
