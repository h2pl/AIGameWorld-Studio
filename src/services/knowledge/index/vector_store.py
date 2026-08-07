"""基于 SQLite + 纯 Python numpy-free 的向量存储.

设计权衡：
- 不想强依赖 numpy/faiss/chromadb 等重包（用户可能只跑 crawler 不做 RAG）；
- 向量维度通常 ≤ 2048（Open/Azure embedding 多为 1536），用 sqlite BLOB 存
  float32 二进制，检索时用 Python math.sqrt 算 L2 距离，O(N) 在万级 chunk
  规模也足够快（单 topic 通常 < 10k 块）；
- 需要规模更大时，用户可以用 VectorStore 协议（upsert/search/clear_all/close）
  自行换实现注入 KnowledgeManager / KnowledgeRetriever。

表结构：
    CREATE TABLE IF NOT EXISTS vectors (
        id TEXT PRIMARY KEY,          -- chunk_id (doc_id#chunk_idx)
        vec BLOB NOT NULL,            -- 向量 float32 二进制（dims * 4 字节）
        text TEXT NOT NULL,           -- 块原文，用于命中展示
        meta TEXT NOT NULL            -- 元信息 JSON 字符串
    );
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import struct
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llama_index.core.schema import TextNode
from llama_index.core.vector_stores.types import (
    BasePydanticVectorStore,
    VectorStoreQuery,
    VectorStoreQueryResult,
)
from pydantic import PrivateAttr


# 向量存储实现
class SQLiteVectorStore:
    # 构造：打开或创建 DB，保证表存在
    # 参数 path：SQLite 文件路径（不存在会创建）
    # 参数 dims：向量维度；>0 时启用 upsert 维度校验，0 表示不校验
    def __init__(self, path: str | Path, dims: int = 0):
        # 路径转 Path
        self._path = Path(path)
        # 父目录不存在则创建（连同父目录）
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 维度（0 = 不校验）
        self._dims = max(0, int(dims or 0))
        # 线程锁（SQLite 多线程同一连接写需要串行）
        self._lock = threading.RLock()
        # 打开 SQLite 连接（check_same_thread=False：允许跨线程访问，但用锁串行化）
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        # 读写性能：开启 WAL（并发读更好）；小 DB 无所谓
        try:
            # 启用 WAL 模式
            self._conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            # WAL 失败（某些平台/权限），退回默认 DELETE 模式也能跑
            pass
        # 同步级别 = NORMAL（WAL 下更稳，断电不丢数据的概率更高）
        try:
            # 设置同步级别
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            # 设置失败忽略
            pass
        # 建表（幂等）
        with self._lock:
            # 执行 CREATE TABLE
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vectors (
                    id TEXT PRIMARY KEY,
                    vec BLOB NOT NULL,
                    text TEXT NOT NULL,
                    meta TEXT NOT NULL
                )
                """
            )
            # 提交
            self._conn.commit()

    # 把 list[float] 转成 float32 的 bytes（与 numpy.ndarray.astype('float32').tobytes() 等价）
    # 参数 vec：float 序列
    # 返回 bytes：float32 二进制
    def _vec_to_blob(self, vec: Sequence[float]) -> bytes:
        # 用 struct.pack 打包；每个 float 占 4 字节 little-endian float
        # 长度就是 len(vec) * 4
        return struct.pack(f"<{len(vec)}f", *vec)

    # 把 BLOB 转回 list[float]（float32 解包）
    # 参数 blob：sqlite 返回的 bytes
    # 返回 list[float]：浮点向量
    def _blob_to_vec(self, blob: bytes) -> list[float]:
        # blob 字节数
        n = len(blob)
        # 必须是 4 的倍数
        if n % 4 != 0:
            # 坏数据，返回空
            return []
        # 元素个数
        cnt = n // 4
        # 用 struct.unpack 解包 little-endian float
        return list(struct.unpack(f"<{cnt}f", blob))

    # 计算 L2 距离平方（开方不影响排序，省掉 sqrt 开销）
    # 参数 a：向量 1
    # 参数 b：向量 2
    # 返回 float：L2 距离平方（越小越相似）
    def _dist_sq(self, a: list[float], b: list[float]) -> float:
        # 长度取最小（维度不匹配时按最小长度算，避免越界）
        n = min(len(a), len(b))
        # 初始距离平方
        s = 0.0
        # 遍历每个维度
        for i in range(n):
            # 差值
            d = a[i] - b[i]
            # 平方累加
            s += d * d
        # 返回距离平方
        return s

    # upsert 一条向量（按 id 覆盖）
    # 参数 id：chunk 唯一 ID（主键）
    # 参数 vec：向量 list[float]
    # 参数 text：块原文
    # 参数 meta：任意 JSON 可序列化元信息
    # 返回 None
    def upsert(self, id: str, vec: Sequence[float], text: str, meta: dict[str, Any] | None = None) -> None:
        # id 空字符串不合法
        if not id:
            # 静默跳过（不抛异常，让上层 manager continue）
            return
        # vec 不能为空
        if not vec:
            # 空向量跳过
            return
        # 维度校验：构造时 dims > 0 才校验
        if self._dims > 0 and len(vec) != self._dims:
            # 维度不匹配，抛 ValueError 给上层处理（manager 里 try/except 会捕获）
            raise ValueError(f"vector dim mismatch: expected {self._dims}, got {len(vec)}")
        # meta 序列化：None 当 {}
        try:
            # JSON 序列化
            meta_json = json.dumps(meta or {}, ensure_ascii=False)
        except Exception:
            # 序列化失败 → 空 dict
            meta_json = "{}"
        # 转 blob
        blob = self._vec_to_blob(list(vec))
        # 行锁
        with self._lock:
            # SQLite UPSERT：INSERT OR REPLACE
            self._conn.execute(
                """
                INSERT INTO vectors (id, vec, text, meta) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    vec  = excluded.vec,
                    text = excluded.text,
                    meta = excluded.meta
                """,
                # 参数 tuple
                (str(id), blob, str(text or ""), meta_json),
            )
            # 提交
            self._conn.commit()

    # 清空所有向量（重建索引前调用）
    # 返回 None
    def clear_all(self) -> None:
        # 加锁
        with self._lock:
            # 删全表
            self._conn.execute("DELETE FROM vectors;")
            # 提交
            self._conn.commit()

    # 向量检索：返回 top-k 最相似（L2 距离最小）的行
    # 参数 query_vec：查询向量
    # 参数 k：返回条数，默认 5
    # 返回 list[tuple[str, float, str, dict]]：(id, score, text, meta_dict)
    # score 当前实现使用 L2 距离（越小越相似）；上层 retriever 内部按 min-max 归一化，所以小分=好不影响
    def search(self, query_vec: Sequence[float], k: int = 5) -> list[tuple[str, float, str, dict]]:
        # 查询向量为空
        if not query_vec:
            return []
        # k 至少 1
        k = max(1, int(k or 1))
        # list 化
        q = list(query_vec)
        # 查询维度
        q_dims = len(q)

        # 加锁读
        with self._lock:
            # 游标执行：先全量拉到 Python 层做 O(N) 扫描（简单实现，规模足够小）
            cur = self._conn.execute("SELECT id, vec, text, meta FROM vectors")
            # 所有行
            rows = cur.fetchall()
        # 没行
        if not rows:
            # 空结果
            return []

        # 候选列表：(dist_sq, id, text, meta_str)
        candidates: list[tuple[float, str, str, str]] = []
        # 遍历每一行
        for row in rows:
            # id
            rid = row[0]
            # blob
            blob = row[1] or b""
            # text
            rtext = row[2] or ""
            # meta json
            rmeta = row[3] or "{}"
            # 解包向量
            rv = self._blob_to_vec(blob)
            # 向量空或维度差太远（比如重建过库但维度变了）跳过
            if not rv:
                continue
            # 维度不匹配但部分重叠时，按最小维度比距离（_dist_sq 内部做了），这里为了准确：至少要有 50% 维度重合才算
            rv_dims = len(rv)
            if rv_dims < q_dims // 2:
                # 维度太少，跳过（避免意外结果）
                continue
            # 距离平方
            d2 = self._dist_sq(q, rv)
            # 加候选
            candidates.append((d2, rid, rtext, rmeta))

        # 不到 k 条：取全部；否则按距离平方升序取前 k
        if len(candidates) <= k:
            # 全部
            chosen = candidates
        else:
            # 按距离平方升序
            candidates.sort(key=lambda t: t[0])
            # 前 k 条
            chosen = candidates[:k]

        # 组装返回：(id, score, text, meta_dict)
        # score 目前是 L2 距离平方；开方后更直观，但排序等价
        out: list[tuple[str, float, str, dict]] = []
        # 遍历 chosen
        for d2, rid, rtext, rmeta in chosen:
            # 距离（开方一下，给上层更直观的数字）
            score = math.sqrt(d2)
            # 反序列化 meta
            try:
                # 解析 JSON
                meta_dict: dict[str, Any] = json.loads(rmeta)
            except Exception:
                # 解析失败 → 空 dict
                meta_dict = {}
            # meta_dict 不是 dict 时兜底
            if not isinstance(meta_dict, dict):
                # 当空
                meta_dict = {}
            # 加入结果
            out.append((str(rid), score, str(rtext), meta_dict))
        # 返回
        return out

    # 按 id 删单条（暂未被上层使用，协议完整性保留）
    # 参数 id：chunk ID
    # 返回 None
    def delete(self, id: str) -> None:
        # id 空跳过
        if not id:
            return
        with self._lock:
            # 执行 DELETE
            self._conn.execute("DELETE FROM vectors WHERE id = ?", (str(id),))
            # 提交
            self._conn.commit()

    # 查当前有多少条向量（调试/监控用）
    # 返回 int：条目数
    def count(self) -> int:
        with self._lock:
            # 游标执行
            cur = self._conn.execute("SELECT COUNT(*) FROM vectors")
            # 取第一行第一列
            row = cur.fetchone()
        # 计数
        return int(row[0]) if row else 0

    # 关闭 SQLite 连接（幂等）
    # 返回 None
    def close(self) -> None:
        # 加锁
        with self._lock:
            # 连接未关闭
            if self._conn is not None:
                # 关闭
                try:
                    # 调用 sqlite close
                    self._conn.close()
                except Exception:
                    # close 失败忽略（可能已经关了）
                    pass
                # 置 None 防止重复关
                self._conn = None


