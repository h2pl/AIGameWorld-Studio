"""KnowledgeManager — 知识库业务编排门面（service 层，组合下层边界）.

对外暴露 :class:`KnowledgeManager`，是 API/UI 层的唯一入口，只做业务编排：
- 上传 / 粘贴 → 委托 :class:`FileIO`（storage 层纯文件 IO）+ 本地登记 kb_document
- 混合检索   → 委托 :class:`KnowledgeRetriever`（检索编排边界）
- 索引触发   → 委托 :class:`KnowledgePipeline`
- 主题/绑定/任务/审计 CRUD → 直接持有各实体 repository（无聚合门面）
- 统计 / 清空 / 状态详情 → 组合各 repo 聚合查询

分层契约（对标主项目 backend/src/）：
  service(manager) → [storage.FileIO / search_engine / pipeline] + repository
  repository → storage（裸客户端）
无 service 之上层，无聚合门面。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...repository.audit_repo import AuditRepository
from ...repository.binding_repo import BindingRepository
from ...repository.document_repo import DocumentRepository
from ...repository.job_repo import JobRepository
from ...repository.topic_repo import TopicRepository
from ...storage.file_io import FileIO
from ...storage.qdrant_client import QdrantClient
from ...utils.sqlite_store import SQLiteStore
from .index.vector_store import KBVectorStoreFactory
from .ingest.pipeline import KnowledgePipeline
from .ingest.reader import KnowledgeReader
from .jobs import KnowledgeJobRunner
from .retrieve.search_engine import KnowledgeRetriever

_log = logging.getLogger(__name__)

# 默认知识库文件根目录（相对于 project_root）
_KB_DATA_DIR = "knowledge-bases"

# 预置主题（deps.py bootstrap_default_topics=True 时自动创建）
_DEFAULT_TOPICS = [
    {"topic": "genshin", "name": "原神", "description": "原神世界观与设定集"},
    {"topic": "world_of_warcraft", "name": "魔兽世界", "description": "魔兽世界编年史与官方设定集"},
]


class KnowledgeManager:
    """知识库业务编排门面 — 组合下层边界 + 各实体 repository."""

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
        # repository 层：按主项目粒度「一实体一 repo」直接持有（无聚合门面）
        self._documents = DocumentRepository(self._store)
        self._topics = TopicRepository(self._store)
        self._bindings = BindingRepository(self._store)
        self._jobs = JobRepository(self._store)
        self._audits = AuditRepository(self._store)
        # storage 层：裸 Qdrant HTTP 客户端（零业务）
        self._qdrant = QdrantClient(self._factory.qdrant_url)
        self._created_by = created_by
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._reader = KnowledgeReader()
        self._readers: dict[str, KnowledgeReader] = {}  # topic → 带 enrichers 的 reader
        self._pipelines: dict[str, KnowledgePipeline] = {}

        # 下层边界：磁盘 IO（storage 层纯 IO）与检索编排（manager 之下，保持职责单一）
        self._file_io = FileIO(project_root=self._project_root)
        self._retriever = KnowledgeRetriever(
            store=self._store,
            documents=self._documents,
            qdrant=self._qdrant,
            get_pipeline=self._get_pipeline,
        )

        # 异步作业调度器（执行层，从 manager 抽离；复用 manager 已持有的 repos/store）
        self._job_runner = KnowledgeJobRunner(
            manager=self,
            jobs=self._jobs,
            documents=self._documents,
            audits=self._audits,
            created_by=self._created_by,
        )

        if auto_init_schema:
            self._store.init_schema(self._project_root / "migrations")
            # 幂等补列：kb_chunk.page_number（PDF 页码感知，migrations/0002 文档说明）
            self._documents.ensure_kb_chunk_page_column()
        if bootstrap_default_topics:
            self._bootstrap_default_topics()

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _get_reader(self, topic_slug: str) -> KnowledgeReader:
        """获取/缓存指定 topic 的 KnowledgeReader（带主题特定的 enrichers）."""
        if topic_slug not in self._readers:
            from .ingest.enrichers import get_enrichers_for_topic

            self._readers[topic_slug] = KnowledgeReader(enrichers=get_enrichers_for_topic(topic_slug))
        return self._readers[topic_slug]

    def _get_pipeline(self, topic_slug: str) -> KnowledgePipeline:
        """获取/缓存指定 topic 的 KnowledgePipeline 实例."""
        if topic_slug not in self._pipelines:
            self._pipelines[topic_slug] = KnowledgePipeline(
                topic_slug,
                factory=self._factory,
                store=self._store,
                chunk_size=self._chunk_size,
                chunk_overlap=self._chunk_overlap,
                enrichers=self._get_reader(topic_slug)._enrichers,
            )
        return self._pipelines[topic_slug]

    def _audit(
        self,
        op: str,
        *,
        topic_slug: str | None = None,
        document_id: str | None = None,
        job_id: str | None = None,
        query_text: str | None = None,
        top_k: int | None = None,
        filters: dict | None = None,
        result_summary: Any = None,
        error: str | None = None,
    ) -> None:
        """写审计日志到 kb_audit 表（委托 repository 层）."""
        self._audits.audit(
            op,
            actor=self._created_by,
            topic_slug=topic_slug,
            document_id=document_id,
            job_id=job_id,
            query_text=query_text,
            top_k=top_k,
            filters=filters,
            result_summary=result_summary,
            error=error,
        )

    def _qdrant_points_count(self, collection: str) -> int | None:
        """通过 Qdrant 客户端查 points_count（委托 storage 层）."""
        return self._qdrant.count_points(collection)

    def _qdrant_delete_points(self, collection: str, point_ids: list[str]) -> bool:
        """通过 Qdrant 客户端批量删 points（委托 storage 层）."""
        return self._qdrant.delete_points(collection, point_ids)

    # ------------------------------------------------------------------
    # 上传（FileIO 落盘 + 本地登记 kb_document）
    # ------------------------------------------------------------------

    def save_uploaded_bytes(
        self,
        topic_slug: str,
        data: bytes,
        source_type: str,
        filename: str,
        *,
        prefix: str = "",
    ) -> dict[str, Any]:
        """保存上传的文件字节流到 knowledge 目录 + 登记 kb_document.

        纯文件 IO 委托 storage 层 :class:`FileIO`，DB 登记由本方法协调
        :class:`DocumentRepository` 完成（业务编排职责在 service 层）。
        """
        info = self._file_io.save_bytes(topic_slug, data, source_type, filename, prefix=prefix)

        doc_id = SQLiteStore.new_id()
        try:
            self._documents.insert_document(
                doc_id=doc_id,
                topic_id=topic_slug,
                title=Path(filename).stem,
                source_type=source_type,
                file_name=info["file_name"],
                file_path=info["file_path"],
                file_size=info["file_size"],
                sha256=info["sha256"],
                content_type=info["content_type"],
                created_by=self._created_by,
            )
        except Exception:
            _log.exception("save_uploaded_bytes: kb_document INSERT failed")

        return {
            "ok": True,
            "file_path": info["file_path"],
            "doc_id": doc_id,
            "file_name": info["file_name"],
        }

    def save_uploaded_text(
        self,
        topic_slug: str,
        text: str,
        source_type: str,
        *,
        file_name: str = "pasted.md",
        prefix: str = "",
    ) -> dict[str, Any]:
        """保存粘贴的文本到 knowledge 目录 + 登记 kb_document."""
        return self.save_uploaded_bytes(topic_slug, text.encode("utf-8"), source_type, file_name, prefix=prefix)

    # ------------------------------------------------------------------
    # 文档 CRUD
    # ------------------------------------------------------------------

    def document_list(
        self,
        topic_slug: str,
        *,
        limit: int = 200,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[dict]:
        """分页返回 kb_document 列表（含每个文档的 chunk_count，委托 repository 层）."""
        return self._documents.document_list(topic_slug, limit=limit, offset=offset, include_deleted=include_deleted)

    def document_delete(self, topic_slug: str, doc_id: str) -> dict[str, Any]:
        """软删文档：kb_document.status='deleted' + 真删 kb_chunk + Qdrant 删 points."""
        # 查出该文档的所有 chunk_id
        chunk_ids = self._documents.chunk_ids_of_document(doc_id, topic_slug)

        now_str = SQLiteStore.now_str()
        self._documents.soft_delete_document(doc_id, topic_slug, now_str)

        # Qdrant 删 points
        collection = f"kb_{topic_slug}"
        self._qdrant_delete_points(collection, chunk_ids)

        self._audit("doc_delete", topic_slug=topic_slug, document_id=doc_id)
        return {"ok": True, "soft_deleted": 1, "deleted_chunk_ids_count": len(chunk_ids)}

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def search_with_meta(
        self,
        topic_slug: str,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.0,
        filters: dict | None = None,
        query_rewrite: bool = False,
    ) -> list[dict]:
        """混合检索：BGE-M3 dense + BM25(RRF 融合) → parent-context 展开 → CrossEncoder 重排.

        编排细节委托 :class:`KnowledgeRetriever`（检索边界），本方法只做审计与返回。
        query_rewrite=True 时启用 Query 改写（multi_query 扩展召回，LLM 不可用时降级）。
        """
        hits = self._retriever.search_with_meta(
            topic_slug,
            query,
            top_k=top_k,
            min_score=min_score,
            filters=filters,
            query_rewrite=query_rewrite,
        )
        self._audit(
            "retrieve",
            topic_slug=topic_slug,
            query_text=query,
            top_k=top_k,
            filters=filters,
            result_summary=[{"score": h["score_cosine_sim"], "source": h["metadata"].get("_source")} for h in hits[:5]],
        )
        return hits

    # ------------------------------------------------------------------
    # 检索辅助（委托 retriever 边界）
    # ------------------------------------------------------------------

    def _build_qdrant_filter(self, topic_slug: str, filters: dict | None) -> dict | None:
        """把 filters 字典转成 Qdrant filter（基于 payload 字段）.

        委托 :class:`KnowledgeRetriever.build_qdrant_filter`。
        """
        return self._retriever.build_qdrant_filter(topic_slug, filters)

    # ------------------------------------------------------------------
    # 索引
    # ------------------------------------------------------------------

    def ensure_knowledge_dir(self, topic_slug: str) -> Path:
        """确保知识库目录骨架存在，返回 knowledge 目录（委托 storage 层 FileIO）."""
        return self._file_io.ensure_knowledge_dir(topic_slug)

    @property
    def job_runner(self) -> KnowledgeJobRunner:
        """异步作业调度器（索引 / 文件 ingest 的后台执行，独立于业务编排）."""
        return self._job_runner

    def index(self, topic_slug: str, *, force: bool = False) -> dict[str, Any]:
        """同步触发索引（阻塞）— 直接调 pipeline.index_directory."""
        pipeline = self._get_pipeline(topic_slug)
        knowledge_dir = self._file_io.knowledge_dir(topic_slug)
        if not knowledge_dir.exists():
            self._file_io.ensure_knowledge_dir(topic_slug)

        mode = "force" if force else "incremental"
        try:
            result = pipeline.index_directory(knowledge_dir, mode=mode, created_by=self._created_by)
            # 索引完成，失效检索端缓存（nodes 已刷新，BM25 需重建）
            self._retriever.invalidate(topic_slug)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._audit("index_done", topic_slug=topic_slug, error=error)
            return {"ok": False, "error": error, "total_chunks": 0}

        total_chunks = result.get("chunks", 0)
        self._audit(
            "index_done",
            topic_slug=topic_slug,
            job_id=result.get("job_id"),
            result_summary=result,
        )
        return {"ok": True, "total_chunks": total_chunks, "indexed": result}

    # ------------------------------------------------------------------
    # 统计 / 清空
    # ------------------------------------------------------------------

    def stats(self, topic_slug: str) -> dict[str, Any]:
        """知识库统计（聚合查询委托 repository 层，Qdrant 点数委托 storage 层）."""
        agg = self._documents.doc_stats(topic_slug)
        kdir = self._file_io.knowledge_dir(topic_slug)
        collection = f"kb_{topic_slug}"
        points_count = self._qdrant_points_count(collection)

        return {
            "topic": topic_slug,
            "collection": collection,
            "topic_dir": str(self._project_root / _KB_DATA_DIR / topic_slug),
            "topic_knowledge_dir": str(kdir),
            "topic_knowledge_dir_exists": kdir.exists(),
            "total_chunks": agg["total_chunks"],
            "documents": agg["documents"],
            "chunks_in_meta": points_count if points_count is not None else agg["total_chunks"],
            "by_source_type": agg["by_source_type"],
            "successful_jobs": agg["successful_jobs"],
        }

    def clear(self, topic_slug: str) -> dict[str, Any]:
        """清空知识库：Qdrant 删 collection + kb_document 软删."""
        pipeline = self._get_pipeline(topic_slug)
        r = pipeline.clear(created_by=self._created_by)
        # 索引已清空，失效检索端缓存的 VectorStoreIndex / BM25（强制下次重建）
        self._retriever.invalidate(topic_slug)
        collection = f"kb_{topic_slug}"
        total_chunks = self._documents.count_chunks(topic_slug)
        return {
            "ok": r.get("ok", True),
            "topic": topic_slug,
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
        topic_slug: str,
        files: list[Path],
        *,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_embed_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        """直接把指定的文件列表写入知识库（读取 → 切块 → 嵌入 → 双写）.

        Parameters
        ----------
        topic_slug : str
            目标主题 ID。
        files : list[Path]
            文件路径列表（支持 pdf / md / txt）。
        chunk_size / chunk_overlap : int | None
            覆盖默认值。
        on_progress : callable, optional
            文件读取进度回调 ``on_progress(done, total)``。
        on_embed_progress : callable, optional
            embedding 进度回调 ``on_embed_progress(done_batches, total_batches)``。

        Returns
        -------
        dict
            含 doc_ids / chunks / skipped_files / elapsed 等信息。
        """
        self.ensure_topic(topic_slug)

        # 文件级去重：内容没变且已 done 的文件跳过，不重算 embedding。
        #   与官方/企业级一致：注入前按文件 hash 查重，命中直接跳过。
        #   纯 IO 去重委托 storage 层 FileIO；已知指纹（已 done 的 sha256）由 repository 提供。
        known_sha256 = self._documents.done_sha256_set(topic_slug)
        new_files, skipped_files = self._file_io.collect_new_files(topic_slug, files, known_sha256=known_sha256)

        if not new_files:
            return {
                "ok": True,
                "doc_ids": [],
                "chunks": 0,
                "skipped_files": len(skipped_files),
                "elapsed": 0.0,
            }

        # 加载文档（用带主题 enrichers 的 reader，串行读取）
        reader = self._get_reader(topic_slug)
        docs = reader.load_documents(new_files, on_progress=on_progress)
        if not docs:
            return {
                "ok": False,
                "error": "没有读到任何文件",
                "doc_ids": [],
                "chunks": 0,
                "skipped_files": len(skipped_files),
            }

        # 走 pipeline ingest
        cs = chunk_size or self._chunk_size
        co = chunk_overlap or self._chunk_overlap
        pipeline = self._get_pipeline(topic_slug)
        # 如果 chunk_size / chunk_overlap 与 pipeline 不同，临时创建新 pipeline
        if cs != self._chunk_size or co != self._chunk_overlap:
            pipeline = KnowledgePipeline(
                topic_slug,
                factory=self._factory,
                store=self._store,
                chunk_size=cs,
                chunk_overlap=co,
            )

        t0 = time.time()
        result = pipeline.ingest(docs, on_embed_progress=on_embed_progress)
        # 索引完成，失效检索端缓存（nodes 已刷新，BM25 需重建）
        self._retriever.invalidate(topic_slug)
        elapsed = time.time() - t0

        return {
            "ok": True,
            "doc_ids": result.get("doc_ids", []),
            "chunks": result.get("chunks", 0),
            "skipped_files": len(skipped_files),
            "elapsed": elapsed,
        }

    # ------------------------------------------------------------------
    # 状态详情（供 CLI 的 status 子命令使用）
    # ------------------------------------------------------------------

    def status_detail(self, topic_slug: str) -> dict[str, Any]:
        """返回 CLI status 所需的详细信息（比 stats() 更丰富，组合各实体 repo 聚合查询）."""
        self.ensure_topic(topic_slug)

        agg = self._documents.doc_status_detail(topic_slug)
        collection = f"kb_{topic_slug}"
        points_count = self._qdrant_points_count(collection)

        return {
            "topic": topic_slug,
            "collection": collection,
            "backend": self._factory.backend,
            "qdrant_url": self._factory.qdrant_url,
            "documents_done": agg["documents_done"],
            "documents_other": agg["documents_other"],
            "documents_deleted": agg["documents_deleted"],
            "chunks_total": agg["chunks_total"],
            "qdrant_points": points_count,
            "recent_docs": agg["recent_docs"],
        }

    # ------------------------------------------------------------------
    # 主题 CRUD
    # ------------------------------------------------------------------

    def ensure_topic(
        self,
        topic_slug: str,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """创建或更新主题（幂等，委托 repository 层）."""
        self._topics.upsert_topic(
            topic_slug=topic_slug,
            name=name,
            description=description,
            tags=tags,
            status=status,
            created_by=self._created_by,
        )
        # 新建时同时创建 knowledge 目录骨架
        if not self._topics.topic_exists(topic_slug):
            self._file_io.ensure_knowledge_dir(topic_slug)
        return self.get_topic(topic_slug) or {"topic": topic_slug, "name": name or topic_slug}

    def list_topics(self, *, include_archived: bool = False) -> list[dict]:
        """返回所有主题列表（委托 repository 层）."""
        return self._topics.list_topics(include_archived=include_archived)

    def get_topic(self, topic_slug: str) -> dict[str, Any] | None:
        """获取单个主题详情（委托 repository 层）."""
        return self._topics.get_topic(topic_slug)

    def delete_topic(self, topic_slug: str) -> dict[str, Any]:
        """删除主题记录（委托 repository 层）."""
        count = self._topics.delete_topic(topic_slug)
        return {"removed_rows": count}

    # ------------------------------------------------------------------
    # World-Topic 绑定
    # ------------------------------------------------------------------

    def list_bindings(self, *, topic_slug: str | None = None) -> list[dict]:
        """查询绑定列表（委托 repository 层）."""
        return self._bindings.list_bindings(topic_slug=topic_slug)

    def get_world_binding(self, world_id: str) -> dict[str, Any] | None:
        """查询单个 world 的绑定（委托 repository 层）."""
        return self._bindings.get_world_binding(world_id)

    def bind_world_topic(self, world_id: str, topic_slug: str, *, priority: int = 0) -> dict[str, Any]:
        """绑定 world 到主题（委托 repository 层）."""
        removed = self._bindings.bind_world_topic(world_id, topic_slug, priority=priority)
        return {"ok": True, "removed_rows": removed}

    def unbind_world(self, world_id: str) -> dict[str, Any]:
        """解绑 world（委托 repository 层）."""
        removed = self._bindings.unbind_world(world_id)
        return {"removed_rows": removed}

    # ------------------------------------------------------------------
    # 索引任务 + 审计
    # ------------------------------------------------------------------

    def job_list(self, topic_slug: str, *, limit: int = 50) -> list[dict]:
        """索引任务列表（委托 repository 层）."""
        return self._jobs.job_list(topic_slug, limit=limit)

    def audit_list(self, topic_slug: str, *, limit: int = 100) -> list[dict]:
        """审计日志列表（委托 repository 层）."""
        return self._audits.audit_list(topic_slug, limit=limit)

    # ------------------------------------------------------------------
    # 预置主题 + 关闭
    # ------------------------------------------------------------------

    def _bootstrap_default_topics(self) -> None:
        """自动创建预置主题."""
        for t in _DEFAULT_TOPICS:
            try:
                self.ensure_topic(t["topic"], name=t["name"], description=t["description"])
            except Exception:
                _log.exception("bootstrap topic failed: %s", t["topic"])

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
