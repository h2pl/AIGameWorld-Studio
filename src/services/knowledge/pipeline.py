"""KnowledgePipeline — 文档处理流水线（可插拔向量库 + SQLiteStore 双写版）/ Document ingestion pipeline.

组织维度: **topic_id (主题/IP)** — 每个主题一个独立向量 collection: ``kb_{topic_id}``
world/pack 只是运行时引用关系，通过 world_topic_binding 表做绑定。

向量库可切换（通过 KBVectorStoreFactory）：
  - 默认 Qdrant（Docker，HTTP 6333 + Dashboard /dashboard）
  - 兼容 Chroma（本地文件，旧模式保留）

流程
----
Reader → `_upsert_document_meta()` (写 kb_document，生成 doc_id)
      → SentenceSplitter（切 chunk，每个 chunk 附 doc_id）
      → BGE-M3 Embedding
      → 写回 kb_chunk（每个 chunk 的 UUID = 向量库 id）
      → 写 kb_index_job（任务进度）
"""

# __future__ 导入：新版本类型语法兼容
from __future__ import annotations

# 标准库导入
import hashlib
import json
import time
from pathlib import Path
from typing import Any

# LlamaIndex 生态：Document / Pipeline / Splitter / Embedding
from llama_index.core import Document
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

# 本地模块导入
from .reader import KnowledgeReader
from .vector_store import KBVectorStoreFactory
from ...utils.sqlite_store import SQLiteStore  # noqa: F401  (对外暴露类型)


