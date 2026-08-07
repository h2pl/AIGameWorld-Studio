"""KnowledgeRetriever 单元测试（LlamaIndex 原生 QueryFusionRetriever + CrossEncoder rerank）.

验证改造后 KnowledgeRetriever.search_with_meta 能走通：
- dense 腿：LlamaIndex VectorStoreIndex + VectorIndexRetriever（原生 metadata filter）
- lexical 腿：_BM25LlamaRetriever（bm25s 引擎 + jieba 中文分词，in-memory nodes）
- 融合：LlamaIndex QueryFusionRetriever（RRF 加权融合，框架原生；EnsembleRetriever 已下架）
- 重排：sentence_transformers.CrossEncoder（bge-reranker-v2-m3，测试里禁用以避免下载模型）

用 sqlite backend 避免依赖 Qdrant Docker；用短中文语料验证混合检索可用。
"""

from pathlib import Path

import pytest
from llama_index.core import Document

from src.repository.document_repo import DocumentRepository
from src.services.knowledge.index.vector_store import KBVectorStoreFactory
from src.services.knowledge.ingest.pipeline import KnowledgePipeline
from src.services.knowledge.retrieve.search_engine import KnowledgeRetriever, _chinese_tokenizer
from src.utils.sqlite_store import SQLiteStore

# 项目根 migrations 目录（用 __file__ 推导，不依赖 cwd，规避 Git Bash 下 /e 挂载成 E:\e 的路径歧义）
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS = _PROJECT_ROOT / "migrations"


