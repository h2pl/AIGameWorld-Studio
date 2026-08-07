"""KnowledgeRetriever — 知识库检索编排边界（混合检索，零磁盘 IO）.

对标主项目分层：「service 持有 repository / 检索编排器，storage 只做裸客户端」。
本文件是 manager 之下的检索编排边界，只负责：
- dense 腿：LlamaIndex ``VectorStoreIndex`` + ``VectorIndexRetriever``（原生 metadata filter）
- lexical 腿：``bm25s``（LlamaIndex 官方 BM25 后端，纯 Python，in-memory + jieba 中文分词）
  封装为 LlamaIndex ``BaseRetriever``，与 dense 腿统一接口
- 融合：LlamaIndex ``QueryFusionRetriever``（RRF 加权融合，框架原生；``EnsembleRetriever``
  在该版本 llama_index 已下架，``QueryFusionRetriever`` 是其官方替代且更通用）
- 重排：``sentence_transformers.CrossEncoder``（bge-reranker-v2-m3，LlamaIndex HuggingFaceRerank
  同款底层后端，现已下架，故直接用该官方后端，懒加载 + 降级）
- 组装兼容现有 API schema 的返回结构

全部基于 LlamaIndex / bm25s / sentence-transformers 原生抽象，不手写 BM25 / RRF / CrossEncoder 算法。
"""

from __future__ import annotations

import logging
from typing import Any

from llama_index.core import VectorStoreIndex
from llama_index.core.retrievers import (
    BaseRetriever,
    QueryFusionRetriever,
)
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.core.vector_stores.types import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)

from ....repository.document_repo import DocumentRepository
from ....utils.sqlite_store import SQLiteStore
from ..ingest.pipeline import KnowledgePipeline

_log = logging.getLogger(__name__)

# BGE-M3 cross-encoder reranker 模型（与 sentence_transformers 复用同一权重）
# 注意：LlamaIndex 的 llama-index-postprocessor-huggingface-rerank 集成包已从 PyPI
# 下架，其底层即 sentence_transformers.CrossEncoder，故直接用该官方后端（非自研算法）。
_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"


def _chinese_tokenizer(text: str) -> list[str]:
    """中文友好的 jieba 分词器，供 ``bm25s`` 检索使用.

    英文/数字保持小写词，中文用 jieba 切词，提升中文 lexical 召回。
    """
    import re

    import jieba

    text = (text or "").lower()
    # 英文/数字词直接保留
    tokens: list[str] = re.findall(r"[a-z0-9]+", text)
    # 中文（含 CJK 扩展）走 jieba 分词
    cjk = re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", text)
    if cjk:
        cjk_text = "".join(cjk)
        tokens.extend(jieba.lcut(cjk_text))
    return [t for t in tokens if t.strip()]


