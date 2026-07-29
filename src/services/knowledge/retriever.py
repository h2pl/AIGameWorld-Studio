"""知识检索服务（向量检索 + 关键词补全）.

结合 :class:`KnowledgeManager` + :class:`VectorStore` 做：
- 向量相似度检索（top_k chunk）
- 同文档相邻 chunk 合并 / 去重 / 截断
- 返回给上层 LLM 做问答上下文
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# 模块级 logger
_log = logging.getLogger(__name__)


# 单条检索命中
@dataclass
class RetrievedChunk:
    # 所属文档 ID
    doc_id: str
    # 文档标题
    title: str
    # 作者（可为空）
    author: str
    # 文档来源 URL（可为空）
    source_url: str
    # 相对 topic_dir 的文件路径
    rel_path: str
    # 子目录类型（documents/notes/...）
    source_type: str
    # 块在文档中的起始偏移（字符）
    start_offset: int
    # 块字符长度
    length: int
    # 块索引
    chunk_index: int
    # 相似度分数（越大越相似；具体含义由向量存储决定，可能是 cosine，也可能是 L2）
    score: float
    # 块文本
    text: str


# 检索结果容器（含去重合并后 chunks，方便上层直接 dump 给 prompt）
@dataclass
class RetrieveResult:
    # 命中 chunk 列表（按 score 从高到低排序）
    chunks: list[RetrievedChunk] = field(default_factory=list)
    # 去重合并后的总字符数（帮助判断 prompt 过长要不要再截断）
    total_chars: int = 0
    # 本次用到的检索模式："hybrid" / "vector" / "keyword" / "none"
    mode: str = "none"
    # 原始命中的文档 ID 集合（用于展示参考文档列表）
    @property
    def doc_ids(self) -> list[str]:
        # 去重的文档 ID 列表
        seen: set[str] = set()
        # 结果列表
        out: list[str] = []
        # 遍历 chunks
        for c in self.chunks:
            # 已见过就跳过
            if c.doc_id in seen:
                continue
            # 加入 seen
            seen.add(c.doc_id)
            # 加入结果
            out.append(c.doc_id)
        # 返回
        return out


# 主检索器
class KnowledgeRetriever:
    # 构造：绑定 topic_dir 与向量库工厂（由调用方 manager 传）
    # 参数 topic_dir：topic 根目录（topic_id 已解析出来）
    # 参数 vector_store_factory：lambda(path, dims) -> VectorStore，允许为 None（纯关键词）
    # 参数 embed_fn：文本→向量 回调，必须和建库时一致
    # 参数 manager：KnowledgeManager 实例（列文档/读文件用），可为 None
    # 参数 vector_subdir：向量索引子目录名（应与 manager._VECTOR_DIRNAME 一致）
    def __init__(
        self,
        topic_dir: str | Path,
        *,
        vector_store_factory: Any = None,
        embed_fn: Any = None,
        manager: Any = None,
        vector_subdir: str = "_vector_index",
    ):
        # 转 Path
        self._topic_dir = Path(topic_dir)
        # 向量库工厂
        self._vec_factory = vector_store_factory
        # Embed 回调
        self._embed_fn = embed_fn
        # KnowledgeManager（可选）
        self._manager = manager
        # 向量索引子目录名
        self._vec_subdir = vector_subdir
        # 向量索引目录 Path
        self._vec_dir = self._topic_dir / self._vec_subdir
        # 向量库 DB 路径（vectors.sqlite）
        self._vec_db = self._vec_dir / "vectors.sqlite"

    # 打开向量库（若存在 + 有 factory；否则返回 None）
    # 参数 dims_hint：可选的维度，传递给 factory；None 表示让 factory 自行推断
    # 返回 Optional[VectorStore]：打开的向量库或 None
    def _open_vec_store(self, dims_hint: int | None = None):
        # factory 不存在直接 None
        if self._vec_factory is None:
            return None
        # 向量库 DB 文件不存在（还没建过索引）
        if not self._vec_db.exists():
            return None
        # 尝试打开，异常记日志
        try:
            # 调 factory（dims_hint 可能为 None）
            return self._vec_factory(self._vec_db, dims_hint or 0)
        except Exception:
            # 打开失败记日志（可能是版本不兼容 / 文件损坏）
            _log.exception("open vector store failed: %s", self._vec_db)
            return None

    # 向量检索入口（top_k 个最相似 chunk）
    # 参数 query：查询文本
    # 参数 top_k：返回块数，默认 6
    # 参数 score_threshold：可选；低于该分数的 chunk 会被过滤（具体是否生效取决于实现）
    # 返回 list[RetrievedChunk]：命中块，可能为空列表
    def vector_search(
        self,
        query: str,
        *,
        top_k: int = 6,
        score_threshold: float | None = None,
    ) -> list[RetrievedChunk]:
        # 查询空字符串 → 空结果
        if not query or not query.strip():
            return []
        # 没 embed_fn → 无法向量化，返回空
        if self._embed_fn is None:
            return []
        # 先对查询向量化
        try:
            # 调用 embed_fn（允许 str 或 [str]）
            v = self._embed_fn(query)
        except Exception:
            # 向量化异常 → 空结果
            _log.exception("embed query failed")
            return []
        # 保证是 list[float]
        try:
            # 转 list
            q_vec = list(v)
        except Exception:
            # 转换失败
            return []
        # 空向量 → 空结果
        if not q_vec:
            return []
        # 维度数
        dims = len(q_vec)
        # 打开向量库（按 q_vec 维度提示）
        store = self._open_vec_store(dims_hint=dims)
        # 打开失败 → 空结果
        if store is None:
            return []
        # 结果 chunks
        hits: list[RetrievedChunk] = []
        # 查询 + 结果解析
        try:
            # 调向量库 search：返回 list[(chunk_id, score, text, meta)]
            raw = store.search(q_vec, k=max(1, int(top_k)))
            # 遍历 raw hits
            for row in raw:
                # 支持 list / tuple 结构：(chunk_id, score, text, meta)
                # 兼容各种实现，按位置取
                try:
                    # 第一个：chunk_id
                    cid = row[0] if len(row) > 0 else ""
                    # 第二个：score
                    score = float(row[1]) if len(row) > 1 else 0.0
                    # 第三个：text
                    text = str(row[2]) if len(row) > 2 else ""
                    # 第四个：meta dict
                    meta = row[3] if len(row) > 3 else {}
                except Exception:
                    # row 结构不认识：跳过
                    continue
                # meta 不是 dict 则当空
                if not isinstance(meta, dict):
                    # 退化为空 dict
                    meta = {}
                # score 阈值过滤（调用方传了 threshold 才生效）
                if score_threshold is not None and score < score_threshold:
                    continue
                # 组装 RetrievedChunk
                rc = RetrievedChunk(
                    # 文档 ID
                    doc_id=str(meta.get("doc_id", "")),
                    # 标题
                    title=str(meta.get("title", "")),
                    # 作者
                    author=str(meta.get("author", "")),
                    # 来源 URL
                    source_url=str(meta.get("source_url", "")),
                    # 相对路径
                    rel_path=str(meta.get("rel_path", "")),
                    # 子目录类型
                    source_type=str(meta.get("source_type", "")),
                    # 块起始偏移
                    start_offset=int(meta.get("start_offset", 0) or 0),
                    # 块长度
                    length=int(meta.get("length", 0) or 0),
                    # 块索引
                    chunk_index=int(meta.get("chunk_index", 0) or 0),
                    # 相似度分数
                    score=score,
                    # 块文本
                    text=text,
                )
                # 加入结果
                hits.append(rc)
        except Exception:
            # 检索异常（库损坏等）→ 记日志
            _log.exception("vector search failed")
            # 当空处理
            hits = []
        finally:
            # 无论成功失败，记得关 store
            try:
                # 调 close
                store.close()
            except Exception:
                # 关失败忽略
                pass
        # 返回命中块列表
        return hits

    # 关键词检索（非常基础的实现：遍历文档做 substring；没向量库时的保底方案）
    # 参数 query：空格分隔关键词或整句
    # 参数 top_k：返回块数上限
    # 参数 case_sensitive：是否区分大小写，默认 False
    # 返回 list[RetrievedChunk]：命中块（伪 score = 关键词命中次数）
    def keyword_search(
        self,
        query: str,
        *,
        top_k: int = 6,
        case_sensitive: bool = False,
    ) -> list[RetrievedChunk]:
        # 查询空 → 空结果
        if not query or not query.strip():
            return []
        # topic_dir 不存在 → 空
        if not self._topic_dir.is_dir():
            return []
        # 关键词拆分：空格 / 中文逗号 / 英文逗号
        import re
        # 按空白或中英文逗号拆分
        kws = [k for k in re.split(r"[\s,，]+", query) if k]
        # 拆分不出关键词 → 整句当 1 个关键词
        if not kws:
            # 整句
            kws = [query]
        # 小写化（若不区分大小写）
        if not case_sensitive:
            # 全转小写
            kws_norm = [k.lower() for k in kws]
        else:
            # 原样
            kws_norm = list(kws)
        # 文档路径列表：优先用 manager 拿（按 metadata 登记的顺序），否则 glob
        doc_paths: list[Path] = []
        # manager 存在
        if self._manager is not None:
            # 从 manager 列路径（幂等，读 JSONL + 校验文件存在）
            doc_paths = self._manager.document_paths(self._topic_dir.name)
        # manager 没给（可能传了 None 或 metadata 坏了）→ 兜底 glob documents/*.md + notes/*.md
        if not doc_paths:
            # 先找 documents/*.md
            for sub in ("documents", "notes"):
                # 子目录 Path
                sub_dir = self._topic_dir / sub
                # 子目录存在
                if sub_dir.is_dir():
                    # 所有 md 文件（排下序更稳定）
                    doc_paths.extend(sorted(sub_dir.glob("*.md")))
        # 没文档 → 空结果
        if not doc_paths:
            return []
        # 命中候选（没做切块：把文档按行粗切，模拟块；比 keyword 扫全文更可控）
        candidates: list[RetrievedChunk] = []
        # 遍历每个文档
        for p in doc_paths:
            # 文件不存在跳过（理论不会，document_paths 已过滤）
            if not p.is_file():
                continue
            # 读正文
            try:
                # 以 UTF-8 读
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                # 读失败跳过
                continue
            # 相对路径
            try:
                # 算相对于 topic_dir 的路径
                rel = p.relative_to(self._topic_dir).as_posix()
            except Exception:
                # 算不出就用文件名
                rel = p.name
            # source_type：按父目录名推断
            source_type = p.parent.name
            # 标题：第一行非空 + 去掉 # 前缀
            title = ""
            # 遍历找第一行
            for ln in text.splitlines():
                # 去空白
                s = ln.strip()
                # 非空则 break
                if s:
                    # 去掉 markdown # 前缀
                    title = re.sub(r"^#+\s*", "", s).strip()
                    # 拿前 200 字符
                    title = title[:200]
                    break
            # 真没标题 → 用文件名
            if not title:
                # 文件名 stem
                title = p.stem
            # 文档 ID（稳定：基于 rel_path hash）
            doc_id = f"doc-{abs(hash(rel)):x}"
            # 粗切块：简单按块大小 ~1500 字符（和 manager._chunk_text 相同窗口，便于对齐）
            win = 1500
            # 块重叠
            lap = 150
            # 游标
            start = 0
            # 全文长度
            n = len(text)
            # 块索引
            idx = 0
            # 游标没到末尾
            while start < n:
                # 结束位置
                end = min(start + win, n)
                # 切片
                piece = text[start:end]
                # 最后一块直接；否则找标点切（这里简化：硬切即可）
                # 做关键词匹配计数（在 piece 范围内）
                # piece 的规范化文本
                p_norm = piece if case_sensitive else piece.lower()
                # 命中次数
                hit_count = 0
                # 每个关键词都算
                for kw in kws_norm:
                    # 关键词空（理论不会）跳过
                    if not kw:
                        continue
                    # 数出现次数
                    hit_count += p_norm.count(kw)
                # 命中 > 0 才加入候选
                if hit_count > 0:
                    # score = 命中次数（简单加权：关键词 * 次数）
                    score = float(hit_count)
                    # 组装候选
                    rc = RetrievedChunk(
                        # 文档 ID
                        doc_id=doc_id,
                        # 标题
                        title=title,
                        # 作者未知
                        author="",
                        # 来源 URL 未知
                        source_url="",
                        # 相对路径
                        rel_path=rel,
                        # 子目录类型
                        source_type=source_type,
                        # 块起始偏移
                        start_offset=start,
                        # 块长度
                        length=len(piece),
                        # 块索引
                        chunk_index=idx,
                        # 伪相似度分
                        score=score,
                        # 块文本
                        text=piece,
                    )
                    # 加入候选
                    candidates.append(rc)
                # 下一块（避免死循环，前进 1 字符保底）
                advance = max(1, (end - start) - lap)
                # 游标前进
                start += advance
                # 块索引+1
                idx += 1
        # 排序：按 score 降序
        candidates.sort(key=lambda c: c.score, reverse=True)
        # top_k 截断
        return candidates[: max(0, int(top_k))]

    # 主入口：混合检索（向量优先，keyword 做补充；合并 + 去重）
    # 参数 query：检索问题
    # 参数 top_k：最终块数上限，默认 8
    # 参数 vector_weight：向量分在混合排序中的权重（keyword 权重 = 1 - vector_weight）
    # 返回 RetrieveResult：封装好的结果（含 chunks/total_chars/mode）
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 8,
        vector_weight: float = 0.6,
    ) -> RetrieveResult:
        # 查询空 → 空结果
        if not query or not query.strip():
            # 返回空
            return RetrieveResult(chunks=[], total_chars=0, mode="none")

        # --- 并行跑两种检索（串行先做，逻辑简单） ---
        # 向量命中
        vec_hits: list[RetrievedChunk] = []
        # 向量库可用才跑
        if self._vec_factory is not None and self._embed_fn is not None and self._vec_db.exists():
            # 调用向量检索（略多取一些，后面合并时再截断）
            vec_hits = self.vector_search(query, top_k=max(top_k, 12))

        # 关键词命中
        kw_hits: list[RetrievedChunk] = []
        # 关键词兜底方案：向量为空或命中数 < top_k/2 时补关键词
        if not vec_hits or len(vec_hits) < max(1, top_k // 2):
            # 跑关键词检索（同样略多取）
            kw_hits = self.keyword_search(query, top_k=max(top_k, 10))

        # --- 归一化分数 + 合并 ---
        # 合并桶：key = (doc_id, chunk_index) → 去重合并
        merged: dict[tuple[str, int], RetrievedChunk] = {}

        # 先处理 vec_hits（权重 vector_weight）
        # 先归一化到 0~1（简单：min-max；若所有 score 相同则都给 0.5）
        if vec_hits:
            # 所有分数
            scores = [h.score for h in vec_hits]
            # 最小最大
            smin, smax = min(scores), max(scores)
            # 范围
            span = smax - smin
            # 遍历
            for h in vec_hits:
                # 归一化分数
                norm = 0.5 if span == 0 else (h.score - smin) / span
                # 加权
                mixed = norm * float(vector_weight)
                # key
                key = (h.doc_id, h.chunk_index)
                # 已在（关键词先加进来了？）→ 取分数高者并加分
                if key in merged:
                    # 已存在的 chunk
                    existing = merged[key]
                    # 混合分相加
                    existing.score = existing.score + mixed
                    # 文本以长的为准（保留更完整上下文）
                    if len(h.text) > len(existing.text):
                        # 覆盖文本
                        existing.text = h.text
                        # 同步 start_offset
                        existing.start_offset = h.start_offset
                        # 同步长度
                        existing.length = h.length
                else:
                    # 新建（拷贝一份避免改原对象）
                    import copy
                    # 深拷贝
                    new_rc = copy.copy(h)
                    # 分数置为混合加权
                    new_rc.score = mixed
                    # 进桶
                    merged[key] = new_rc

        # 再处理 kw_hits（权重 1-vector_weight）
        if kw_hits:
            # 所有分数
            scores = [h.score for h in kw_hits]
            # 最小最大
            smin, smax = min(scores), max(scores)
            # 范围
            span = smax - smin
            # 关键词权重
            kw_weight = 1.0 - float(vector_weight)
            # 遍历
            for h in kw_hits:
                # 归一化
                norm = 0.5 if span == 0 else (h.score - smin) / span
                # 加权
                mixed = norm * kw_weight
                # key
                key = (h.doc_id, h.chunk_index)
                # 已存在：分数叠加
                if key in merged:
                    # 加上关键词分
                    merged[key].score += mixed
                else:
                    # 新建
                    import copy
                    # 拷贝
                    new_rc = copy.copy(h)
                    # 分数 = 关键词加权
                    new_rc.score = mixed
                    # 进桶
                    merged[key] = new_rc

        # --- 按混合分降序 + top_k 截断 ---
        # 从 dict 里拿出列表
        final_list: list[RetrievedChunk] = list(merged.values())
        # 排序：分数高→低
        final_list.sort(key=lambda c: c.score, reverse=True)
        # top_k 截断
        final_list = final_list[: max(0, int(top_k))]
        # 总字符数
        total_chars = sum(len(c.text) for c in final_list)
        # 判定 mode
        if vec_hits and kw_hits:
            # 混合检索
            mode = "hybrid"
        elif vec_hits:
            # 纯向量
            mode = "vector"
        elif kw_hits:
            # 纯关键词
            mode = "keyword"
        else:
            # 都没命中
            mode = "none"
        # 返回封装结果
        return RetrieveResult(chunks=final_list, total_chars=total_chars, mode=mode)