# 知识库文档处理流水线类：双写 SQLite 元数据 + 可插拔向量存储
class KnowledgePipeline:
    """知识库文档处理流水线（双写 SQLite 元数据 + 可插拔向量存储）."""

    # 构造函数：初始化 topic、向量库工厂、SQLiteStore、Reader + 构建 LlamaIndex IngestionPipeline
    def __init__(
        self,
        topic_id: str,
        factory: KBVectorStoreFactory | None = None,
        store: SQLiteStore | None = None,
    ):
        self.topic_id = topic_id
        self.collection_name = f"kb_{topic_id}"
        self._factory = factory or KBVectorStoreFactory.get_default()
        self._store = store
        self._reader = KnowledgeReader()

        # BGE-M3 本地路径（ModelScope / HuggingFace 下载缓存）
        import os

        _bge_path = os.path.expanduser(
            "~/.cache/huggingface/hub/models/BAAI--bge-m3/snapshots/master"
        )

        splitter = SentenceSplitter(chunk_size=500, chunk_overlap=50)
        splitter.include_metadata = True
        splitter.include_prev_next_rel = True
        try:
            splitter.excluded_embed_metadata_keys = []
            splitter.excluded_llm_metadata_keys = []
        except Exception:
            pass

        self._pipeline = IngestionPipeline(
            transformations=[
                splitter,
                HuggingFaceEmbedding(model_name=_bge_path, trust_remote_code=True),
            ],
            vector_store=self._factory.get_vector_store(topic_id),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # ingest：主入口 — 对 documents 依次执行 upsert_meta → pipeline.run → 写 kb_chunk 批量入库
    def ingest(self, documents: list[Document]) -> dict:
        """执行流水线：切 chunk → embedding → 双写.

        Returns
        -------
        dict
            ``{"chunks": int, "doc_ids": [uuid...], "chunk_ids": [uuid...]}``
        """
        if not documents:
            return {"chunks": 0, "doc_ids": [], "chunk_ids": []}

        # 第一步：逐个 Document 写 kb_document 元数据，拿到 doc_id
        doc_ids: list[str] = []
        for doc in documents:
            doc_id = self._upsert_document_meta(doc)
            doc.metadata["doc_id"] = doc_id
            doc.metadata["topic_id"] = self.topic_id
            try:
                object.__setattr__(doc, "id_", doc_id)
            except Exception:
                pass
            doc_ids.append(doc_id)

        # 第二步：确保向量库 collection 就绪，然后跑 LlamaIndex IngestionPipeline（切 chunk + embedding + 入库）
        self._ensure_vector_store_collection()
        nodes = self._pipeline.run(documents=documents)

        # 第三步：按 doc_id 分组 nodes，为每个 chunk 生成 UUID + 元数据 + text_hash，准备批量写 kb_chunk
        chunk_ids: list[str] = []
        by_doc: dict[str, list[tuple[int, object]]] = {}
        for i, n in enumerate(nodes):
            ref = getattr(n, "ref_doc_id", None) or (
                n.metadata.get("doc_id") if n.metadata else None
            )
            if ref not in by_doc:
                by_doc[ref] = []
            by_doc[ref].append((i, n))

        # 第四步：遍历每个 doc 的 chunks，计算 chunk 元数据并组装 SQLite 行
        all_chunk_rows: list[tuple] = []
        for doc_id, items in by_doc.items():
            # 每个 doc 内的 chunk 总数，用于给 chunk_index / chunk_count 赋值
            chunk_count = len(items)
            for idx_in_doc, (_global_idx, node) in enumerate(items):
                chunk_id = SQLiteStore.new_id()
                text = getattr(node, "text", "") or ""
                text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                preview = text[:200]
                node_meta = dict(node.metadata) if node.metadata else {}
                node_meta["chunk_id"] = chunk_id
                node_meta["chunk_index"] = idx_in_doc
                node_meta["chunk_count"] = chunk_count
                node_meta["text_hash"] = text_hash
                node_meta["topic_id"] = self.topic_id
                try:
                    object.__setattr__(node, "metadata", node_meta)
                except Exception:
                    pass
                meta_json = json.dumps(node_meta, ensure_ascii=False)
                try:
                    object.__setattr__(node, "id_", chunk_id)
                except Exception:
                    pass
                all_chunk_rows.append((
                    chunk_id,
                    doc_id,
                    self.topic_id,
                    idx_in_doc,
                    chunk_count,
                    text_hash,
                    preview,
                    0,
                    meta_json,
                    int(time.time() * 1000),
                ))
                chunk_ids.append(chunk_id)

        # 第五步：事务批量写 SQLite — 先写所有 kb_chunk 行，再更新 kb_document 状态为 done
        if self._store is not None:
            with self._store.transaction():
                # SQL: 批量 INSERT OR REPLACE 写 kb_chunk（10 列：id / doc_id / topic 等）
                self._store.executemany(
                    """
                    INSERT OR REPLACE INTO kb_chunk
                        (id, document_id, topic_id, chunk_index, chunk_count,
                         text_hash, text_preview, token_count, meta_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    all_chunk_rows,
                )
                # SQL: 批量 UPDATE kb_document 状态为 done（跳过已软删的记录）
                self._store.executemany(
                    """
                    UPDATE kb_document
                       SET status       = 'done',
                           updated_at   = unixepoch('subsec') * 1000,
                           error_msg    = NULL
                     WHERE id = ?
                       AND status != 'deleted'
                    """,
                    [(doc_id,) for doc_id in doc_ids],
                )

        return {"chunks": len(nodes), "doc_ids": doc_ids, "chunk_ids": chunk_ids}

    # clear：清空当前 topic 的知识库 — 先删向量 collection，再软删 kb_document，写审计
    def clear(self, *, created_by: str = "system") -> dict:
        """清空当前主题知识库：向量库删 collection + kb_document 软删."""
        # 先删底层向量 collection（通过 Factory 路由到底层实现）
        self._factory.delete_collection(self.topic_id)
        # 再刷新 pipeline.vector_store 引用（拿新的 collection）
        self._pipeline.vector_store = self._factory.get_vector_store(self.topic_id)
        self._ensure_vector_store_collection()

        count_docs = 0
        if self._store is not None:
            now_ms = int(time.time() * 1000)
            # SQL: 软删 kb_document（status = deleted，填 updated_at / deleted_at）
            cur = self._store.execute(
                """
                UPDATE kb_document
                   SET status     = 'deleted',
                       updated_at = ?,
                       deleted_at = ?
                 WHERE topic_id   = ?
                   AND status    != 'deleted'
                """,
                (now_ms, now_ms, self.topic_id),
            )
            count_docs = cur.rowcount
            self._audit("clear_collection", created_by=created_by)

        return {
            "ok": True,
            "topic_id": self.topic_id,
            "soft_deleted_documents": count_docs,
        }

    # index_directory：高层目录索引入口 — 创建 job → 读文件 → force/增量过滤 → ingest → 更新 job + 审计
    def index_directory(self, knowledge_dir: Path, *, mode: str = "incremental", created_by: str = "system") -> dict:
        """索引整个知识目录.

        Parameters
        ----------
        knowledge_dir : Path
            知识根目录（含 lore|documents|images|videos），不再强制是 world-pack 的子目录
        """
        # 先写一条 kb_index_job 记录（pending 状态），用于 UI 任务面板
        job_id = self._create_job(mode=mode, created_by=created_by)
        documents = self._reader.load(knowledge_dir)
        if not documents:
            self._update_job(job_id, "done", file_total=0, file_done=0, chunk_total=0)
            return {"files": 0, "skipped_files": 0, "new_files": 0, "chunks": 0, "job_id": job_id}

        self._update_job(job_id, "running", file_total=len(documents), file_done=0, chunk_total=0)

        try:
            # force 模式：先 clear 清空旧 collection，所有文件都视为新文件
            if mode == "force":
                self.clear(created_by=created_by)
                new_docs = documents
                skipped = 0
            # incremental 模式：基于 kb_document + SHA256 过滤出未变更文件
            else:
                new_docs, skipped = self._filter_indexed(documents)

            if not new_docs:
                self._update_job(job_id, "done", file_total=len(documents), file_done=len(documents), chunk_total=0)
                return {
                    "files": len(documents),
                    "skipped_files": skipped,
                    "new_files": 0,
                    "chunks": 0,
                    "job_id": job_id,
                }

            result = self.ingest(new_docs)
            self._update_job(
                job_id,
                "done",
                file_total=len(documents),
                file_done=len(documents),
                chunk_total=result["chunks"],
            )
            self._audit(
                "index_done",
                created_by=created_by,
                topic_id=self.topic_id,
                job_id=job_id,
                extras={
                    "files_total": len(documents),
                    "files_new": len(new_docs),
                    "files_skipped": skipped,
                    "chunks": result["chunks"],
                },
            )
            return {
                "files": len(documents),
                "skipped_files": skipped,
                "new_files": len(new_docs),
                "chunks": result["chunks"],
                "doc_ids": result["doc_ids"],
                "chunk_ids": result["chunk_ids"],
                "job_id": job_id,
            }
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self._update_job(job_id, "failed", error_msg=msg)
            self._audit("index_done", created_by=created_by, topic_id=self.topic_id, job_id=job_id, error=msg)
            raise

    # ------------------------------------------------------------------
    # Incremental indexing helpers
    # ------------------------------------------------------------------

    # _filter_indexed：基于 kb_document 表 + 文件 SHA256，过滤出需要重新索引的新/变更文件
    def _filter_indexed(self, documents: list[Document]) -> tuple[list[Document], int]:
        """基于 ``kb_document`` 表 + SHA256 判断哪些文件需要重新索引."""
        if self._store is None:
            return documents, 0

        new: list[Document] = []
        skipped = 0
        # 逐个 doc：读本地文件算 SHA256 → 查 kb_document → 未命中或哈希不等 → 加入 new
        for doc in documents:
            fp = doc.metadata.get("file_path", "")
            if not fp or not Path(fp).exists():
                new.append(doc)
                continue
            file_bytes = Path(fp).read_bytes()
            sha256 = hashlib.sha256(file_bytes).hexdigest()
            # SQL: 查 kb_document 是否有同 topic + file_path 的未删除记录
            row = self._store.fetch_one(
                """
                SELECT id, status
                  FROM kb_document
                 WHERE topic_id = ?
                   AND file_path = ?
                   AND status IN ('parsing', 'done')
                """,
                (self.topic_id, fp),
            )
            if row is None:
                new.append(doc)
                continue
            # SQL: 取已有记录的 sha256 和当前文件哈希对比，相等则跳过
            sha_row = self._store.fetch_one("SELECT sha256 FROM kb_document WHERE id = ?", (row["id"],))
            if sha_row and sha_row["sha256"] == sha256:
                doc.metadata["doc_id"] = row["id"]
                skipped += 1
                continue
            new.append(doc)
        return new, skipped

    # _upsert_document_meta：为 Document 在 kb_document 表 upsert 元数据（软删旧版本 + INSERT 新版本）
    def _upsert_document_meta(self, doc: Document) -> str:
        """为单个 Document 在 kb_document 里 upsert 一条记录.

        定位键：topic_id + file_path
        """
        fp = doc.metadata.get("file_path", "")
        if not fp:
            return SQLiteStore.new_id()

        p = Path(fp)
        title = doc.metadata.get("title") or (p.stem if p.exists() else fp)
        source_type = doc.metadata.get("source_type") or KnowledgeReader._infer_source_type(
            p, Path(p).parent
        )
        content_type = doc.metadata.get("content_type") or p.suffix.lstrip(".") or "text"
        file_name = doc.metadata.get("file_name") or p.name
        try:
            file_size = doc.metadata.get("file_size") or (p.stat().st_size if p.exists() else 0)
        except Exception:
            file_size = 0

        file_bytes = p.read_bytes() if p.exists() else b""
        sha256 = hashlib.sha256(file_bytes).hexdigest()
        tags_list: list[str] = []
        if isinstance(doc.metadata.get("tags"), str):
            tags_list = [t.strip() for t in doc.metadata["tags"].split(",") if t.strip()]
        elif isinstance(doc.metadata.get("tags"), list):
            tags_list = [str(t).strip() for t in doc.metadata["tags"] if str(t).strip()]
        tags_json = json.dumps(tags_list, ensure_ascii=False)

        # related_packs：doc.metadata 里带则用，否则空数组（仅引用标记，非主键）
        rp = doc.metadata.get("related_packs") or []
        if isinstance(rp, str):
            try:
                rp_parsed = json.loads(rp)
                rp = rp_parsed if isinstance(rp_parsed, list) else [rp_parsed]
            except Exception:
                rp = [rp]
        if not isinstance(rp, list):
            rp = [rp]
        related_packs_json = json.dumps([str(x) for x in rp if str(x)], ensure_ascii=False)

        if self._store is None:
            return SQLiteStore.new_id()

        # SQL: 查询同 topic_id + file_path 的最新非删除版本（决定是复用 / 软删旧版本后新增）
        existing = self._store.fetch_one(
            """
            SELECT id, version, status
              FROM kb_document
             WHERE topic_id = ?
               AND file_path = ?
               AND status != 'deleted'
             ORDER BY version DESC
             LIMIT 1
            """,
            (self.topic_id, fp),
        )

        if existing and existing["status"] != "deleted":
            # SQL: 取旧记录的 sha256 对比，完全一致则复用旧 doc_id（跳过重索引）
            sha_row = self._store.fetch_one("SELECT sha256 FROM kb_document WHERE id = ?", (existing["id"],))
            if sha_row and sha_row["sha256"] == sha256:
                return existing["id"]
            # 哈希不一致 → 软删旧版本，version + 1
            now_ms = int(time.time() * 1000)
            # SQL: 软删旧版本（status=deleted + 填时间戳）
            self._store.execute(
                """
                UPDATE kb_document
                   SET status     = 'deleted',
                       updated_at = ?,
                       deleted_at = ?
                 WHERE id = ?
                """,
                (now_ms, now_ms, existing["id"]),
            )
            new_version = int(existing["version"] or 1) + 1
        else:
            new_version = 1

        doc_id = SQLiteStore.new_id()
        with self._store.transaction():
            # SQL: INSERT 新版本 kb_document 记录（status=parsing，后续 ingest 成功后改为 done）
            self._store.execute(
                """
                INSERT INTO kb_document
                    (id, topic_id, title, source_type, file_name, file_path,
                     file_size, sha256, content_type, version, status,
                     created_by, related_packs, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'parsing', ?, ?, ?)
                """,
                (
                    doc_id,
                    self.topic_id,
                    title,
                    source_type,
                    file_name,
                    fp,
                    int(file_size),
                    sha256,
                    content_type,
                    new_version,
                    "system",
                    related_packs_json,
                    tags_json,
                ),
            )
        return doc_id

    # ------------------------------------------------------------------
    # Jobs & Audit
    # ------------------------------------------------------------------

    # _create_job：写一条 kb_index_job 记录（pending），返回 job_id；同时写一条 audit log
    def _create_job(self, *, mode: str, created_by: str, document_id: str | None = None) -> str:
        if self._store is None:
            return ""
        job_id = SQLiteStore.new_id()
        # SQL: INSERT 索引任务记录（pending 状态 + 初始进度 0）
        self._store.execute(
            """
            INSERT INTO kb_index_job
                (id, topic_id, document_id, mode, status, progress, created_by, created_at)
            VALUES (?, ?, ?, ?, 'pending', 0, ?, unixepoch('subsec') * 1000)
            """,
            (job_id, self.topic_id, document_id, mode, created_by),
        )
        self._audit(
            "index_start",
            created_by=created_by,
            topic_id=self.topic_id,
            job_id=job_id,
            extras={"mode": mode, "document_id": document_id},
        )
        return job_id

    # _update_job：按参数动态拼接 SET 字段，更新 kb_index_job 状态/进度/时间戳
    def _update_job(
        self,
        job_id: str,
        status: str,
        *,
        file_total: int | None = None,
        file_done: int | None = None,
        chunk_total: int | None = None,
        error_msg: str | None = None,
    ) -> None:
        if self._store is None or not job_id:
            return
        set_fields: list[str] = ["status = ?"]
        params: list[Any] = [status]
        if file_total is not None:
            set_fields.append("file_total = ?")
            params.append(file_total)
        if file_done is not None:
            set_fields.append("file_done = ?")
            params.append(file_done)
        if chunk_total is not None:
            set_fields.append("chunk_total = ?")
            params.append(chunk_total)
            set_fields.append("progress = ?")
            params.append(100 if chunk_total and status == "done" else 50 if status == "running" else 0)
        if error_msg is not None:
            set_fields.append("error_msg = ?")
            params.append(error_msg)
        if status in {"done", "failed", "canceled"}:
            set_fields.append("finished_at = unixepoch('subsec') * 1000")
            if status == "running":
                set_fields.append("started_at  = unixepoch('subsec') * 1000")
        elif status == "running":
            set_fields.append("started_at  = IFNULL(started_at, unixepoch('subsec') * 1000)")
        params.append(job_id)
        self._store.execute(f"UPDATE kb_index_job SET {', '.join(set_fields)} WHERE id = ?", tuple(params))

    # _audit：写一条 kb_audit 审计日志（索引开始/结束、清空、搜索等操作）
    def _audit(
        self,
        op: str,
        *,
        created_by: str = "system",
        topic_id: str | None = None,
        world_id: str | None = None,
        document_id: str | None = None,
        job_id: str | None = None,
        query_text: str | None = None,
        top_k: int | None = None,
        filters: dict | None = None,
        result_summary: list[dict] | None = None,
        extras: dict | None = None,
        error: str | None = None,
    ) -> None:
        if self._store is None:
            return
        data = dict(extras or {})
        if result_summary:
            data["top_hits"] = result_summary[:10]
        self._store.execute(
            """
            INSERT INTO kb_audit
                (id, actor, op, topic_id, world_id, document_id, job_id, query_text, top_k,
                 filters_json, result_json, error_msg, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch('subsec') * 1000)
            """,
            (
                SQLiteStore.new_id(),
                created_by,
                op,
                topic_id or self.topic_id,
                world_id,
                document_id,
                job_id,
                query_text,
                top_k,
                json.dumps(filters, ensure_ascii=False) if filters else None,
                json.dumps(data, ensure_ascii=False) if data else None,
                error,
            ),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _ensure_vector_store_collection(self):
        """刷新 pipeline.vector_store 对底层 collection 的引用（clear 删完 collection 后要调用）。

        - Chroma 模式：必须刷新 `_collection` / `_chroma_collection` 属性（否则还指向旧 collection id）
        - Qdrant 模式：不用做特殊处理（QdrantVectorStore 通过 collection_name 寻址，自动指向最新）
        """
        try:
            vector_store = getattr(self._pipeline, "vector_store", None)
            if vector_store is None:
                return

            # Qdrant：无需刷新，直接返回
            vs_class = type(vector_store).__name__
            if vs_class == "QdrantVectorStore":
                return

            # Chroma（兼容旧模式）：重新拿最新 collection 并刷属性
            try:
                new_store = self._factory.get_vector_store(self.topic_id)
                for attr in ("_collection", "_chroma_collection"):
                    if hasattr(new_store, attr) and hasattr(vector_store, attr):
                        object.__setattr__(
                            vector_store, attr, getattr(new_store, attr)
                        )
            except Exception:
                # 实在刷不了就直接换整个 vector_store 引用
                self._pipeline.vector_store = self._factory.get_vector_store(self.topic_id)
        except Exception:
            pass