class _BM25LlamaRetriever(BaseRetriever):
    """用 ``bm25s``（LlamaIndex 官方 BM25 后端）封装的 LlamaIndex 检索器.

    ``bm25s`` 是 LlamaIndex ``BM25Retriever`` 的底层引擎，纯 Python、无需编译、
    in-memory 索引。这里继承框架原生 ``BaseRetriever``，使其能与 ``VectorIndexRetriever``
    一同交给 ``QueryFusionRetriever`` 融合——完全框架原生，不手写 BM25 算法、不落 SQLite。

    中文分词走 jieba（``_chinese_tokenizer``），英文保留原词（不词干化，避免引入
    需要 C++ 编译的 ``pystemmer`` 依赖）。
    """

    def __init__(self, nodes: list, *, similarity_top_k: int = 20):
        super().__init__()
        from bm25s import BM25

        self._nodes = list(nodes)
        self._top_k = similarity_top_k
        corpus = [_chinese_tokenizer(n.get_content() or "") for n in self._nodes]
        # bm25s 需要非空词表；空语料直接跳过建索引
        if any(corpus):
            self._retriever = BM25()
            self._retriever.index(bm25s_tokenize(corpus))
        else:
            self._retriever = None

    @classmethod
    def from_nodes(cls, nodes: list, *, similarity_top_k: int = 20) -> _BM25LlamaRetriever:
        return cls(nodes, similarity_top_k=similarity_top_k)

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        if self._retriever is None or not self._nodes:
            return []

        query_tokens = bm25s_tokenize([_chinese_tokenizer(query_bundle.query_str or "")])
        # bm25s 0.3.x 的 Results 是 namedtuple(documents, scores)，均为 (n_queries, k) 的 numpy 2D 数组
        results = self._retriever.retrieve(query_tokens, k=min(self._top_k, len(self._nodes)))
        # documents 即 doc indices；scores 已为相关性（越高越相关）
        doc_ids = results.documents[0] if hasattr(results, "documents") else results[0]
        scores = results.scores[0] if hasattr(results, "scores") else results[1]
        # numpy 标量转 Python int，避免 self._nodes[np.int64] 的索引歧义
        doc_ids = [int(i) for i in doc_ids]
        hits: list[NodeWithScore] = []
        for rank, idx in enumerate(doc_ids):
            if idx < 0 or idx >= len(self._nodes):
                continue
            try:
                score = float(scores[rank])
            except TypeError, IndexError:
                score = 0.0
            hits.append(NodeWithScore(node=self._nodes[idx], score=score))
        return hits


def bm25s_tokenize(corpus: list[list[str]]) -> Any:
    """把分词后的 token 列表转成 bm25s 需要的格式（纯 Python list[str]）.

    bm25s 接受 list[list[str]] 词表，这里直接透传（不再做 stemmer，避免 pystemmer 编译依赖）。
    """
    return corpus


