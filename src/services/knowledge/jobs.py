"""KnowledgeJobRunner — 知识库异步作业调度（从 manager 抽离的执行层）.

manager 只负责**同步业务编排**（index / ingest_files / clear / search …），
而「异步任务记录创建 + 后台线程执行 + job 进度更新 + 审计」属于执行/调度
职责，不归门面管。本模块把它们独立出来，使 manager 不被线程逻辑与
FastAPI 依赖污染。

典型用法（API 层）：
    runner = KnowledgeJobRunner(manager=km, jobs=jobs_repo, documents=docs_repo,
                                audits=audits_repo, created_by="ui")
    r = runner.index_async(topic, force=False)
    background_tasks.add_task(runner.run_index_job, topic, r["job_id"], force=False)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ...repository.audit_repo import AuditRepository
from ...repository.document_repo import DocumentRepository
from ...repository.job_repo import JobRepository
from ...utils.sqlite_store import SQLiteStore

_log = logging.getLogger(__name__)


class KnowledgeJobRunner:
    """知识库异步作业调度器 — 持有 manager + 相关 repo，负责 job 生命周期."""

    def __init__(
        self,
        *,
        manager: Any,  # KnowledgeManager（避免循环 import，用 duck typing）
        jobs: JobRepository,
        documents: DocumentRepository,
        audits: AuditRepository,
        created_by: str = "ui",
    ):
        self._manager = manager
        self._jobs = jobs
        self._documents = documents
        self._audits = audits
        self._created_by = created_by

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 索引作业
    # ------------------------------------------------------------------

    def index_async(self, topic_slug: str, *, force: bool = False) -> dict[str, Any]:
        """创建索引任务记录并返回 job_id（不执行，由后台任务调 run_index_job）.

        Raises 语义由调用方处理（API 层转 HTTPException 409）。
        """
        # 防并发：同一 topic 同时只允许一个索引任务
        running = self._jobs.has_running_job(topic_slug)
        if running:
            raise JobConflictError(f"主题 {topic_slug} 已有索引任务运行中 (job_id={running})")

        mode = "force" if force else "incremental"
        # 先确保 knowledge 目录存在
        self._manager.ensure_knowledge_dir(topic_slug)
        # 创建 job 记录
        job_id = ""
        try:
            job_id = SQLiteStore.new_id()
            self._jobs.create_index_job(job_id=job_id, topic_slug=topic_slug, mode=mode, created_by=self._created_by)
            self._audit("index_start", topic_slug=topic_slug, job_id=job_id)
        except Exception:
            _log.exception("index_async: create job failed")

        return {"ok": True, "topic": topic_slug, "job_id": job_id, "status": "pending"}

    def run_index_job(self, topic_slug: str, job_id: str, *, force: bool = False) -> None:
        """后台线程执行索引（由 FastAPI BackgroundTasks 调度）."""
        try:
            self._jobs.update_index_job(job_id, "running", started_at=SQLiteStore.now_str())

            result = self._manager.index(topic_slug, force=force)

            chunks = result.get("chunks", 0)
            self._jobs.update_index_job(
                job_id,
                "done",
                finished_at=SQLiteStore.now_str(),
                file_total=result.get("files", 0),
                file_done=result.get("files", 0),
                chunk_total=chunks,
                progress=100,
            )
            self._audit("index_done", topic_slug=topic_slug, job_id=job_id, result_summary=result)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            _log.exception("run_index_job failed: job_id=%s", job_id)
            self._jobs.update_index_job(job_id, "failed", finished_at=SQLiteStore.now_str(), error_msg=error)
            self._audit("index_done", topic_slug=topic_slug, job_id=job_id, error=error)

    # ------------------------------------------------------------------
    # 文件级 ingest 作业
    # ------------------------------------------------------------------

    def run_ingest_files_job(
        self,
        topic_slug: str,
        job_id: str,
        file_paths: list[Path],
        *,
        chunk_size: int = 800,
        chunk_overlap: int = 120,
    ) -> None:
        """后台线程执行文件级 ingest（由 /ingest-files 端点的 BackgroundTasks 调度）."""
        try:
            self._jobs.update_index_job(job_id, "running", started_at=SQLiteStore.now_str(), file_total=len(file_paths))

            # 进度回调：文件读取占 0-5%，embedding 占 5-100%
            # （embedding 是瓶颈，占 ~95% 耗时，进度条应主要反映 embedding 进度）
            def _on_file_done(done: int, total: int) -> None:
                pct = int(done / total * 5) if total > 0 else 5
                self._jobs.update_index_job(job_id, "running", file_done=done, progress=pct)

            def _on_embed_done(done: int, total: int) -> None:
                pct = 5 + int(done / total * 95) if total > 0 else 100
                self._jobs.update_index_job(job_id, "running", progress=pct)

            result = self._manager.ingest_files(
                topic_slug,
                file_paths,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                on_progress=_on_file_done,
                on_embed_progress=_on_embed_done,
            )

            chunks = result.get("chunks", 0)
            doc_count = len(result.get("doc_ids", []))
            self._jobs.update_index_job(
                job_id,
                "done",
                finished_at=SQLiteStore.now_str(),
                file_total=len(file_paths),
                file_done=len(file_paths),
                chunk_total=chunks,
                progress=100,
            )
            self._audit(
                "ingest_files_done",
                topic_slug=topic_slug,
                job_id=job_id,
                result_summary={"doc_count": doc_count, "chunks": chunks},
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            _log.exception("run_ingest_files_job failed: job_id=%s", job_id)
            self._jobs.update_index_job(job_id, "failed", finished_at=SQLiteStore.now_str(), error_msg=error)
            self._audit("ingest_files_done", topic_slug=topic_slug, job_id=job_id, error=error)


class JobConflictError(RuntimeError):
    """索引任务冲突（同一 topic 已有 running/pending 任务）."""
