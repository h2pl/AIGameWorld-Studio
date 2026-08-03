"""KnowledgeManager — 知识库统一管理门面（组合 KnowledgePipeline + KBVectorStoreFactory + SQLiteStore）.

对外暴露 :class:`KnowledgeManager`，是 API/UI 层的唯一入口：
- 上传文件 / 粘贴文本 → 保存到 knowledge 目录 + 登记 kb_document
- 触发索引 → 调 KnowledgePipeline.index_directory（支持异步 BackgroundTasks）
- 语义检索 → BGE-M3 embedding + Qdrant dense search
- 文档/主题/绑定 CRUD → SQLite 正式表
  (kb_document / kb_chunk / knowledge_topic / world_topic_binding / kb_index_job / kb_audit_log)
- 统计 / 清空 / 审计查询
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from ...utils.sqlite_store import SQLiteStore
from .pipeline import KnowledgePipeline
from .reader import KnowledgeReader
from .vector_store import KBVectorStoreFactory

_log = logging.getLogger(__name__)

# 默认知识库文件根目录（相对于 project_root）
_KB_DATA_DIR = "knowledge-bases"

# 预置主题（deps.py bootstrap_default_topics=True 时自动创建）
_DEFAULT_TOPICS = [
    {"topic_id": "genshin", "name": "原神", "description": "原神世界观与设定集"},
    {"topic_id": "wow_worldview", "name": "魔兽世界", "description": "魔兽世界编年史与官方设定集"},
]


class KnowledgeManager:
    """知识库统一管理门面 — 组合 Pipeline + Factory + Store + Reader."""

    def __init__(
        self,
        *,
        factory: KBVectorStoreFactory | None = None,
        store: SQLiteStore | None = None,
        project_root: Path | str | None = None,
        auto_init_schema: bool = True,
        created_by: str = "ui",
        bootstrap_default_topics: bool = False,
        chunk_size: int = 800,
        chunk_overlap: int = 120,
    ):
        self._project_root = Path(project_root or Path.cwd()).resolve()
        self._store = store or SQLiteStore(self._project_root / "data" / "studio.db")
        self._factory = factory or KBVectorStoreFactory.get_default(project_root=self._project_root)
        self._created_by = created_by
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._reader = KnowledgeReader()
        self._readers: dict[str, KnowledgeReader] = {}  # topic → 带 enrichers 的 reader
        self._pipelines: dict[str, KnowledgePipeline] = {}

        if auto_init_schema:
            self._store.init_schema(self._project_root / "migrations")
        if bootstrap_default_topics:
            self._bootstrap_default_topics()

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _get_reader(self, topic_id: str) -> KnowledgeReader:
        """获取/缓存指定 topic 的 KnowledgeReader（带主题特定的 enrichers）."""
        if topic_id not in self._readers:
            from .enrichers import get_enrichers_for_topic

            self._readers[topic_id] = KnowledgeReader(enrichers=get_enrichers_for_topic(topic_id))
        return self._readers[topic_id]

    def _get_pipeline(self, topic_id: str) -> KnowledgePipeline:
        """获取/缓存指定 topic 的 KnowledgePipeline 实例."""
        if topic_id not in self._pipelines:
            self._pipelines[topic_id] = KnowledgePipeline(
                topic_id,
                factory=self._factory,
                store=self._store,
                chunk_size=self._chunk_size,
                chunk_overlap=self._chunk_overlap,
                enrichers=self._get_reader(topic_id)._enrichers,
            )
        return self._pipelines[topic_id]

    def _knowledge_dir(self, topic_id: str) -> Path:
        """返回知识库文件目录: project_root/knowledge-bases/{topic_id}/knowledge."""
        return self._project_root / _KB_DATA_DIR / topic_id / "knowledge"

    def _ensure_knowledge_dir(self, topic_id: str) -> Path:
        """确保知识库目录骨架存在 (lore/documents/images/videos)."""
        kdir = self._knowledge_dir(topic_id)
        for sub in ("lore", "documents", "images", "videos"):
            (kdir / sub).mkdir(parents=True, exist_ok=True)
        return kdir

    def _audit(
        self,
        op: str,
        *,
        topic_id: str | None = None,
        document_id: str | None = None,
        job_id: str | None = None,
        query_text: str | None = None,
        top_k: int | None = None,
        filters: dict | None = None,
        result_summary: Any = None,
        error: str | None = None,
    ) -> None:
        """写审计日志到 kb_audit_log 表."""
        try:
            audit_id = SQLiteStore.new_id()
            now_str = SQLiteStore.now_str()
            filters_json = json.dumps(filters, ensure_ascii=False) if filters else None
            result_json = json.dumps(result_summary, ensure_ascii=False) if result_summary else None
            self._store.execute(
                """
                INSERT INTO kb_audit_log
                    (id, op, actor, topic_id, document_id, job_id,
                     query_text, top_k, filters_json, result_json, error_msg, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    op,
                    self._created_by,
                    topic_id,
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
            _log.exception("audit write failed: op=%s", op)

    def _qdrant_points_count(self, collection: str) -> int | None:
        """通过 Qdrant HTTP 查 points_count."""
        try:
            import urllib.request as u

            raw = u.urlopen(f"{self._factory.qdrant_url}/collections/{collection}", timeout=8).read()
            return json.loads(raw)["result"].get("points_count")
        except Exception:
            return None

    def _qdrant_delete_points(self, collection: str, point_ids: list[str]) -> bool:
        """通过 Qdrant HTTP 批量删 points."""
        if not point_ids:
            return True
        try:
            import urllib.request as u

            url = f"{self._factory.qdrant_url}/collections/{collection}/points/delete"
            body = json.dumps({"ids": point_ids}, ensure_ascii=False).encode("utf-8")
            req = u.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with u.urlopen(req, timeout=15) as resp:
                resp.read()
            return True
        except Exception:
            _log.exception("Qdrant delete points failed: collection=%s count=%d", collection, len(point_ids))
            return False

    def _sanitize_filename(self, name: str) -> str:
        """Windows 文件名安全化：转义特殊字符."""
        for ch in r'<>:"/\|?*':
            name = name.replace(ch, "_")
        return name

    # ------------------------------------------------------------------
    # 上传
    # ------------------------------------------------------------------

    def save_uploaded_bytes(
        self,
        topic_id: str,
        data: bytes,
        source_type: str,
        filename: str,
        *,
        prefix: str = "",
    ) -> dict[str, Any]:
        """保存上传的文件字节流到 knowledge 目录 + 登记 kb_document."""
        kdir = self._ensure_knowledge_dir(topic_id)
        dest_dir = kdir / source_type
        if prefix:
            dest_dir = dest_dir / prefix
        dest_dir.mkdir(parents=True, exist_ok=True)

        safe_name = self._sanitize_filename(filename)
        target = dest_dir / safe_name

        # 同名文件追加序号
        if target.exists():
            stem, suffix = target.stem, target.suffix
            idx = 1
            while target.exists():
                target = dest_dir / f"{stem}-{idx}{suffix}"
                idx += 1

        target.write_bytes(data)

        sha256 = hashlib.sha256(data).hexdigest()
        file_size = len(data)
        content_type = Path(filename).suffix.lstrip(".") or "binary"

        # 在 kb_document 表 INSERT 元数据
        doc_id = SQLiteStore.new_id()
        try:
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
                    Path(filename).stem,
                    source_type,
                    safe_name,
                    str(target),
                    file_size,
                    sha256,
                    content_type,
                    self._created_by,
                ),
            )
        except Exception:
            _log.exception("save_uploaded_bytes: kb_document INSERT failed")

        return {"ok": True, "file_path": str(target), "doc_id": doc_id, "file_name": safe_name}

    def save_uploaded_text(
        self,
        topic_id: str,
        text: str,
        source_type: str,
        *,
        file_name: str = "pasted.md",
        prefix: str = "",
    ) -> dict[str, Any]:
        """保存粘贴的文本到 knowledge 目录 + 登记 kb_document."""
        data = text.encode("utf-8")
        return self.save_uploaded_bytes(topic_id, data, source_type, file_name, prefix=prefix)

    # ------------------------------------------------------------------
    # 文档 CRUD
    # ------------------------------------------------------------------

    def document_list(
        self,
        topic_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[dict]:
        """分页返回 kb_document 列表（含每个文档的 chunk_count）."""
        where = "WHERE topic_id = ?"
        params: list[Any] = [topic_id]
        if not include_deleted:
            where += " AND status != 'deleted'"

        rows = self._store.fetch_all(
            f"""
            SELECT d.id, d.topic_id, d.title, d.source_type, d.content_type,
                   d.file_name, d.file_path, d.file_size, d.version, d.status,
                   d.tags_json, d.created_at, d.updated_at,
                   (SELECT COUNT(*) FROM kb_chunk c WHERE c.document_id = d.id) AS chunk_count
              FROM kb_document d
             {where}
             ORDER BY d.updated_at DESC
             LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        )

        result = []
        for r in rows:
            d = dict(r)
            # 解析 tags_json → tags list
            try:
                d["tags"] = json.loads(d.get("tags_json") or "[]")
            except Exception:
                d["tags"] = []
            d.pop("tags_json", None)
            result.append(d)
        return result

    def document_delete(self, topic_id: str, doc_id: str) -> dict[str, Any]:
        """软删文档：kb_document.status='deleted' + 真删 kb_chunk + Qdrant 删 points."""
        # 查出该文档的所有 chunk_id
        rows = self._store.fetch_all(
            "SELECT id FROM kb_chunk WHERE document_id = ? AND topic_id = ?",
            (doc_id, topic_id),
        )
        chunk_ids = [r["id"] for r in rows]

        now_str = SQLiteStore.now_str()
        with self._store.transaction():
            self._store.execute(
                """
                UPDATE kb_document
                   SET status = 'deleted', updated_at = ?, deleted_at = ?
                 WHERE id = ? AND topic_id = ?
                """,
                (now_str, now_str, doc_id, topic_id),
            )
            self._store.execute(
                "DELETE FROM kb_chunk WHERE document_id = ?",
                (doc_id,),
            )

        # Qdrant 删 points
        collection = f"kb_{topic_id}"
        self._qdrant_delete_points(collection, chunk_ids)

        self._audit("doc_delete", topic_id=topic_id, document_id=doc_id)
        return {"ok": True, "soft_deleted": 1, "deleted_chunk_ids_count": len(chunk_ids)}

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def search_with_meta(
        self,
        topic_id: str,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.0,
        filters: dict | None = None,
    ) -> list[dict]:
        """语义检索：BGE-M3 embedding → Qdrant dense search → 补 text → 审计."""
        pipeline = self._get_pipeline(topic_id)

        # 生成 query embedding
        qvec = pipeline._embedding_model.get_query_embedding(query)

        # Qdrant HTTP search
        collection = f"kb_{topic_id}"
        url = f"{self._factory.qdrant_url}/collections/{collection}/points/search"
        body = json.dumps(
            {
                "vector": qvec,
                "limit": top_k,
                "with_payload": True,
                "with_vectors": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        import urllib.request as u

        req = u.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with u.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
        hits_data = json.loads(raw).get("result") or []

        hits = []
        for h in hits_data:
            score = float(h.get("score") or 0.0)
            if min_score > 0 and score < min_score:
                continue
            meta = h.get("payload") or {}
            chunk_id = str(h.get("id") or "")

            # 提取 text：优先 payload 里的 text/content，其次 _node_content JSON
            text = ""
            for k in ("text", "content"):
                if k in meta and isinstance(meta[k], str) and meta[k].strip():
                    text = meta[k]
                    break
            if not text and "_node_content" in meta and isinstance(meta["_node_content"], str):
                try:
                    nc = json.loads(meta["_node_content"])
                    if isinstance(nc, dict):
                        for kk in ("text", "__text__", "content"):
                            if kk in nc and isinstance(nc[kk], str) and nc[kk].strip():
                                text = nc[kk]
                                break
                except Exception:
                    pass
            # 兜底：SQLite text_preview
            if not text and chunk_id:
                row = self._store.fetch_one(
                    "SELECT text_preview FROM kb_chunk WHERE id = ?",
                    (chunk_id,),
                )
                text = (row or {}).get("text_preview") or ""

            hits.append(
                {
                    "text": text,
                    "score_cosine_sim": score,
                    "distance": 1.0 - score,
                    "metadata": meta,
                }
            )

        self._audit(
            "retrieve",
            topic_id=topic_id,
            query_text=query,
            top_k=top_k,
            filters=filters,
            result_summary=[{"score": h["score_cosine_sim"]} for h in hits[:5]],
        )
        return hits

    # ------------------------------------------------------------------
    # 索引
    # ------------------------------------------------------------------

    def index(self, topic_id: str, *, force: bool = False) -> dict[str, Any]:
        """同步触发索引（阻塞）— 直接调 pipeline.index_directory."""
        pipeline = self._get_pipeline(topic_id)
        knowledge_dir = self._knowledge_dir(topic_id)
        if not knowledge_dir.exists():
            self._ensure_knowledge_dir(topic_id)

        mode = "force" if force else "incremental"
        try:
            result = pipeline.index_directory(knowledge_dir, mode=mode, created_by=self._created_by)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._audit("index_done", topic_id=topic_id, error=error)
            return {"ok": False, "error": error, "total_chunks": 0}

        total_chunks = result.get("chunks", 0)
        self._audit(
            "index_done",
            topic_id=topic_id,
            job_id=result.get("job_id"),
            result_summary=result,
        )
        return {"ok": True, "total_chunks": total_chunks, "indexed": result}

    def index_async(self, topic_id: str, *, force: bool = False) -> dict[str, Any]:
        """创建索引任务记录并返回 job_id（不执行，由 BackgroundTasks 调 _run_index_job）."""
        mode = "force" if force else "incremental"
        # 先确保 knowledge 目录存在
        self._ensure_knowledge_dir(topic_id)
        # 创建 job 记录（pipeline 内部的 _create_job 需要 store）
        job_id = ""
        try:
            job_id = SQLiteStore.new_id()
            now_str = SQLiteStore.now_str()
            self._store.execute(
                """
                INSERT INTO kb_index_job
                    (id, topic_id, document_id, mode, status, progress, created_by, created_at)
                VALUES (?, ?, NULL, ?, 'pending', 0, ?, ?)
                """,
                (job_id, topic_id, mode, self._created_by, now_str),
            )
            self._audit("index_start", topic_id=topic_id, job_id=job_id)
        except Exception:
            _log.exception("index_async: create job failed")

        return {"ok": True, "topic_id": topic_id, "job_id": job_id, "status": "pending"}

    def _run_index_job(self, topic_id: str, job_id: str, *, force: bool = False) -> None:
        """后台线程执行索引（由 FastAPI BackgroundTasks 调度）."""
        try:
            # 更新 job 状态为 running
            now_str = SQLiteStore.now_str()
            self._store.execute(
                """
                UPDATE kb_index_job
                   SET status = 'running', started_at = ?
                 WHERE id = ?
                """,
                (now_str, job_id),
            )

            pipeline = self._get_pipeline(topic_id)
            knowledge_dir = self._knowledge_dir(topic_id)
            mode = "force" if force else "incremental"
            result = pipeline.index_directory(knowledge_dir, mode=mode, created_by=self._created_by)

            # 更新 job 状态为 done
            chunks = result.get("chunks", 0)
            files = result.get("files", 0)
            self._store.execute(
                """
                UPDATE kb_index_job
                   SET status = 'done',
                       progress = 100,
                       file_total = ?,
                       file_done = ?,
                       chunk_total = ?,
                       finished_at = strftime('%Y-%m-%d %H:%M:%S','now')
                 WHERE id = ?
                """,
                (files, files, chunks, job_id),
            )
            self._audit("index_done", topic_id=topic_id, job_id=job_id, result_summary=result)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            _log.exception("_run_index_job failed: job_id=%s", job_id)
            try:
                self._store.execute(
                    """
                    UPDATE kb_index_job
                       SET status = 'failed',
                           error_msg = ?,
                           finished_at = strftime('%Y-%m-%d %H:%M:%S','now')
                     WHERE id = ?
                    """,
                    (error, job_id),
                )
            except Exception:
                pass
            self._audit("index_done", topic_id=topic_id, job_id=job_id, error=error)

    def _run_ingest_files_job(
        self,
        topic_id: str,
        job_id: str,
        file_paths: list[Path],
        *,
        chunk_size: int = 800,
        chunk_overlap: int = 120,
    ) -> None:
        """后台线程执行文件级 ingest（由 /ingest-files 端点的 BackgroundTasks 调度）."""
        try:
            # 更新 job 状态为 running
            now_str = SQLiteStore.now_str()
            self._store.execute(
                """
                UPDATE kb_index_job
                   SET status = 'running', started_at = ?, file_total = ?
                 WHERE id = ?
                """,
                (now_str, len(file_paths), job_id),
            )

            result = self.ingest_files(
                topic_id,
                file_paths,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )

            # 更新 job 状态为 done
            chunks = result.get("chunks", 0)
            doc_count = len(result.get("doc_ids", []))
            self._store.execute(
                """
                UPDATE kb_index_job
                   SET status = 'done',
                       progress = 100,
                       file_total = ?,
                       file_done = ?,
                       chunk_total = ?,
                       finished_at = strftime('%Y-%m-%d %H:%M:%S','now')
                 WHERE id = ?
                """,
                (len(file_paths), len(file_paths), chunks, job_id),
            )
            self._audit(
                "ingest_files_done",
                topic_id=topic_id,
                job_id=job_id,
                result_summary={"doc_count": doc_count, "chunks": chunks},
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            _log.exception("_run_ingest_files_job failed: job_id=%s", job_id)
            try:
                self._store.execute(
                    """
                    UPDATE kb_index_job
                       SET status = 'failed',
                           error_msg = ?,
                           finished_at = strftime('%Y-%m-%d %H:%M:%S','now')
                     WHERE id = ?
                    """,
                    (error, job_id),
                )
            except Exception:
                pass
            self._audit("ingest_files_done", topic_id=topic_id, job_id=job_id, error=error)

    # ------------------------------------------------------------------
    # 统计 / 清空
    # ------------------------------------------------------------------

    def stats(self, topic_id: str) -> dict[str, Any]:
        """知识库统计."""
        total_chunks = self._store.fetch_one("SELECT COUNT(*) c FROM kb_chunk WHERE topic_id = ?", (topic_id,))["c"]
        doc_count = self._store.fetch_one(
            "SELECT COUNT(*) c FROM kb_document WHERE topic_id = ? AND status != 'deleted'",
            (topic_id,),
        )["c"]
        by_type_rows = self._store.fetch_all(
            """
            SELECT d.source_type, COUNT(*) c
              FROM kb_chunk c
              JOIN kb_document d ON c.document_id = d.id
             WHERE c.topic_id = ?
             GROUP BY d.source_type
            """,
            (topic_id,),
        )
        by_source_type = {r["source_type"] or "unknown": r["c"] for r in by_type_rows}
        successful_jobs = self._store.fetch_one(
            "SELECT COUNT(*) c FROM kb_index_job WHERE topic_id = ? AND status = 'done'",
            (topic_id,),
        )["c"]
        kdir = self._knowledge_dir(topic_id)
        collection = f"kb_{topic_id}"
        points_count = self._qdrant_points_count(collection)

        return {
            "topic_id": topic_id,
            "collection": collection,
            "topic_dir": str(self._project_root / _KB_DATA_DIR / topic_id),
            "topic_knowledge_dir": str(kdir),
            "topic_knowledge_dir_exists": kdir.exists(),
            "total_chunks": total_chunks,
            "documents": doc_count,
            "chunks_in_meta": points_count if points_count is not None else total_chunks,
            "by_source_type": by_source_type,
            "successful_jobs": successful_jobs,
        }

    def clear(self, topic_id: str) -> dict[str, Any]:
        """清空知识库：Qdrant 删 collection + kb_document 软删."""
        pipeline = self._get_pipeline(topic_id)
        r = pipeline.clear(created_by=self._created_by)
        collection = f"kb_{topic_id}"
        total_chunks = self._store.fetch_one("SELECT COUNT(*) c FROM kb_chunk WHERE topic_id = ?", (topic_id,))["c"]
        return {
            "ok": r.get("ok", True),
            "topic_id": topic_id,
            "collection": collection,
            "cleared": True,
            "soft_deleted_documents": r.get("soft_deleted_documents", 0),
            "total_chunks": total_chunks,
        }

    # ------------------------------------------------------------------
    # 文件级 Ingest（供 CLI 直接指定文件路径使用）
    # ------------------------------------------------------------------

    def ingest_files(
        self,
        topic_id: str,
        files: list[Path],
        *,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> dict[str, Any]:
        """直接把指定的文件列表写入知识库（读取 → 切块 → 嵌入 → 双写）.

        Parameters
        ----------
        topic_id : str
            目标主题 ID。
        files : list[Path]
            文件路径列表（支持 pdf / md / txt）。
        chunk_size / chunk_overlap : int | None
            覆盖默认值。

        Returns
        -------
        dict
            含 doc_ids / chunks / elapsed 等信息。
        """
        self.ensure_topic(topic_id)

        # 加载文档（用带主题 enrichers 的 reader）
        reader = self._get_reader(topic_id)
        docs = reader.load_documents(files)
        if not docs:
            return {"ok": False, "error": "没有读到任何文件", "doc_ids": [], "chunks": 0}

        # 走 pipeline ingest
        cs = chunk_size or self._chunk_size
        co = chunk_overlap or self._chunk_overlap
        pipeline = self._get_pipeline(topic_id)
        # 如果 chunk_size / chunk_overlap 与 pipeline 不同，临时创建新 pipeline
        if cs != self._chunk_size or co != self._chunk_overlap:
            pipeline = KnowledgePipeline(
                topic_id,
                factory=self._factory,
                store=self._store,
                chunk_size=cs,
                chunk_overlap=co,
            )

        t0 = time.time()
        result = pipeline.ingest(docs)
        elapsed = time.time() - t0

        return {
            "ok": True,
            "doc_ids": result.get("doc_ids", []),
            "chunks": result.get("chunks", 0),
            "elapsed": elapsed,
        }

    # ------------------------------------------------------------------
    # 状态详情（供 CLI 的 status 子命令使用）
    # ------------------------------------------------------------------

    def status_detail(self, topic_id: str) -> dict[str, Any]:
        """返回 CLI status 所需的详细信息（比 stats() 更丰富）."""
        self.ensure_topic(topic_id)

        done = self._store.fetch_one(
            "SELECT COUNT(*) c FROM kb_document WHERE topic_id = ? AND status = 'done'",
            (topic_id,),
        )["c"]
        deleted = self._store.fetch_one(
            "SELECT COUNT(*) c FROM kb_document WHERE topic_id = ? AND status = 'deleted'",
            (topic_id,),
        )["c"]
        parsing = self._store.fetch_one(
            "SELECT COUNT(*) c FROM kb_document WHERE topic_id = ? AND status NOT IN ('done','deleted')",
            (topic_id,),
        )["c"]
        chunks = self._store.fetch_one(
            "SELECT COUNT(*) c FROM kb_chunk WHERE topic_id = ?",
            (topic_id,),
        )["c"]
        collection = f"kb_{topic_id}"
        points_count = self._qdrant_points_count(collection)

        # 最近 10 条文档
        recent_docs = []
        if done:
            rows = self._store.fetch_all(
                """
                SELECT id, version, title, file_name, file_size, updated_at
                  FROM kb_document
                 WHERE topic_id = ? AND status = 'done'
                 ORDER BY updated_at DESC
                 LIMIT 10
                """,
                (topic_id,),
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
            "topic_id": topic_id,
            "collection": collection,
            "backend": self._factory.backend,
            "qdrant_url": self._factory.qdrant_url,
            "documents_done": done,
            "documents_other": parsing,
            "documents_deleted": deleted,
            "chunks_total": chunks,
            "qdrant_points": points_count,
            "recent_docs": recent_docs,
        }

    # ------------------------------------------------------------------
    # 主题 CRUD
    # ------------------------------------------------------------------

    def ensure_topic(
        self,
        topic_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """创建或更新主题（幂等）."""
        display_name = name or topic_id
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        now_str = SQLiteStore.now_str()

        existing = self._store.fetch_one(
            "SELECT topic_id FROM knowledge_topic WHERE topic_id = ?",
            (topic_id,),
        )
        if existing:
            # 更新
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
                params.append(topic_id)
                self._store.execute(
                    f"UPDATE knowledge_topic SET {', '.join(set_parts)} WHERE topic_id = ?",
                    tuple(params),
                )
        else:
            # 新建
            self._store.execute(
                """
                INSERT INTO knowledge_topic
                    (topic_id, name, description, tags_json, status, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic_id,
                    display_name,
                    description or "",
                    tags_json,
                    status or "active",
                    self._created_by,
                    now_str,
                    now_str,
                ),
            )
            # 同时创建 knowledge 目录骨架
            self._ensure_knowledge_dir(topic_id)

        return self.get_topic(topic_id) or {"topic_id": topic_id, "name": display_name}

    def list_topics(self, *, include_archived: bool = False) -> list[dict]:
        """返回所有主题列表."""
        where = "" if include_archived else "WHERE status != 'archived'"
        rows = self._store.fetch_all(
            f"""
            SELECT t.topic_id, t.name, t.description, t.tags_json, t.status,
                   t.created_at, t.updated_at,
                   (SELECT COUNT(*) FROM kb_chunk c WHERE c.topic_id = t.topic_id) AS chunks_in_collection
              FROM knowledge_topic t
             {where}
             ORDER BY t.updated_at DESC
            """
        )
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags_json") or "[]")
            except Exception:
                d["tags"] = []
            d.pop("tags_json", None)
            result.append(d)
        return result

    def get_topic(self, topic_id: str) -> dict[str, Any] | None:
        """获取单个主题详情."""
        row = self._store.fetch_one(
            """
            SELECT t.topic_id, t.name, t.description, t.tags_json, t.status,
                   t.created_at, t.updated_at,
                   (SELECT COUNT(*) FROM kb_chunk c WHERE c.topic_id = t.topic_id) AS chunks_in_collection
              FROM knowledge_topic t
             WHERE t.topic_id = ?
            """,
            (topic_id,),
        )
        if row is None:
            return None
        d = dict(row)
        try:
            d["tags"] = json.loads(d.get("tags_json") or "[]")
        except Exception:
            d["tags"] = []
        d.pop("tags_json", None)
        return d

    def delete_topic(self, topic_id: str) -> dict[str, Any]:
        """删除主题记录."""
        count = self._store.execute(
            "DELETE FROM knowledge_topic WHERE topic_id = ?",
            (topic_id,),
        )
        return {"removed_rows": count}

    # ------------------------------------------------------------------
    # World-Topic 绑定
    # ------------------------------------------------------------------

    def list_bindings(self, *, topic_id: str | None = None) -> list[dict]:
        """查询绑定列表."""
        if topic_id:
            return self._store.fetch_all(
                "SELECT world_id, topic_id, priority FROM world_topic_binding"
                " WHERE topic_id = ? ORDER BY priority DESC",
                (topic_id,),
            )
        return self._store.fetch_all(
            "SELECT world_id, topic_id, priority FROM world_topic_binding ORDER BY priority DESC"
        )

    def get_world_binding(self, world_id: str) -> dict[str, Any] | None:
        """查询单个 world 的绑定."""
        return self._store.fetch_one(
            "SELECT world_id, topic_id, priority FROM world_topic_binding WHERE world_id = ?",
            (world_id,),
        )

    def bind_world_topic(self, world_id: str, topic_id: str, *, priority: int = 0) -> dict[str, Any]:
        """绑定 world 到主题."""
        removed = 0
        # 先删旧绑定（同一 world_id）
        removed = self._store.execute(
            "DELETE FROM world_topic_binding WHERE world_id = ?",
            (world_id,),
        )
        self._store.execute(
            """
            INSERT INTO world_topic_binding (world_id, topic_id, priority)
            VALUES (?, ?, ?)
            """,
            (world_id, topic_id, priority),
        )
        return {"ok": True, "removed_rows": removed}

    def unbind_world(self, world_id: str) -> dict[str, Any]:
        """解绑 world."""
        removed = self._store.execute(
            "DELETE FROM world_topic_binding WHERE world_id = ?",
            (world_id,),
        )
        return {"removed_rows": removed}

    # ------------------------------------------------------------------
    # 索引任务 + 审计
    # ------------------------------------------------------------------

    def job_list(self, topic_id: str, *, limit: int = 50) -> list[dict]:
        """索引任务列表."""
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
            (topic_id, limit),
        )

    def audit_list(self, topic_id: str, *, limit: int = 100) -> list[dict]:
        """审计日志列表."""
        return self._store.fetch_all(
            """
            SELECT id, op, actor, topic_id, document_id, job_id,
                   query_text, top_k, filters_json, result_json, error_msg, created_at
              FROM kb_audit_log
             WHERE topic_id = ?
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (topic_id, limit),
        )

    # ------------------------------------------------------------------
    # 预置主题 + 关闭
    # ------------------------------------------------------------------

    def _bootstrap_default_topics(self) -> None:
        """自动创建预置主题."""
        for t in _DEFAULT_TOPICS:
            try:
                self.ensure_topic(t["topic_id"], name=t["name"], description=t["description"])
            except Exception:
                _log.exception("bootstrap topic failed: %s", t["topic_id"])

    def close(self) -> None:
        """关闭所有资源."""
        try:
            self._factory.close_all()
        except Exception:
            pass
        try:
            self._store.close()
        except Exception:
            pass
        self._pipelines.clear()
