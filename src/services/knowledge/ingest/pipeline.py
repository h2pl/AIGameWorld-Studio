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
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

# LlamaIndex 生态：Document / Pipeline / Node / Embedding
from llama_index.core import Document
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.schema import TextNode
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

# TEI 嵌入后端（可选）：仅当启用 TEI 时才需要。
# 懒导入，避免未安装该依赖时影响默认的本地 HuggingFaceEmbedding 链路。
try:  # pragma: no cover - 可选依赖
    from llama_index.embeddings.text_embeddings_inference import TextEmbeddingsInference
except Exception:  # noqa: BLE001
    TextEmbeddingsInference = None  # type: ignore[assignment]

from ....utils.sqlite_store import SQLiteStore  # noqa: F401  (对外暴露类型)
from ..index.vector_store import KBVectorStoreFactory

# 本地模块导入
from .reader import KnowledgeReader

logger = logging.getLogger(__name__)


def _resolve_bge_m3_model_name() -> str:
    """解析 BGE-M3 模型名 / Resolve BGE-M3 model name.

    优先返回本地 HF 缓存中 BAAI/bge-m3 的 snapshot 绝对路径，使离线环境下
    无需联网解析 revision 即可加载。兼容新旧两种 HF 缓存目录结构：
      - 新版：hub/models--BAAI--bge-m3/snapshots/<rev>/
      - 旧版：hub/models/BAAI--bge-m3/snapshots/<rev>/
    若均不存在则返回标准模型名 "BAAI/bge-m3"（触发联网下载）。
    """
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    candidates = [
        hf_home / "hub" / "models--BAAI--bge-m3" / "snapshots",
        hf_home / "hub" / "models" / "BAAI--bge-m3" / "snapshots",
    ]
    for base in candidates:
        if base.exists():
            snapshots = [p for p in base.iterdir() if p.is_dir()]
            if snapshots:
                # 取最新修改的 snapshot（通常即 main/master）
                latest = max(snapshots, key=lambda p: p.stat().st_mtime)
                return str(latest)
    return "BAAI/bge-m3"


# ----------------------------------------------------------------------------
# 进程级 BGE-M3 模型单例缓存
# ----------------------------------------------------------------------------
# 问题根因：HuggingFaceEmbedding 每次 KnowledgePipeline.__init__ 都会重新
# load_weights（BGE-M3 有 391 个权重分片，冷加载约 15s）。在 Windows 上，
# 模型冷加载期间如果进程收到 SIGINT（IDE「停止」按钮 / Ctrl+C / 后台窗口关闭），
# load_weights 会被中断，触发 llama_index 的重试逻辑 → 又慢加载 → 又被中断，
# 表现为「进程卡死无输出」。
#
# 修复：把底层 SentenceTransformer 模型做成进程级单例，第一次加载后缓存，
# 之后所有 KnowledgePipeline 实例复用，不再重复 load_weights。这样：
#   1) 冷加载只发生一次（即便是首跑，加载完就稳了）；
#   2) 后续 ingest（重建索引 / 多 topic）几乎零等待，不再暴露于 SIGINT 窗口；
#   3) pipeline 已通过 self._embedding_model._model.encode(...) 直接调用，
#      复用同一底层模型对象完全兼容。
# ----------------------------------------------------------------------------
_BGE_MODEL_CACHE: dict[str, Any] = {}


def _get_cached_bge_model(model_name: str, *, local_files_only: bool = False) -> Any:
    """返回进程级缓存的 SentenceTransformer 实例（首次加载后复用）.

    Parameters
    ----------
    model_name : str
        HuggingFace 模型名或本地路径（如 ``BAAI/bge-m3`` 或离线 snapshot 路径）。
    local_files_only : bool
        本地优先（离线）模式，不联网解析 revision。
    """
    key = f"{model_name}::{local_files_only}"
    cached = _BGE_MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    # 懒导入，避免未安装 sentence_transformers 时影响模块导入
    from sentence_transformers import SentenceTransformer

    load_kwargs: dict[str, Any] = {"trust_remote_code": True}
    if local_files_only:
        load_kwargs["local_files_only"] = True
    model = SentenceTransformer(model_name, **load_kwargs)
    _BGE_MODEL_CACHE[key] = model
    return model