# ============================================================================
# LlamaIndex VectorStore 协议适配层：把各种后端（SQLite/Qdrant/Chroma）统一成
# LlamaIndex IngestionPipeline 能用的 VectorStore 接口。
# ============================================================================


class _SQLiteLlamaStoreAdapter(BasePydanticVectorStore):
    """把自定义 SQLiteVectorStore 适配成 LlamaIndex 原生 ``BasePydanticVectorStore``.

    这样 ``IngestionPipeline(transformations=[...], vector_store=self)`` 可以直接原生接管：
    split → embed（pydantic 声明） → ``add(nodes)`` 自动双写（向量库 + kb_chunk 业务表）。

    ``kb_chunk`` 业务表双写通过 ``kb_writer`` 回调完成：LlamaIndex 在 ``pipeline.run()``
    时自动调用 ``add()``，回调把 node 映射成业务行写入 SQLite——不再是手写并行循环。

    实现要点：用 pydantic ``PrivateAttr`` 声明所有 ``_`` 前缀实例属性（store / collection_name
    等），并调用 ``super().__init__(stores_text=True)`` 初始化 LlamaIndex 契约字段
    （``stores_text`` 是 ``BasePydanticVectorStore`` 的字段，框架在
    ``VectorStoreIndex.from_vector_store`` 时会读取）。这是 pydantic v2 的原生约定写法。
    """

    _store: SQLiteVectorStore = PrivateAttr()
    _collection_name: str = PrivateAttr()
    _kb_writer: Any = PrivateAttr(default=None)
    _topic_id: Any = PrivateAttr(default=None)

    def __init__(
        self,
        store: SQLiteVectorStore,
        collection_name: str,
        *,
        kb_writer: Any | None = None,
        topic_id: str | None = None,
    ):
        """初始化.

        Parameters
        ----------
        store : SQLiteVectorStore
            底层向量存储（BLOB 向量）。
        collection_name : str
            collection 名（``kb_{topic_id}``）。
        kb_writer : callable | None
            业务双写回调 ``kb_writer(nodes) -> None``，在 add() 内被 LlamaIndex 触发，
            负责把 node 写进 kb_chunk 业务表。不传则只写向量库。
        topic_id : str | None
            主题 ID，透传给 kb_writer 使用。
        """
        # 先调用 pydantic 基类构造，初始化契约字段（stores_text 等）
        super().__init__(stores_text=True)
        # 再用 PrivateAttr 机制赋值（pydantic v2 下 _ 属性必须走 PrivateAttr 才能正常读写）
        self._store = store
        self._collection_name = collection_name
        self._kb_writer = kb_writer
        self._topic_id = topic_id

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @property
    def client(self) -> Any:
        """返回底层向量存储客户端（BasePydanticVectorStore 抽象要求）."""
        return self._store

    def _set_kb_writer(self, kb_writer: Any) -> None:
        """注入 kb_chunk 业务表双写回调（由 KnowledgePipeline 调用）."""
        self._kb_writer = kb_writer

    def _set_topic_id(self, topic_id: str) -> None:
        """设置 topic_id（透传给 kb_writer 使用）."""
        self._topic_id = topic_id

    def add(self, nodes: Sequence[Any], **kwargs: Any) -> list[str]:
        ids: list[str] = []
        for n in nodes:
            node_id = getattr(n, "id_", None) or getattr(n, "node_id", None) or ""
            if not node_id:
                continue
            embedding = getattr(n, "embedding", None) or None
            if embedding is None:
                continue
            text = getattr(n, "text", "") or ""
            meta_raw: Any = getattr(n, "metadata", None) or {}
            if isinstance(meta_raw, dict):
                meta = dict(meta_raw)
            else:
                meta = {"_raw": str(meta_raw)}
            meta["node_id"] = node_id
            meta["collection"] = self._collection_name
            self._store.upsert(str(node_id), list(embedding), text, meta)
            ids.append(str(node_id))

        # LlamaIndex 原生回调：把 node 双写到 kb_chunk 业务表（不阻塞向量写入）
        if self._kb_writer is not None and ids:
            try:
                self._kb_writer(nodes)
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "[kb] kb_chunk 双写失败（向量已写入 collection=%s）", self._collection_name
                )
        return ids

    def delete(self, ref_doc_id: str | None = None, delete_all: bool = False, **kwargs: Any) -> None:
        if delete_all:
            self._store.clear_all()
            return
        if ref_doc_id:
            # 简单实现：按 ref_doc_id 前缀/元数据匹配删除
            # 我们的主键是 chunk_id，没有 ref_doc_id 维度索引，这里兜底 clear_all
            # 如果需要精确删除，建议直接删 collection 重建
            self._store.clear_all()

    def query(self, query: VectorStoreQuery, **kwargs: Any) -> VectorStoreQueryResult:
        qvec = getattr(query, "query_embedding", None) or kwargs.get("query_embedding") or []
        k = int(getattr(query, "similarity_top_k", 5) or 5)
        hits = self._store.search(list(qvec), k=k)
        ids = [h[0] for h in hits]
        scores = [1.0 / (1.0 + h[1]) for h in hits]  # 距离 → 相似度归一化
        texts = [h[2] for h in hits]
        metas = [h[3] for h in hits]
        nodes = []
        for rid, txt, m in zip(ids, texts, metas):
            try:
                nodes.append(TextNode(id_=rid, text=txt, metadata=m or {}))
            except Exception:
                pass
        return VectorStoreQueryResult(nodes=nodes, similarities=scores, ids=ids)