class KnowledgeRetriever:
    """知识库检索编排 — dense + BM25(Ensemble) → CrossEncoder rerank.

    设计对齐 LlamaIndex 原生检索范式：
    - dense 召回走 LlamaIndex ``VectorStoreIndex`` + ``VectorIndexRetriever``（自带
      metadata filter，不再手写 Qdrant filter dict）；
    - lexical 召回走 ``_BM25LlamaRetriever``（bm25s 引擎，in-memory nodes，jieba 分词），
      封装为框架原生 ``BaseRetriever``；
    - 两路通过 ``QueryFusionRetriever`` 融合（框架原生 RRF 加权融合）；
    - 融合后用 ``sentence_transformers.CrossEncoder``（bge-reranker-v2-m3）做 cross-encoder 重排。
    """

    def __init__(
        self,
        *,
        store: SQLiteStore,
        documents: DocumentRepository,
        qdrant: Any,
        get_pipeline: Any,  # callable(topic_slug) -> KnowledgePipeline
    ):
        self._store = store
        self._documents = documents
        self._qdrant = qdrant
        self._get_pipeline = get_pipeline
        # VectorStoreIndex 按 topic 缓存（pricey 构建，避免每次检索重建）
        self._indexes: dict[str, VectorStoreIndex] = {}
        # BM25Retriever 按 topic 缓存（依赖 pipeline nodes，重建索引需失效）
        self._bm25: dict[str, Any] = {}
        # CrossEncoder reranker（全局懒加载缓存，HuggingFaceRerank 已下架，底层即此）
        self._reranker: Any = None

    # ------------------------------------------------------------------
    # 检索主入口
    # ------------------------------------------------------------------

    def search_with_meta(
        self,
        topic_slug: str,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.0,
        filters: dict | None = None,
    ) -> list[dict]:
        """混合检索：BGE-M3 dense + BM25(Ensemble 融合) → CrossEncoder 重排.

        - dense：LlamaIndex ``VectorStoreIndex`` + ``VectorIndexRetriever``（原生 metadata filter）
        - lexical：``_BM25LlamaRetriever``（bm25s 引擎，in-memory nodes，jieba 分词）
        - 融合：``QueryFusionRetriever``（框架原生 RRF 加权融合）
        - 重排：``sentence_transformers.CrossEncoder``（bge-reranker-v2-m3，不可用时跳过降级）
        """
        pipeline = self._get_pipeline(topic_slug)

        # ── 1) 构建融合检索器（dense + BM25，Ensemble 原生融合） ──
        ensemble = self._get_ensemble(topic_slug, pipeline, top_k=top_k)

        # QueryFusionRetriever.retrieve 返回 NodeWithScore 列表（已融合排序）
        try:
            nodes: list[NodeWithScore] = ensemble.retrieve(QueryBundle(query_str=query))
        except Exception as e:  # noqa: BLE001
            _log.warning("[kb] Ensemble 检索失败，退回纯 dense: %s", e)
            try:
                nodes = (
                    self._get_index(topic_slug, pipeline)
                    .as_retriever(
                        similarity_top_k=max(top_k * 3, 20),
                        filters=self.build_qdrant_filter(topic_slug, filters),
                    )
                    .retrieve(QueryBundle(query_str=query))
                )
            except Exception as e2:  # noqa: BLE001
                # 常见原因：Qdrant collection 不存在（该 topic 尚未建索引/向量未写入）。
                # 不应让 404 崩溃整个检索请求，优雅返回空结果。
                _log.warning("[kb] 纯 dense 检索也失败（可能 collection 未建索引）topic=%s: %s", topic_slug, e2)
                nodes = []

        # ── 2) Cross-Encoder 重排（bge-reranker-v2-m3，HuggingFaceRerank 已下架，改用 CrossEncoder） ──
        reranked = self._rerank(query, nodes, top_k=top_k)

        # ── 3) 组装返回（兼容现有 API schema） ────────────────────
        hits: list[dict] = []
        for n in reranked:
            meta = dict(n.metadata or {})
            score = float(getattr(n, "score", 0.0) or 0.0)
            if min_score > 0 and score < min_score:
                continue
            chunk_id = str(getattr(n, "id_", "") or meta.get("chunk_id") or "")
            text = (getattr(n, "text", "") or "") or self.extract_text_from_payload(meta, chunk_id)
            hits.append(
                {
                    "text": text,
                    "score_cosine_sim": score,
                    "distance": 1.0 - score if 0.0 <= score <= 1.0 else None,
                    "metadata": meta,
                }
            )
        return hits

    # ------------------------------------------------------------------
    # QueryFusionRetriever 缓存（dense + BM25，框架原生融合）
    # ------------------------------------------------------------------

    def _get_ensemble(self, topic_slug: str, pipeline: KnowledgePipeline, *, top_k: int) -> QueryFusionRetriever:
        """获取/缓存指定 topic 的 QueryFusionRetriever（dense + BM25 两路融合）.

        使用 LlamaIndex 框架原生的 ``QueryFusionRetriever``（融合检索器），
        设置 ``num_queries=1`` 关闭 LLM 查询扩展（只做混合融合，不额外调 LLM），
        ``mode=RECIPROCAL_RANK`` 走 reciprocal rank fusion（RRF，框架原生算法，非自研）。
        两路检索器：
          - dense：``VectorIndexRetriever``（BGE-M3 向量召回）
          - lexical：``_BM25LlamaRetriever``（bm25s 引擎，jieba 中文分词）
        通过 ``retriever_weights`` 加权（dense 0.6 / BM25 0.4）。

        ``QueryFusionRetriever.__init__`` 即便 ``num_queries=1`` 也会校验 ``llm``
        参数（默认解析 ``Settings.llm``，需要 ``llama-index-llms-openai``）。这里注入
        框架原生的 ``MockLLM`` 占位，避免生产/测试环境强依赖 OpenAI 包；由于
        ``num_queries=1`` 实际不会触发 LLM 查询生成，``MockLLM`` 仅用于满足构造校验。
        """
        from llama_index.core.llms.mock import MockLLM

        vector_retriever = self._get_index(topic_slug, pipeline).as_retriever(
            similarity_top_k=max(top_k * 3, 20),
            filters=self.build_qdrant_filter(topic_slug, None),
        )
        bm25_retriever = self._get_bm25(topic_slug, pipeline, top_k=max(top_k * 3, 20))

        retrievers = [vector_retriever]
        weights = [0.6]
        if bm25_retriever is not None:
            retrievers.append(bm25_retriever)
            weights.append(0.4)

        return QueryFusionRetriever(
            retrievers=retrievers,
            retriever_weights=weights,
            mode=FUSION_MODES.RECIPROCAL_RANK,
            num_queries=1,  # 关闭 LLM 查询扩展，纯做混合融合
            llm=MockLLM(),  # 占位，避免强依赖 openai 包（num_queries=1 不实际调用）
            similarity_top_k=max(top_k * 3, 20),
        )

    # ------------------------------------------------------------------
    # VectorStoreIndex 缓存
    # ------------------------------------------------------------------

    def _get_index(self, topic_slug: str, pipeline: KnowledgePipeline) -> VectorStoreIndex:
        """获取/缓存指定 topic 的 VectorStoreIndex（封装 LlamaIndex VectorStore）.

        复用 ``KnowledgePipeline`` 已构建的 LlamaIndex ``VectorStore`` 对象（Qdrant/
        Chroma/SQLite 适配），由框架负责底层连接与 collection 寻址。
        显式传入 ``embed_model=pipeline._embedding_model``（BGE-M3），避免 LlamaIndex
        默认去解析未安装的 ``llama-index-embeddings-openai``。
        """
        if topic_slug not in self._indexes:
            vs = pipeline._vector_store
            self._indexes[topic_slug] = VectorStoreIndex.from_vector_store(vs, embed_model=pipeline._embedding_model)
        return self._indexes[topic_slug]

    # ------------------------------------------------------------------
    # BM25 检索器缓存（bm25s 引擎，封装为 LlamaIndex BaseRetriever，jieba 中文分词）
    # ------------------------------------------------------------------

    def _get_bm25(self, topic_slug: str, pipeline: KnowledgePipeline, *, top_k: int) -> Any:
        """获取/缓存指定 topic 的 ``_BM25LlamaRetriever``（bm25s 引擎）.

        BM25 是 in-memory lexical 检索，需要 pipeline 最近一次 ingest 的 nodes。
        从 ``pipeline.get_nodes()`` 取节点；无节点时返回 None（降级为纯 dense）。
        用 jieba 中文分词器（非自研 BM25 算法，bm25s 是 LlamaIndex 官方 BM25 后端）。
        """
        if topic_slug in self._bm25:
            return self._bm25[topic_slug]

        nodes = pipeline.get_nodes()
        if not nodes:
            _log.info("[kb] topic=%s 无 ingest nodes，跳过 BM25 腿（纯 dense）", topic_slug)
            return None
        try:
            bm25 = _BM25LlamaRetriever.from_nodes(list(nodes), similarity_top_k=top_k)
            self._bm25[topic_slug] = bm25
            return bm25
        except Exception as e:  # noqa: BLE001
            _log.warning("[kb] BM25 检索器构建失败，降级纯 dense: %s", e)
            return None

    def invalidate(self, topic_slug: str) -> None:
        """使指定 topic 的索引/BM25 缓存失效（索引重建后调用）."""
        self._indexes.pop(topic_slug, None)
        self._bm25.pop(topic_slug, None)

    # ------------------------------------------------------------------
    # CrossEncoder 重排（bge-reranker-v2-m3，HuggingFaceRerank 已下架）
    # ------------------------------------------------------------------

    def _get_reranker(self) -> Any:
        """懒加载 bge-reranker-v2-m3 CrossEncoder（sentence_transformers 官方后端），全局缓存.

        LlamaIndex 的 ``HuggingFaceRerank`` 集成包已从 PyPI 下架，其底层就是
        ``sentence_transformers.CrossEncoder``，故直接用该官方后端（非自研算法）。
        """
        if self._reranker is not None:
            return self._reranker
        try:
            from sentence_transformers import CrossEncoder

            # 离线优先：只加载本地缓存的 reranker 权重，绝不触发联网下载。
            # 离线/无网环境下若联网解析 revision 会长时间阻塞（甚至被 SIGINT 中断），
            # 表现为检索「卡死」。本地无缓存时 local_files_only=True 会立即抛异常，
            # 走下方降级逻辑（跳过重排，返回融合排序结果），不再阻塞。
            try:
                self._reranker = CrossEncoder(_RERANKER_MODEL, trust_remote_code=True, local_files_only=True)
            except Exception:
                # 本地无缓存：不联网重试，直接标记不可用（降级纯融合检索）
                _log.warning(
                    "[kb] reranker 本地缓存未命中（%s），跳过重排（纯 dense+BM25 融合）",
                    _RERANKER_MODEL,
                )
                self._reranker = False
                return None
            _log.info("[kb] reranker 已加载: %s", _RERANKER_MODEL)
        except Exception as e:  # noqa: BLE001
            _log.warning("[kb] reranker 加载失败，跳过重排: %s", e)
            self._reranker = False  # 标记不可用，避免重复尝试
        return self._reranker if self._reranker else None

    def _rerank(self, query: str, nodes: list[NodeWithScore], *, top_k: int) -> list[NodeWithScore]:
        """用 bge-reranker-v2-m3 对融合后节点做 cross-encoder 重排.

        不可用时原样返回（降级）。
        """
        if not nodes:
            return nodes
        reranker = self._get_reranker()
        if reranker is None or reranker is False:
            return nodes[: max(1, top_k)]
        try:
            pairs = [(query, n.get_content()) for n in nodes]
            scores = reranker.predict(pairs, show_progress_bar=False)
            scored = list(zip(nodes, scores))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [n for n, _ in scored[: max(1, top_k)]]
        except Exception as e:  # noqa: BLE001
            _log.warning("[kb] rerank 失败，使用融合排序: %s", e)
            return nodes[: max(1, top_k)]

    # ------------------------------------------------------------------
    # 检索辅助
    # ------------------------------------------------------------------

    def extract_text_from_payload(self, meta: dict, chunk_id: str) -> str:
        """从 payload / SQLite 兜底提取 chunk 文本."""
        text = ""
        for k in ("text", "content"):
            if k in meta and isinstance(meta[k], str) and meta[k].strip():
                text = meta[k]
                break
        if not text and "_node_content" in meta and isinstance(meta["_node_content"], str):
            try:
                import json

                nc = json.loads(meta["_node_content"])
                if isinstance(nc, dict):
                    for kk in ("text", "__text__", "content"):
                        if kk in nc and isinstance(nc[kk], str) and nc[kk].strip():
                            text = nc[kk]
                            break
            except Exception:
                pass
        if not text and chunk_id:
            try:
                text = self._documents.chunk_text_preview(chunk_id)
            except Exception:
                pass
        return text

    def build_qdrant_filter(self, topic_slug: str, filters: dict | None) -> MetadataFilters | None:
        """把 filters 字典转成 LlamaIndex 原生 ``MetadataFilters``.

        使用 LlamaIndex 的 ``MetadataFilter`` / ``MetadataFilters`` 抽象（框架会自行
        转成后端原生的 Qdrant/Chroma filter），不再手写 Qdrant filter dict。

        filters 支持：
          - {"source_type": "lore"}              等值匹配（单值）
          - {"source_type": ["lore","documents"]} 多值 IN
          - {"file_name": "x.pdf"}               等值
          - {"page_number": 3}                   等值
        返回 ``MetadataFilters`` 或 None（无过滤）。
        """
        if not filters:
            return None
        conditions: list[MetadataFilter] = []
        for key in ("source_type", "file_name", "page_number", "content_type"):
            if key in filters and filters[key] is not None:
                val = filters[key]
                if isinstance(val, (list, tuple)):
                    # 多值 OR：用 IN 算子表达
                    conditions.append(
                        MetadataFilter(
                            key=key,
                            operator=FilterOperator.IN,
                            value=[str(v) for v in val],
                        )
                    )
                else:
                    conditions.append(MetadataFilter(key=key, operator=FilterOperator.EQ, value=val))
        if not conditions:
            return None
        return MetadataFilters(filters=conditions)