def _resolve_tei_url() -> str | None:
    """解析 TEI embedding 服务地址 / Resolve TEI embedding server URL.

    仅在显式配置时返回地址，否则返回 None（走默认本地 HuggingFaceEmbedding）：
      - 优先读环境变量 ``TEI_URL``（如 http://localhost:8080）；
      - 其次读 ``config.yaml`` 的 ``embedding.tei_url``。
    返回 None 表示未启用 TEI，保持原有本地链路不变。
    """
    env_url = os.environ.get("TEI_URL")
    if env_url:
        return env_url.rstrip("/")
    try:
        import yaml as _yaml

        _cfg_path = Path(__file__).resolve().parents[3] / "config.yaml"
        if _cfg_path.exists():
            _cfg = _yaml.safe_load(_cfg_path.read_text(encoding="utf-8")) or {}
            _tei = (_cfg.get("embedding") or {}).get("tei_url")
            if _tei:
                return str(_tei).rstrip("/")
    except Exception:  # noqa: BLE001 - 配置缺失不影响本地默认链路
        pass
    return None


# 知识库文档处理流水线类：双写 SQLite 元数据 + 可插拔向量存储
class KnowledgePipeline:
    """知识库文档处理流水线（双写 SQLite 元数据 + 可插拔向量存储）."""

    # 构造函数：初始化 topic、向量库工厂、SQLiteStore、Reader + 构建 LlamaIndex IngestionPipeline
    def __init__(
        self,
        topic_id: str,
        factory: KBVectorStoreFactory | None = None,
        store: SQLiteStore | None = None,
        *,
        chunk_size: int = 800,
        chunk_overlap: int = 120,
        enrichers: list | None = None,
        use_parent_child: bool = True,
        parent_chunk_size: int = 1200,
        parent_chunk_overlap: int = 150,
        child_chunk_size: int = 400,
        child_chunk_overlap: int = 60,
    ):
        self.topic_id = topic_id
        self.collection_name = f"kb_{topic_id}"
        self._factory = factory or KBVectorStoreFactory.get_default()
        self._store = store
        self._reader = KnowledgeReader(enrichers=enrichers)
        # 保存切分参数，供 ingest 里手动切 chunk + 逐条 embed 使用
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        # 父子切分开关与粒度：父块=语义完整的大块（返回上下文，不嵌入），
        # 子块=小块（嵌入检索，命中后回拉所属父块）。默认开启。
        self.use_parent_child = use_parent_child
        self._parent_chunk_size = parent_chunk_size
        self._parent_chunk_overlap = parent_chunk_overlap
        self._child_chunk_size = child_chunk_size
        self._child_chunk_overlap = child_chunk_overlap
        # 记录被"更新"替换掉的旧版本 doc_id（用于清理其残留 chunks）
        self._obsolete_doc_ids: list[str] = []

        # BGE-M3 本地路径（ModelScope / HuggingFace 下载缓存）
        # 不硬编码 snapshot 目录名，离线优先探测本地缓存，避免：
        #   - 目录名是 main/其它 revision hash 时加载失败（之前踩过坑）；
        #   - 只认旧版 models/ 结构、不兼容新版 models--BAAI--bge-m3；
        #   - 本地无缓存时走联网下载（离线环境会超时/失败）。
        _bge_path = _resolve_bge_m3_model_name()

        # 切分：恢复 LlamaIndex 原生 SentenceSplitter 作为 IngestionPipeline 的 transformation，
        # 由框架负责「按语义边界切 + 注入 chunk 的 prev/next 关系元数据（include_prev_next_rel）」。
        # 这样检索端的 parent-context 展开应优先用框架内置的 node.relationships，而非手写 SQL 回拉。
        # 保留 chunk_size / chunk_overlap 作为字符窗口参数。
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

        # 离线优先：本地缓存命中时不走网络解析 revision
        # batch_size=64：在用户本地终端实测，BGE-M3 CPU 大 batch 吞吐更高。
        #   （之前 8 是怀疑 KeyboardInterrupt 与大 batch 相关而保守降低；用户在终端跑
        #    证明 KeyboardInterrupt 来自 IDE 命令环境，非 batch 大小。用 64 提升吞吐）

        def _build_local_model() -> HuggingFaceEmbedding:
            """构建本地 HuggingFaceEmbedding，进程级缓存整个实例.

            根因修复：原先每次 KnowledgePipeline.__init__ 都会让 HuggingFaceEmbedding
            重新 load_weights（BGE-M3 共 391 个权重分片，冷加载约 15s）。Windows 上
            模型冷加载期间若收到 SIGINT（IDE「停止」/Ctrl+C/后台窗口关闭），
            load_weights 被中断 → llama_index 重试 → 又慢加载 → 又被中断，表现为
            「进程卡死无输出」。

            现在把整个 HuggingFaceEmbedding 实例做进程级缓存：第一次构造时完成
            load_weights，之后所有 KnowledgePipeline 实例直接复用同一对象，彻底跳过
            重复的权重加载与 SIGINT 暴露窗口。底层 SentenceTransformer 也走
            _get_cached_bge_model 单例，双重保险。
            """
            _cache_key = f"hf::{_bge_path}::64"
            _cached_emb = _BGE_MODEL_CACHE.get(_cache_key)
            if _cached_emb is not None:
                return _cached_emb
            # 预载底层 SentenceTransformer（进程级单例，冷加载一次）
            _cached_model = _get_cached_bge_model(_bge_path, local_files_only=(_bge_path != "BAAI/bge-m3"))
            # 构造 HuggingFaceEmbedding 外壳
            _hf_kwargs: dict[str, Any] = {
                "trust_remote_code": True,
                "embed_batch_size": 64,
                "model_name": _bge_path,
            }
            if _bge_path != "BAAI/bge-m3":
                _hf_kwargs["local_files_only"] = True
            try:
                _emb = HuggingFaceEmbedding(**_hf_kwargs)
            except Exception:
                # 极小概率：本地路径构造失败，用占位构造再换底层模型
                _emb = HuggingFaceEmbedding(model_name="BAAI/bge-m3", trust_remote_code=True, embed_batch_size=64)
            # 用已缓存模型对象替换外壳内部 _model，确保零重复 load_weights
            try:
                object.__setattr__(_emb, "_model", _cached_model)
            except Exception:
                _emb._model = _cached_model
            _BGE_MODEL_CACHE[_cache_key] = _emb
            return _emb

        # TEI 可选后端：仅当显式配置 TEI_URL / config.yaml embedding.tei_url 时启用。
        # 默认（未配置）走本地模型，行为与原 develop 版本完全一致。
        # 注意：当前 TEI 容器多为 CPU + candle backend（强制 batch≤8），单条往返比本地慢
        # 约 10 倍；仅当 TEI 跑在 GPU（如 RTX 50 系 Blackwell 镜像）时才建议启用。
        _tei_url = _resolve_tei_url()
        if _tei_url and TextEmbeddingsInference is not None:
            try:
                self._embedding_model = TextEmbeddingsInference(model_name="BAAI/bge-m3", base_url=_tei_url)
                logger.info("[kb] TEI embedding 已连接 (base_url=%s, model=BAAI/bge-m3)", _tei_url)
            except Exception as e:
                logger.warning("[kb] TEI 连接失败 (%s)，回退本地 HuggingFaceEmbedding", e)
                self._embedding_model = _build_local_model()
        else:
            self._embedding_model = _build_local_model()
            if _bge_path != "BAAI/bge-m3":
                logger.info(
                    "[kb] BGE-M3 本地 embedding 模型加载 (path=%s, embed_batch_size=64, offline)",
                    _bge_path,
                )
            else:
                logger.info(
                    "[kb] BGE-M3 本地 embedding 模型加载 (model=%s, embed_batch_size=64)",
                    _bge_path,
                )

        # 切分器：父块（语义完整大块）+ 子块（小块，供检索嵌入）。
        #   - 父块：chunk_size=parent_chunk_size，只存 SQLite（不嵌入），作为返回上下文。
        #   - 子块：chunk_size=child_chunk_size，嵌入 Qdrant，命中后按 parent_id 回拉父块。
        # 均用 LlamaIndex 原生 SentenceSplitter（按语义边界切 + 注入 prev/next 关系）。
        from llama_index.core.node_parser import SentenceSplitter

        if use_parent_child:
            self._text_splitter = SentenceSplitter(
                chunk_size=parent_chunk_size,
                chunk_overlap=parent_chunk_overlap,
                include_metadata=True,
                include_prev_next_rel=True,
            )
            self._child_splitter = SentenceSplitter(
                chunk_size=child_chunk_size,
                chunk_overlap=child_chunk_overlap,
                include_metadata=True,
                include_prev_next_rel=True,
            )
        else:
            # 兼容旧单层模式：父块尺寸 == 单层 chunk 尺寸，无子块
            self._text_splitter = SentenceSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                include_metadata=True,
                include_prev_next_rel=True,
            )
            self._child_splitter = None
        self._pipeline = IngestionPipeline(
            transformations=[
                self._text_splitter,
                self._embedding_model,
            ],
        )
        # 独立拿 LlamaIndex VectorStore 对象（KBVectorStoreFactory 负责后端路由）
        self._vector_store = self._factory.get_vector_store(topic_id)

        # 幂等确保 kb_chunk 有 page_number / chunk_level / parent_id 列（SQLite 不支持 IF NOT EXISTS）
        if self._store is not None:
            for _ddl in (
                "ALTER TABLE kb_chunk ADD COLUMN page_number INTEGER",
                "ALTER TABLE kb_chunk ADD COLUMN chunk_level TEXT",
                "ALTER TABLE kb_chunk ADD COLUMN parent_id TEXT",
            ):
                try:
                    self._store.execute(_ddl)
                except Exception:
                    pass
            # parent_id 索引：子块命中后按父块回拉上下文
            try:
                self._store.execute("CREATE INDEX IF NOT EXISTS idx_kb_chunk_parent ON kb_chunk(parent_id)")
            except Exception:
                pass

        # 进程内缓存最近一次 ingest 的 nodes（供检索端 BM25Retriever 使用）
        self._last_nodes: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # PDF 感知切分（LangChain RecursiveCharacterTextSplitter）
    # ------------------------------------------------------------------

    def _split_documents_pdf_aware(self, documents: list[Document]) -> list[TextNode]:
        """切分：委托 LlamaIndex 原生 SentenceSplitter（IngestionPipeline 同款）.

        - 由框架负责「按语义边界切 + 注入 chunk 的 prev/next 关系（include_prev_next_rel）」。
        - 在框架切出的 node 上补充业务元数据：page_number / chunk_index / chunk_count。
          PDF：reader 已给每个 Document 注入 ``page_number``（来自 page_label），
          我们按同 page 的 node 批注 page_number，不跨页边界。
        - 不在此做 embedding（embedding 在 ingest 里手写批量推理以规避 Windows 子线程不稳定）。

        返回 LlamaIndex TextNode 列表（与后续 embedding 双写逻辑兼容）。
        """
        if not documents:
            return []

        # 用与 IngestionPipeline 相同的 SentenceSplitter 在内存里切（不触发框架 embedding）。
        nodes = self._text_splitter.get_nodes_from_documents(documents)

        # 补充业务元数据：page_number 来自 Document.metadata.page_label/page_number；
        # chunk_index / chunk_count 在同 Document 内重排；doc_id 用 ref_doc_id 兜底
        # （SentenceSplitter 生成的 node 可能不含 metadata.doc_id，只含 ref_doc_id）。
        by_doc: dict[str, list[TextNode]] = {}
        for n in nodes:
            src = n.metadata.get("doc_id") or getattr(n, "ref_doc_id", None) or n.metadata.get("file_path") or "_"
            by_doc.setdefault(src, []).append(n)
        for doc_nodes in by_doc.values():
            chunk_count = len(doc_nodes)
            for idx, n in enumerate(doc_nodes):
                meta = dict(n.metadata or {})
                # 统一补真实 doc_id（若缺失则用 ref_doc_id）
                if not meta.get("doc_id"):
                    meta["doc_id"] = getattr(n, "ref_doc_id", None) or meta.get("file_path") or "_"
                # PDF 页码：reader 注入的 page_label/page_number 透传到 chunk
                page = meta.get("page_number") or meta.get("page_label")
                try:
                    page_num = int(page) if page is not None else None
                except TypeError, ValueError:
                    page_num = None
                meta["chunk_index"] = idx
                meta["chunk_count"] = chunk_count
                if page_num is not None:
                    meta["page_number"] = page_num
                try:
                    object.__setattr__(n, "metadata", meta)
                except Exception:
                    pass

        return [n for n in nodes if (n.get_content() or "").strip()]

    def _split_parent_child_pdf_aware(self, documents: list[Document]) -> tuple[list[TextNode], list[TextNode] | None]:
        """父子两级切分：父块（大）→ 子块（小）。

        Returns
        -------
        (parent_nodes, child_nodes)
            - parent_nodes：父块 TextNode 列表（注入 page_number/chunk_index/chunk_count），
              只存 SQLite，不嵌入。
            - child_nodes：子块 TextNode 列表（每个挂 parent_id 关联所属父块），嵌入 Qdrant。
              单层模式（``use_parent_child=False``）返回 ``None``，此时 parent_nodes 即单层块。
        """
        parents = self._split_documents_pdf_aware(documents)
        if self._child_splitter is None:
            return parents, None

        # 每个父块再切成若干子块，并记录所属父块索引（父块的 document+chunk_index）。
        # 注意：子块切分只传纯文本，不继承父块 metadata——否则长 metadata 会被 SentenceSplitter
        # 计入切分窗口，既可能抛 "Metadata length > chunk size"，也会稀释子块检索质量。
        # 切完后手动挂上需要继承的字段（page_number / doc_id / parent 关联）。
        children: list[TextNode] = []
        for p in parents:
            p_meta = dict(p.metadata or {})
            sub_nodes = self._child_splitter.get_nodes_from_documents([Document(text=p.get_content() or "")])
            for c in sub_nodes:
                c_meta = dict(c.metadata or {})
                # 继承父块归属字段（不含 file_path/file_name 等长字符串，避免污染元数据）
                for key in ("page_number", "doc_id", "topic_id"):
                    if key in p_meta and key not in c_meta:
                        c_meta[key] = p_meta[key]
                # 标记子块归属的父块（用父块的 doc_id + 父块序号，待入库后解析成父块 chunk_id）
                c_meta["parent_doc_id"] = p_meta.get("doc_id") or p_meta.get("file_path") or "_"
                c_meta["parent_index"] = p_meta.get("chunk_index")
                try:
                    object.__setattr__(c, "metadata", c_meta)
                except Exception:
                    pass
                children.append(c)
        return parents, children

    # ingest：主入口 — 对 documents 依次执行 upsert_meta → pipeline.run → 写 kb_chunk 批量入库
    def ingest(
        self,
        documents: list[Document],
        on_embed_progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """执行流水线：切 chunk → embedding → 双写.

        Parameters
        ----------
        documents : list[Document]
            待入库文档。
        on_embed_progress : callable, optional
            嵌入进度回调 ``on_embed_progress(done_batches, total_batches)``。
            兼容 manager.ingest_files 的调用约定；当前实现为单进程批量推理，
            会在开始前/结束后各回调一次（done=0/total 与 done=total/total）。

        Returns
        -------
        dict
            ``{"chunks": int, "doc_ids": [uuid...], "chunk_ids": [uuid...]}``
        """
        if on_embed_progress is not None:
            # 单进程批量推理：无中间批次，开始即标记进度
            try:
                on_embed_progress(0, 1)
            except Exception:
                pass
        if not documents:
            return {"chunks": 0, "doc_ids": [], "chunk_ids": []}

        # 第一步：逐个 Document 写 kb_document 元数据，拿到 doc_id
        # 重置被"更新"替换的旧 doc_id 记录（每次 ingest 独立）
        self._obsolete_doc_ids = []
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

        # 第二步：确保向量库 collection 就绪，然后切 chunk + embedding 生成 nodes。
        #   使用 sentence-transformers 原生的 encode() 批量推理，而非 LlamaIndex 封装的
        #   get_text_embedding()（其内部 tenacity 重试 + 子线程在 Windows 上不稳定）。
        #   挂完 chunk_id + 业务 metadata + 写 kb_chunk 后再写向量库（保证 payload 完整）。
        self._ensure_vector_store_collection()

        # 2.5 步：幂等清理 —— 删除需要重建的 doc 的旧 chunks（SQLite + Qdrant），避免重复/残留。
        #   - 本次 doc_id（复用旧 doc_id 的重复 ingest）→ 删旧 chunks 重建，防止 376→752 翻倍
        #   - _obsolete_doc_ids（文件内容变化时被替换的旧版本 doc_id）→ 清掉残留 chunks
        _cleanup_ids = list(doc_ids) + list(self._obsolete_doc_ids)
        if self._store is not None and _cleanup_ids:
            for _doc_id in _cleanup_ids:
                _old = self._store.fetch_all("SELECT id FROM kb_chunk WHERE document_id = ?", (_doc_id,))
                if not _old:
                    continue
                with self._store.transaction():
                    self._store.execute("DELETE FROM kb_chunk WHERE document_id = ?", (_doc_id,))
                # 同步删 Qdrant 该文档的旧 points（QdrantVectorStore.delete 按 doc_id 过滤）
                try:
                    self._vector_store.delete(ref_doc_id=_doc_id)
                except Exception:
                    logger.warning("[kb] 清理旧 Qdrant points 失败 doc_id=%s", _doc_id)

        # 切分：父子两级（父块语义完整，子块嵌入检索）或旧单层模式。
        # 返回 (parent_nodes, child_nodes)；单层模式 child_nodes 为 None。
        parent_nodes, child_nodes = self._split_parent_child_pdf_aware(documents)
        nodes = child_nodes if child_nodes is not None else parent_nodes

        # 过滤空/极短 chunk：PDF 的封面/版权/空白页会被切成空 node，无检索价值，不应入库。
        #   否则检索会命中这些空 chunk（正文为空），且浪费 embedding。
        min_chunk_chars = 10
        nodes = [n for n in nodes if len((n.get_content() or "").strip()) >= min_chunk_chars]
        if parent_nodes is not None:
            parent_nodes = [n for n in parent_nodes if len((n.get_content() or "").strip()) >= min_chunk_chars]

        _texts = [n.get_content() for n in nodes]
        # 分批 batch encode（主线程，绕开 llama-index tenacity 子线程）+ KeyboardInterrupt 重试。
        # batch=64：用户终端实测键盘中断来自 IDE 环境而非 batch，用大 batch 提升 CPU 吞吐。
        # 保留 KeyboardInterrupt 重试兜底应对 IDE 环境偶发 SIGINT。
        # ⚠️ 关键：每完成一个 batch 都打印进度日志。embed 是 CPU 长耗时推理，单批可能要
        #   几十秒~几分钟，期间"无日志"只是因为正在算，不代表卡死/进程死了——切勿据此 kill 进程。
        embed_batch = min(self._embedding_model.embed_batch_size or 64, 64)
        _total_texts = len(_texts)
        _embeddings: list = []
        logger.info("[kb] 开始 embedding 共 %d 个 chunk，batch_size=%d", _total_texts, embed_batch)
        for _batch_no, chunk_start in enumerate(range(0, _total_texts, embed_batch), start=1):
            chunk_texts = _texts[chunk_start : chunk_start + embed_batch]
            logger.info(
                "[kb] ▶ 编码 batch %d/%d（chunk %d~%d / 共 %d）...",
                _batch_no,
                (max(_total_texts - 1, 0) // embed_batch) + 1,
                chunk_start,
                min(chunk_start + embed_batch, _total_texts) - 1,
                _total_texts,
            )
            # IDE 命令环境（Windows）会在 embed 长耗时推理期间向进程组发 SIGINT，
            # 触发 KeyboardInterrupt，导致整批索引静默中断。这里改为**无限重试**，
            # 每次中断后短暂停顿再重试同一 batch，直到成功——避免索引半途而废。
            # （真实错误如 OOM 会抛出非 KeyboardInterrupt 异常，照常上抛。）
            encode_attempt = 0
            while True:
                try:
                    emb_array = self._embedding_model._model.encode(
                        chunk_texts, batch_size=embed_batch, show_progress_bar=False
                    )
                    _embeddings.extend(emb_array.tolist())
                    break
                except KeyboardInterrupt:
                    encode_attempt += 1
                    logger.warning(
                        "[kb] ⚠ batch %d encode 被 KeyboardInterrupt 中断（第%d次），重试同一 batch...",
                        _batch_no,
                        encode_attempt,
                    )
                    # 短暂停顿，避免被连续 SIGINT 空转烧 CPU
                    import time as _t

                    _t.sleep(1.0)
            logger.info(
                "[kb] ✅ batch %d 完成，已编码 %d/%d 个 chunk",
                _batch_no,
                len(_embeddings),
                _total_texts,
            )
        for n, e in zip(nodes, _embeddings):
            try:
                object.__setattr__(n, "embedding", e)
            except Exception:
                pass

        # 第三步：为父块/子块分配 UUID + 元数据，准备批量写 kb_chunk。
        #   - 父块（parent）：只写 SQLite（chunk_level='parent', parent_id=NULL），不嵌入。
        #   - 子块（child）：写 SQLite（chunk_level='child', parent_id=所属父块 id）+ 嵌入 Qdrant。
        # 先为父块建 (doc_id, chunk_index) → chunk_id 映射，供子块解析 parent_id。
        chunk_ids: list[str] = []
        all_chunk_rows: list[tuple] = []

        # ── 父块：分配 id 并入库 ────────────────────────────────
        parent_id_by_key: dict[tuple[str, int], str] = {}
        if parent_nodes:
            for pi, pn in enumerate(parent_nodes):
                parent_id = SQLiteStore.new_id()
                pdoc = pn.metadata.get("doc_id") or pn.metadata.get("file_path") or "_"
                p_idx = int(pn.metadata.get("chunk_index") or pi)
                parent_id_by_key[(str(pdoc), p_idx)] = parent_id
                _pn_meta = dict(pn.metadata or {})
                _pn_meta["chunk_id"] = parent_id
                _pn_meta["chunk_level"] = "parent"
                _pn_meta["parent_id"] = None
                _pn_meta["topic_id"] = self.topic_id
                # 父块完整文本写入 meta_json，供检索端命中子块时回拉完整上下文
                _pn_meta["text"] = pn.get_content() or ""
                try:
                    object.__setattr__(pn, "metadata", _pn_meta)
                except Exception:
                    pass
                try:
                    _pp = int(_pn_meta.get("page_number") or 0) or None
                except TypeError, ValueError:
                    _pp = None
                all_chunk_rows.append(
                    (
                        parent_id,
                        pn.metadata.get("doc_id") or "",
                        self.topic_id,
                        p_idx,
                        len(parent_nodes),
                        hashlib.sha256((pn.get_content() or "").encode("utf-8")).hexdigest(),
                        (pn.get_content() or "")[:200],
                        0,
                        _pp,
                        json.dumps(_pn_meta, ensure_ascii=False),
                        SQLiteStore.now_str(),
                        "parent",
                        None,
                    )
                )

        # ── 子块：分配 id，解析 parent_id，入库（含嵌入向量） ──
        # 注意：分组优先用 metadata["doc_id"]（父块/子块均已统一注入真实 doc_id），
        # ref_doc_id 仅作兜底——子块是临时 Document 切出的 node，ref_doc_id 是随机 UUID，
        # 若优先用它会导致 document_id 违反外键。
        by_doc: dict[str, list[tuple[int, object]]] = {}
        for i, n in enumerate(nodes):
            md = n.metadata or {}
            ref = md.get("doc_id") or getattr(n, "ref_doc_id", None) or "_"
            if ref not in by_doc:
                by_doc[ref] = []
            by_doc[ref].append((i, n))

        for doc_id, items in by_doc.items():
            chunk_count = len(items)
            for idx_in_doc, (_global_idx, node) in enumerate(items):
                chunk_id = SQLiteStore.new_id()
                # Qdrant 要求 point ID 为标准 UUID 格式（带 - 分隔符），否则 400 Bad Request。
                # SQLiteStore.new_id() 返回 32 位 hex（无 -），需要转成 xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx。
                chunk_uuid = f"{chunk_id[:8]}-{chunk_id[8:12]}-{chunk_id[12:16]}-{chunk_id[16:20]}-{chunk_id[20:]}"
                text = getattr(node, "text", "") or ""
                text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                preview = text[:200]
                node_meta = dict(node.metadata) if node.metadata else {}
                node_meta["chunk_id"] = chunk_id
                node_meta["chunk_index"] = idx_in_doc
                node_meta["chunk_count"] = chunk_count
                node_meta["text_hash"] = text_hash
                node_meta["topic_id"] = self.topic_id
                node_meta["chunk_level"] = "child" if parent_nodes else "parent"
                # 解析子块所属父块 chunk_id
                if parent_nodes:
                    pdoc = node_meta.get("parent_doc_id") or node_meta.get("doc_id") or doc_id or "_"
                    p_idx = node_meta.get("parent_index")
                    try:
                        p_idx = int(p_idx) if p_idx is not None else 0
                    except TypeError, ValueError:
                        p_idx = 0
                    parent_id = parent_id_by_key.get((str(pdoc), p_idx))
                    node_meta["parent_id"] = parent_id
                else:
                    node_meta["parent_id"] = None
                # 提取 page_number（PDF 感知切分写入的元数据），写入 kb_chunk.page_number 列
                try:
                    _pn = node_meta.get("page_number")
                    page_number = int(_pn) if _pn is not None else None
                except TypeError, ValueError:
                    page_number = None
                try:
                    object.__setattr__(node, "metadata", node_meta)
                except Exception:
                    pass
                meta_json = json.dumps(node_meta, ensure_ascii=False)
                try:
                    object.__setattr__(node, "id_", chunk_uuid)
                except Exception:
                    pass
                all_chunk_rows.append(
                    (
                        chunk_id,
                        doc_id,
                        self.topic_id,
                        idx_in_doc,
                        chunk_count,
                        text_hash,
                        preview,
                        0,
                        page_number,
                        meta_json,
                        SQLiteStore.now_str(),
                        "child" if parent_nodes else "parent",
                        node_meta.get("parent_id"),
                    )
                )
                chunk_ids.append(chunk_id)

        # 第五步：事务批量写 SQLite — 先写所有 kb_chunk 行（父块 + 子块），再更新 kb_document 状态为 done
        if self._store is not None:
            with self._store.transaction():
                self._store.executemany(
                    """
                    INSERT OR REPLACE INTO kb_chunk
                        (id, document_id, topic_id, chunk_index, chunk_count,
                         text_hash, text_preview, token_count, page_number, meta_json, created_at,
                         chunk_level, parent_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    all_chunk_rows,
                )
                self._store.executemany(
                    """
                    UPDATE kb_document
                       SET status       = 'done',
                           updated_at   = strftime('%Y-%m-%d %H:%M:%S','now'),
                           error_msg    = NULL
                     WHERE id = ?
                       AND status != 'deleted'
                    """,
                    [(doc_id,) for doc_id in doc_ids],
                )

        # 第六步（顺序修复后）：node 已挂完 chunk_id + 完整 metadata，现在写向量库
        #   （256 条一批，避免 Qdrant gRPC/HTTP 包过大超时）
        for i in range(0, len(nodes), 256):
            self._vector_store.add(nodes[i : i + 256])

        # 缓存本次 ingest 的 nodes（进程内），供检索端构建 LlamaIndex 原生
        # ``BM25Retriever(nodes=...)`` 使用。BM25 是 in-memory lexical 检索，
        # 必须持有节点列表；pipeline 本就按 topic 缓存在 KnowledgeManager 里，
        # 重建索引（clear / 再次 ingest）会覆盖此缓存。
        self._last_nodes = list(nodes)

        if on_embed_progress is not None:
            try:
                on_embed_progress(1, 1)
            except Exception:
                pass

        return {"chunks": len(nodes), "doc_ids": doc_ids, "chunk_ids": chunk_ids}

    # clear：清空当前 topic 的知识库 — 先删向量 collection，再软删 kb_document，写审计
    def clear(self, *, created_by: str = "system") -> dict:
        """清空当前主题知识库：向量库删 collection + kb_document 软删."""
        # 先删底层向量 collection（通过 Factory 路由到底层实现）
        self._factory.delete_collection(self.topic_id)
        # 删除旧 collection 后重拿新的 vector_store（因为 backend 可能是 per-topic 缓存了旧对象）
        self._vector_store = self._factory.get_vector_store(self.topic_id)
        self._ensure_vector_store_collection()

        count_docs = 0
        if self._store is not None:
            now_str = SQLiteStore.now_str()
            # SQL: 软删 kb_document（status = deleted，填 updated_at / deleted_at）
            # 注意：SQLiteStore.execute 返回 int = rowcount，不是 cursor 对象
            count_docs = self._store.execute(
                """
                UPDATE kb_document
                   SET status     = 'deleted',
                       updated_at = ?,
                       deleted_at = ?
                 WHERE topic_id   = ?
                   AND status    != 'deleted'
                """,
                (now_str, now_str, self.topic_id),
            )
            # 软删 document 不会触发 ON DELETE CASCADE（因为只是 UPDATE），所以要真删 chunk
            self._store.execute(
                "DELETE FROM kb_chunk WHERE topic_id = ?",
                (self.topic_id,),
            )
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
        source_type = doc.metadata.get("source_type") or KnowledgeReader._infer_source_type(p, Path(p).parent)
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
            # 记录旧 doc_id，供 ingest 清理其残留 chunks（SQLite + Qdrant）
            if existing["id"] not in self._obsolete_doc_ids:
                self._obsolete_doc_ids.append(existing["id"])
            now_str = SQLiteStore.now_str()
            # SQL: 软删旧版本（status=deleted + 填时间戳）
            self._store.execute(
                """
                UPDATE kb_document
                   SET status     = 'deleted',
                       updated_at = ?,
                       deleted_at = ?
                 WHERE id = ?
                """,
                (now_str, now_str, existing["id"]),
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
            VALUES (?, ?, ?, ?, 'pending', 0, ?, strftime('%Y-%m-%d %H:%M:%S','now'))
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
            set_fields.append("finished_at = strftime('%Y-%m-%d %H:%M:%S','now')")
            if status == "running":
                set_fields.append("started_at  = strftime('%Y-%m-%d %H:%M:%S','now')")
        elif status == "running":
            set_fields.append("started_at  = IFNULL(started_at, strftime('%Y-%m-%d %H:%M:%S','now'))")
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%S','now'))
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

    def get_nodes(self) -> list:
        """返回最近一次 ingest 的 nodes（进程内缓存）.

        供检索端构建 LlamaIndex 原生 ``BM25Retriever(nodes=...)`` 使用（BM25 是
        in-memory lexical 检索，需要节点列表）。clear / 重新 ingest 会刷新缓存。
        若尚未 ingest 过则返回空列表。
        """
        return list(self._last_nodes)

    def _ensure_vector_store_collection(self):
        """保证 self._vector_store 指向最新、可写的 collection。

        - Qdrant：collection 被 factory.delete_collection 删除后，重新拿新的
          QdrantVectorStore 即可（它按 collection_name 寻址）；
        - Chroma：之前的旧实现，保留兼容。
        """
        try:
            if self._vector_store is None:
                self._vector_store = self._factory.get_vector_store(self.topic_id)

            # Qdrant：无需额外刷新，直接返回（保证 vs 是新的就行）
            vs_class = type(self._vector_store).__name__
            if vs_class == "QdrantVectorStore":
                return

            # 其它后端（SQLite/Chroma）：尝试重新拿一份覆盖
            try:
                new_store = self._factory.get_vector_store(self.topic_id)
                self._vector_store = new_store
            except Exception:
                pass
        except Exception:
            pass