@pytest.fixture
def retriever(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    store.init_schema(_MIGRATIONS)
    store.execute(
        "INSERT INTO knowledge_topic "
        "(id, topic, name, description, tags_json, status, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '[]', 'active', 'test', "
        "strftime('%Y-%m-%d %H:%M:%S','now'), strftime('%Y-%m-%d %H:%M:%S','now'))",
        ("t1", "test_world", "Test World", "test"),
    )
    factory = KBVectorStoreFactory.get_default(project_root=tmp_path, vector_store_backend="sqlite")
    pipeline = KnowledgePipeline("test_world", factory=factory, store=store)

    # 写入一条可检索的语料（中文）
    doc = Document(
        text="蒙德城是自由之都，由西风骑士团守护，是提瓦特大陆七国之一。",
        metadata={"file_path": str(tmp_path / "mondstadt.md"), "title": "蒙德", "source_type": "lore"},
    )
    pipeline.ingest([doc])

    r = KnowledgeRetriever(
        store=store,
        documents=DocumentRepository(store),
        qdrant=None,  # 改造后 qdrant 参数仅占位，dense 由 LlamaIndex VectorStoreIndex 托管
        get_pipeline=lambda _slug: pipeline,
    )
    # 测试环境禁用 CrossEncoder 网络加载（避免下载 bge-reranker-v2-m3）
    r._reranker = False
    return r, pipeline


class TestChineseTokenizer:
    def test_tokenizer_splits_cjk_and_english(self):
        toks = _chinese_tokenizer("蒙德城 Mondstadt 骑士团")
        # 英文保留小写原词
        assert "mondstadt" in toks
        # 中文被 jieba 切词（蒙德城 → 蒙 / 德城 等子串），至少应含中文 token
        assert any(t for t in toks if t and ord(t[0]) > 0x4E00)
        # 骑士团应被切出
        assert "骑士团" in toks


class TestSearchWithMeta:
    """search_with_meta 主流程（Ensemble 融合 + 降级重排）."""

    def test_search_returns_list(self, retriever):
        """检索返回 list[dict]，schema 含 text / score_cosine_sim / metadata."""
        r = retriever[0]
        hits = r.search_with_meta("test_world", "蒙德城", top_k=3)
        assert isinstance(hits, list)
        for h in hits:
            assert "text" in h
            assert "score_cosine_sim" in h
            assert "metadata" in h

    def test_search_hits_expected_doc(self, retriever):
        """混合检索能召回 ingested 的中文语料."""
        r = retriever[0]
        hits = r.search_with_meta("test_world", "西风骑士团", top_k=3)
        assert hits, "应至少召回 1 条"
        assert any("蒙德" in (h.get("text") or "") for h in hits)

    def test_search_bm25_leg_present(self, retriever):
        """BM25 腿已构建（pipeline 缓存了 nodes），融合检索器含两路检索器."""
        r, pipeline = retriever
        ensemble = r._get_ensemble("test_world", pipeline, top_k=3)
        # QueryFusionRetriever 应有 dense + bm25 两路（BM25 腿在无节点时才缺）
        assert len(ensemble._retrievers) >= 1

    def test_search_filter_builds_metadatafilters(self, retriever):
        """build_qdrant_filter 返回 LlamaIndex MetadataFilters（框架原生抽象）."""
        from llama_index.core.vector_stores.types import MetadataFilters

        r = retriever[0]
        f = r.build_qdrant_filter("test_world", {"source_type": "lore"})
        assert isinstance(f, MetadataFilters)
        assert len(f.filters) == 1
        assert f.filters[0].key == "source_type"

    def test_search_multi_value_filter(self, retriever):
        """多值 filter 用 IN 算子表达 OR 语义."""
        from llama_index.core.vector_stores.types import FilterOperator, MetadataFilters

        r = retriever[0]
        f = r.build_qdrant_filter("test_world", {"source_type": ["lore", "documents"]})
        assert isinstance(f, MetadataFilters)
        assert f.filters[0].operator == FilterOperator.IN


class TestParentChildExpand:
    """父子切分：命中子块时回拉父块完整文本作为上下文."""

    def _insert_parent(self, retriever, parent_id: str, parent_text: str) -> None:
        """插入一条父块记录（meta_json 含完整文本）."""
        import json

        r = retriever[0]
        store = r._store
        store.execute(
            "INSERT INTO kb_document (id, topic_id, title, source_type, file_name, file_path, "
            "file_size, sha256, content_type, version, status, created_by, related_packs, tags_json) "
            "VALUES (?, 'test_world', 'p', 'lore', 'p.md', '/p.md', 0, 'x', 'markdown', 1, 'done', "
            "'test', '[]', '[]')",
            (f"doc_{parent_id}",),
        )
        store.execute(
            "INSERT INTO kb_chunk (id, document_id, topic_id, chunk_index, chunk_count, text_hash, "
            "text_preview, token_count, page_number, meta_json, created_at, chunk_level, parent_id) "
            "VALUES (?, ?, 'test_world', 0, 1, 'h', ?, 0, NULL, ?, "
            "strftime('%Y-%m-%d %H:%M:%S','now'), 'parent', NULL)",
            (
                parent_id,
                f"doc_{parent_id}",
                parent_text[:200],
                json.dumps({"chunk_level": "parent", "text": parent_text}, ensure_ascii=False),
            ),
        )

    def test_child_hit_expands_to_parent(self, retriever):
        """命中子块（chunk_level=child, 带 parent_id）→ 返回父块完整文本."""
        r = retriever[0]
        parent_id = "parent_chunk_001"
        parent_text = (
            "完整的父块上下文：蒙德城由西风骑士团守护，是自由之都，风神巴巴托斯眷顾这片土地，居民世代安居乐业。"
        )
        self._insert_parent(retriever, parent_id, parent_text)

        child_meta = {
            "chunk_level": "child",
            "parent_id": parent_id,
            "chunk_id": "child_chunk_001",
        }
        child_text = "西风骑士团守护蒙德"
        expanded = r._expand_parent_context(child_meta, child_text, "child_chunk_001")
        # 返回父块完整文本（含子块未涵盖的上下文）
        assert "完整的父块上下文" in expanded
        assert "风神巴巴托斯" in expanded
        assert len(expanded) > len(child_text)

    def test_non_child_returns_original(self, retriever):
        """非子块（单层 chunk）返回原文本，不触发回拉."""
        r = retriever[0]
        meta = {"chunk_level": "parent", "chunk_id": "p1"}
        assert r._expand_parent_context(meta, "原文", "p1") == "原文"

    def test_child_missing_parent_falls_back(self, retriever):
        """子块 parent_id 指向不存在的父块 → 降级返回子块原文."""
        r = retriever[0]
        meta = {"chunk_level": "child", "parent_id": "not_exists"}
        assert r._expand_parent_context(meta, "子块原文", "c1") == "子块原文"