@dataclass
class KBVectorStoreFactory:
    """可插拔向量库工厂：支持 sqlite / qdrant / chroma 三种后端.

    用法::

        factory = KBVectorStoreFactory.get_default(project_root=Path(...))
        vs = factory.get_vector_store("world_of_warcraft")
        factory.delete_collection("world_of_warcraft")
    """

    backend: str = "qdrant"  # "sqlite" | "qdrant" | "chroma"  （Studio 默认用 Qdrant Docker，不再用 SQLite 向量库）
    project_root: Path = field(default_factory=lambda: Path.cwd())
    chroma_path: Path | None = None
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str | None = None
    # 单例缓存
    _default_singleton: KBVectorStoreFactory | None = None
    _per_topic: dict[tuple[str, str], Any] = field(default_factory=dict)
    _per_topic_sqlite: dict[str, SQLiteVectorStore] = field(default_factory=dict)

    @classmethod
    def get_default(
        cls,
        *,
        project_root: Path | None = None,
        vector_store_backend: str | None = None,
        chroma_path: Path | None = None,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
    ) -> KBVectorStoreFactory:
        if cls._default_singleton is not None and vector_store_backend is None:
            return cls._default_singleton
        backend = (
            (vector_store_backend or "").strip().lower()
            or os.environ.get("KB_VECTOR_STORE", "").strip().lower()
            or "qdrant"
        )
        if backend not in {"sqlite", "qdrant", "chroma"}:
            # 非法值用默认 qdrant，不再默默降级 sqlite
            backend = "qdrant"
        root = Path(project_root or Path.cwd()).resolve()
        inst = cls(
            backend=backend,
            project_root=root,
            chroma_path=Path(chroma_path or root / "data" / "chroma"),
            qdrant_url=(qdrant_url or os.environ.get("QDRANT_URL") or "http://127.0.0.1:6333").rstrip("/"),
            qdrant_api_key=qdrant_api_key or os.environ.get("QDRANT_API_KEY") or None,
        )
        cls._default_singleton = inst
        return inst

    # ------------------------------------------------------------------
    # get_vector_store：按 topic_id 返回 LlamaIndex VectorStore
    # ------------------------------------------------------------------
    def get_vector_store(self, topic_id: str):
        collection = f"kb_{topic_id}"
        cache_key = (self.backend, collection)
        if cache_key in self._per_topic:
            return self._per_topic[cache_key]

        if self.backend == "sqlite":
            db_path = self.project_root / "data" / "kb_vectors" / f"{collection}.db"
            store = SQLiteVectorStore(db_path)
            self._per_topic_sqlite[collection] = store
            vs = _SQLiteLlamaStoreAdapter(store, collection)
            self._per_topic[cache_key] = vs
            return vs

        if self.backend == "qdrant":
            try:
                from llama_index.vector_stores.qdrant import QdrantVectorStore
                from qdrant_client import AsyncQdrantClient, QdrantClient

                client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key)
                aclient = AsyncQdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key)
                vs = QdrantVectorStore(client=client, aclient=aclient, collection_name=collection)
                self._per_topic[cache_key] = vs
                return vs
            except Exception as e:
                raise RuntimeError(
                    f"Qdrant（{self.qdrant_url}）连接失败，kb collection={collection} 无法创建。"
                    f"请先启动容器：scripts/start_qdrant.py start 。错误：{type(e).__name__}: {e}"
                ) from e

        if self.backend == "chroma":
            try:
                import chromadb
                from llama_index.vector_stores.chroma import ChromaVectorStore

                self.chroma_path = Path(self.chroma_path or (self.project_root / "data" / "chroma"))
                self.chroma_path.parent.mkdir(parents=True, exist_ok=True)
                settings = chromadb.get_settings()
                chroma_client = chromadb.PersistentClient(path=str(self.chroma_path), settings=settings)
                chroma_collection = chroma_client.get_or_create_collection(name=collection)
                vs = ChromaVectorStore(chroma_collection=chroma_collection)
                self._per_topic[cache_key] = vs
                return vs
            except Exception as e:
                raise RuntimeError(
                    f"Chroma 连接失败，kb collection={collection} 无法创建。错误：{type(e).__name__}: {e}"
                ) from e

        return self._fallback_to_sqlite(collection)

    def _fallback_to_sqlite(self, collection: str):
        db_path = self.project_root / "data" / "kb_vectors" / f"{collection}.db"
        store = SQLiteVectorStore(db_path)
        self._per_topic_sqlite[collection] = store
        vs = _SQLiteLlamaStoreAdapter(store, collection)
        self._per_topic[("sqlite", collection)] = vs
        return vs

    # ------------------------------------------------------------------
    # delete_collection：删除一个 topic 的向量集合（幂等）
    # ------------------------------------------------------------------
    def delete_collection(self, topic_id: str) -> bool:
        collection = f"kb_{topic_id}"
        cache_key = (self.backend, collection)
        self._per_topic.pop(cache_key, None)

        try:
            if self.backend == "sqlite":
                store = self._per_topic_sqlite.pop(collection, None)
                if store is None:
                    db_path = self.project_root / "data" / "kb_vectors" / f"{collection}.db"
                    store = SQLiteVectorStore(db_path)
                store.clear_all()
                # 删整个 db 文件，真·清空
                try:
                    db_path = self.project_root / "data" / "kb_vectors" / f"{collection}.db"
                    for suffix in ("", "-wal", "-shm", "-journal"):
                        p = Path(str(db_path) + suffix)
                        if p.exists():
                            p.unlink()
                except Exception:
                    pass
                return True

            if self.backend == "qdrant":
                try:
                    from qdrant_client import QdrantClient

                    client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key)
                    client.delete_collection(collection_name=collection)
                    return True
                except Exception:
                    return False

            if self.backend == "chroma":
                try:
                    import chromadb

                    self.chroma_path = Path(self.chroma_path or (self.project_root / "data" / "chroma"))
                    settings = chromadb.get_settings()
                    chroma_client = chromadb.PersistentClient(path=str(self.chroma_path), settings=settings)
                    try:
                        chroma_client.delete_collection(name=collection)
                        return True
                    except Exception:
                        return False
                except Exception:
                    return False
        except Exception:
            return False
        return False

    def close_all(self) -> None:
        for s in list(self._per_topic_sqlite.values()):
            try:
                s.close()
            except Exception:
                pass
        self._per_topic_sqlite.clear()
        self._per_topic.clear()
