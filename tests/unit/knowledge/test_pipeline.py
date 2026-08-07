"""KnowledgePipeline 单元测试（LlamaIndex 原生范式）.

改造后 KnowledgePipeline 完全由 LlamaIndex IngestionPipeline 托管：
- split → embed → vector_store.add（双写 kb_chunk）→ docstore 去重 → cache
- factory 必须是真实的 KBVectorStoreFactory（带 project_root Path），
  不再接受 mock client（构造函数会访问 project_root / data / kb_cache）。
"""

from pathlib import Path

import pytest

from src.services.knowledge.index.vector_store import KBVectorStoreFactory
from src.services.knowledge.ingest.pipeline import KnowledgePipeline
from src.utils.sqlite_store import SQLiteStore


@pytest.fixture
def pipeline(tmp_path: Path):
    """创建测试用 pipeline（sqlite backend，避免依赖 Qdrant Docker）."""
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    store.init_schema(Path("migrations"))
    # kb_document.topic_id 外键需要 knowledge_topic 记录
    store.execute(
        "INSERT INTO knowledge_topic "
        "(id, topic, name, description, tags_json, status, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '[]', 'active', 'test', "
        "strftime('%Y-%m-%d %H:%M:%S','now'), strftime('%Y-%m-%d %H:%M:%S','now'))",
        ("t1", "test_world", "Test World", "test"),
    )
    factory = KBVectorStoreFactory.get_default(project_root=tmp_path, vector_store_backend="sqlite")
    return KnowledgePipeline("test_world", factory=factory, store=store)


class TestPipelineIngest:
    """流水线执行测试."""

    def test_ingest_empty_returns_zero(self, pipeline):
        """空文档列表返回 0 chunks."""
        result = pipeline.ingest([])
        assert result["chunks"] == 0
        assert result["doc_ids"] == []

    def test_clear_deletes_collection(self, pipeline):
        """清空删除 collection + 清空 kb_chunk."""
        # 先写一点东西进去
        from llama_index.core import Document

        doc = Document(
            text="蒙德城是自由之都，是提瓦特大陆上七座主要城邦之一，由七位神明之一的尘世执政所统治。",
            metadata={"file_path": str(pipeline._factory.project_root / "x.txt"), "title": "x"},
        )
        pipeline.ingest([doc])
        assert pipeline._store.fetch_one("SELECT COUNT(*) c FROM kb_chunk WHERE topic_id='test_world'")["c"] > 0
        # clear
        pipeline.clear()
        # kb_chunk 被清空
        assert pipeline._store.fetch_one("SELECT COUNT(*) c FROM kb_chunk WHERE topic_id='test_world'")["c"] == 0


class TestPipelineIndexDirectory:
    """目录索引测试."""

    def test_empty_directory(self, pipeline, tmp_path):
        """空目录返回零文件."""
        result = pipeline.index_directory(tmp_path)
        assert result["files"] == 0
        assert result["chunks"] == 0

    def test_nonexistent_directory(self, pipeline):
        """不存在目录返回零文件."""
        result = pipeline.index_directory(Path("/nonexistent/12345"))
        assert result["files"] == 0
        assert result["chunks"] == 0


class TestPipelineDedup:
    """增量索引（_filter_indexed）SHA256 去重测试."""

    def test_duplicate_doc_skipped(self, pipeline, tmp_path):
        """内容相同的 .md 文件第二次 index_directory 被 SHA256 去重跳过."""
        text = "提瓦特大陆由七国组成，由神明统治。"
        f = tmp_path / "a.md"
        f.write_text(text, encoding="utf-8")

        r1 = pipeline.index_directory(tmp_path)
        assert r1["new_files"] > 0

        r2 = pipeline.index_directory(tmp_path)
        assert r2["new_files"] == 0  # 已被 SHA256 去重跳过
        assert r2["skipped_files"] == r1["files"]
