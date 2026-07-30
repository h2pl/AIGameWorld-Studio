"""知识管理服务（Topic → Promote → Chunk → Embed → Store）.

对外暴露 :class:`KnowledgeManager`：

- ``ensure_topic(topic_id, ...)`` 创建/读取 topic 根目录
- ``promote_documents(topic_id, staging_dir, source_type)`` 把 crawler 暂存的文件合并进
  topic 文档子目录 + 在 metadata.sqlite 登记
- ``rebuild_vector_index(topic_id)`` 重新切块 → Embed → 存向量（失败不抛，由上层决定提示）
- ``document_paths(topic_id, source_type=None)`` 列出已入库文档的 Path 列表
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 模块级 logger
_log = logging.getLogger(__name__)

# 文档目录内 metadata JSONL 文件名（每条一行，顺序即展示顺序）
_DOC_META_JSONL = "documents.jsonl"
# 切块/向量索引目录（每个 topic 一个子目录存 sqlite 向量库）
_VECTOR_DIRNAME = "_vector_index"
# 每块默认最大字符数（中文按字符算，够用；EMBED 模型通常 512~8192 token）
_DEFAULT_CHUNK_CHARS = 1500
# 块之间重叠字符数（避免边界信息丢失）
_DEFAULT_CHUNK_OVERLAP = 150


# Document 记录（Topic 子目录里的单篇文档）
@dataclass
class TopicDocument:
    # 文档 ID（主键，默认基于路径 hash）
    id: str
    # 主题 ID
    topic_id: str
    # 文档子目录类型（documents/notes/...）
    source_type: str
    # 相对 topic_dir 的文件路径字符串
    rel_path: str
    # 展示标题
    title: str
    # 作者（可为空）
    author: str
    # 来源 URL（可为空）
    source_url: str
    # 正文大小（字符数，空为 0）
    size_chars: int
    # 入库时间戳（毫秒）
    promoted_at: int


# 主类
class KnowledgeManager:
    # 构造函数
    # 参数 projects_root：所有 topic 的父目录（默认 ./data/topics）
    # 参数 embed_fn：可选的文本→向量 回调（None 表示向量索引不启用）
    # 参数 vector_store_factory：可选的向量存储工厂（lambda path, dims -> VectorStore）
    # 参数 chunk_chars：切块大小，默认 1500 字符
    # 参数 chunk_overlap：块重叠字符数，默认 150
    def __init__(
        self,
        projects_root: str | Path,
        *,
        embed_fn: Any = None,
        vector_store_factory: Any = None,
        chunk_chars: int = _DEFAULT_CHUNK_CHARS,
        chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
    ):
        # 转 Path
        self._root = Path(projects_root)
        # 根目录不存在就创建（连同父目录）
        self._root.mkdir(parents=True, exist_ok=True)
        # Embed 回调函数（文本→向量列表）
        self._embed_fn = embed_fn
        # VectorStore 工厂函数
        self._vec_factory = vector_store_factory
        # 切块大小
        self._chunk_chars = max(100, int(chunk_chars or _DEFAULT_CHUNK_CHARS))
        # 块重叠
        self._chunk_overlap = max(0, int(chunk_overlap or _DEFAULT_CHUNK_OVERLAP))
        # topic_id 级别的重入锁（避免并行 promote/rebuild 互相覆盖）
        self._locks: dict[str, threading.RLock] = {}
        # _locks 字典自身的读写锁
        self._locks_guard = threading.Lock()

    # 取/建 topic 级别的锁
    # 参数 topic_id：主题 ID
    # 返回 threading.RLock：该 topic 的可重入锁
    def _lock_for(self, topic_id: str) -> threading.RLock:
        # 先加字典锁读
        with self._locks_guard:
            # 已存在就直接返回
            if topic_id in self._locks:
                return self._locks[topic_id]
            # 不存在就新建 RLock
            lock = threading.RLock()
            # 存字典
            self._locks[topic_id] = lock
            # 返回锁
            return lock

    # 确保 topic 目录存在（不存在就创建）
    # 参数 topic_id：主题 ID
    # 参数 topic_title：可选的主题标题（首次创建时写入 topic.json）
    # 返回 Path：topic 根目录
    def ensure_topic(self, topic_id: str, *, topic_title: str = "") -> Path:
        # topic 根目录
        d = self._root / topic_id
        # 不存在则创建
        d.mkdir(parents=True, exist_ok=True)
        # 子目录：文档默认目录
        (d / "documents").mkdir(exist_ok=True)
        # 子目录：笔记默认目录
        (d / "notes").mkdir(exist_ok=True)
        # 子目录：向量索引目录
        (d / _VECTOR_DIRNAME).mkdir(exist_ok=True)
        # topic 元信息文件（topic.json）
        meta_file = d / "topic.json"
        # 有传标题 + 没 topic.json → 写一个
        if topic_title and not meta_file.exists():
            # 初始化 topic 元信息字典
            info: dict[str, Any] = {
                # 主题 ID
                "id": topic_id,
                # 主题标题
                "title": topic_title,
                # 创建时间戳
                "created_at": int(__import__("time").time() * 1000),
            }
            try:
                # 以 UTF-8 写 JSON
                meta_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                # 写失败只记日志
                _log.exception("write topic.json failed: %s", meta_file)
        # 返回 topic 根目录
        return d

    # 返回 topic 根目录（不自动创建）
    # 参数 topic_id：主题 ID
    # 返回 Path：可能不存在的 topic 根路径
    def topic_dir(self, topic_id: str) -> Path:
        # 直接拼
        return self._root / topic_id

    # 列出 Topic 下已登记的文档
    # 参数 topic_id：主题 ID
    # 参数 source_type：可选；只返回该子目录类型（documents/notes...）
    # 返回 list[TopicDocument]：已登记文档列表
    def list_documents(self, topic_id: str, *, source_type: str | None = None) -> list[TopicDocument]:
        # topic 根目录
        d = self.topic_dir(topic_id)
        # metadata jsonl 路径
        meta = d / _DOC_META_JSONL
        # 没文件 → 空列表
        if not meta.exists():
            return []
        # 结果列表
        out: list[TopicDocument] = []
        # 按行读 JSONL
        try:
            # 以 UTF-8 打开
            with meta.open("r", encoding="utf-8") as f:
                # 遍历每一行
                for line in f:
                    # 行去空白
                    line = line.strip()
                    # 空行跳过
                    if not line:
                        continue
                    # 解析 JSON，处理异常
                    try:
                        # 解析
                        obj = json.loads(line)
                    except Exception:
                        # 解析失败跳过
                        continue
                    # source_type 过滤
                    if source_type and obj.get("source_type") != source_type:
                        # 不是指定类型跳过
                        continue
                    # 组装 TopicDocument
                    doc = TopicDocument(
                        # ID
                        id=str(obj.get("id", "")),
                        # 主题 ID
                        topic_id=topic_id,
                        # 类型
                        source_type=str(obj.get("source_type", "")),
                        # 相对路径
                        rel_path=str(obj.get("rel_path", "")),
                        # 标题
                        title=str(obj.get("title", "")),
                        # 作者
                        author=str(obj.get("author", "")),
                        # 来源 URL
                        source_url=str(obj.get("source_url", "")),
                        # 字符数
                        size_chars=int(obj.get("size_chars", 0) or 0),
                        # 入库时间
                        promoted_at=int(obj.get("promoted_at", 0) or 0),
                    )
                    # 过滤掉 id 空的（坏行）
                    if doc.id:
                        # 加入结果
                        out.append(doc)
        except Exception:
            # 读取失败（权限、磁盘坏块）→ 记日志，返回已读部分
            _log.exception("list_documents failed: topic=%s", topic_id)
        # 返回列表
        return out

    # 返回文档绝对路径列表（给 retriever / 前端下载用）
    # 参数 topic_id：主题 ID
    # 参数 source_type：可选过滤子目录类型
    # 返回 list[Path]：文档绝对路径（只含存在的）
    def document_paths(self, topic_id: str, *, source_type: str | None = None) -> list[Path]:
        # 先列 TopicDocument
        docs = self.list_documents(topic_id, source_type=source_type)
        # topic 根目录
        d = self.topic_dir(topic_id)
        # 结果
        out: list[Path] = []
        # 遍历文档
        for doc in docs:
            # 相对路径转绝对
            p = d / doc.rel_path
            # 文件确实存在才加（防止文件被手动删了但 metadata 还留着）
            if p.is_file():
                # 加入
                out.append(p)
        # 返回
        return out

    # 把 crawler 暂存的结果搬到 topic 目录并登记（幂等：基于 item_id 去重）
    # 参数 topic_id：主题 ID
    # 参数 staging_dir：crawler 暂存目录（应含 _metadata.jsonl + *.md）
    # 参数 source_type：目标子目录名（documents / notes / ...）
    # 返回 list[TopicDocument]：本次新 promote 的文档列表
    def promote_documents(
        self,
        topic_id: str,
        staging_dir: str | Path,
        *,
        source_type: str = "documents",
    ) -> list[TopicDocument]:
        # staging_dir 转 Path
        staging = Path(staging_dir)
        # staging 不存在直接返回空
        if not staging.exists():
            return []
        # 先确保 topic 目录存在
        self.ensure_topic(topic_id)
        # 加 topic 级别锁（避免两个线程同时 promote 相互覆盖 metadata）
        with self._lock_for(topic_id):
            # topic 根
            d = self.topic_dir(topic_id)
            # 目标子目录
            dest_dir = d / source_type
            # 不存在就创建
            dest_dir.mkdir(parents=True, exist_ok=True)
            # metadata 文件路径
            meta_file = d / _DOC_META_JSONL
            # 先读已有登记（用于去重：已存在的 item_id 不再重复加）
            existing_ids: set[str] = set()
            # 先读已有 metadata
            if meta_file.exists():
                # 按行解析
                try:
                    # 读文件
                    for line in meta_file.read_text(encoding="utf-8").splitlines():
                        # 去空白
                        line = line.strip()
                        # 空行跳过
                        if not line:
                            continue
                        # 解析 JSON
                        try:
                            # 解析
                            obj = json.loads(line)
                        except Exception:
                            # 解析失败跳过
                            continue
                        # id 字段
                        doc_id = obj.get("id")
                        # 非空加入去重集合
                        if doc_id:
                            # 加入 existing_ids
                            existing_ids.add(str(doc_id))
                except Exception:
                    # 读 metadata 失败：保守处理，不去重了，最多重复 append
                    _log.exception("promote: read existing meta failed, will append blindly")

            # staging 下的 metadata jsonl
            stage_meta = staging / "_metadata.jsonl"
            # 没 metadata（旧版 crawler 或同步回退）：只靠 *.md 也能 promote
            staged_rows: list[dict[str, Any]] = []
            # metadata 文件存在则解析
            if stage_meta.exists():
                # 遍历 JSONL 行
                try:
                    # 读行
                    for line in stage_meta.read_text(encoding="utf-8").splitlines():
                        # 去空白
                        line = line.strip()
                        # 空行跳过
                        if not line:
                            continue
                        # 解析 JSON
                        try:
                            # 解析
                            obj = json.loads(line)
                        except Exception:
                            # 解析失败跳过
                            continue
                        # 追加行
                        staged_rows.append(obj)
                except Exception:
                    # 解析失败记日志，后面用 glob 兜底
                    _log.exception("promote: parse staging metadata failed, fallback glob")

            # staged_rows 为空（metadata 空或解析全失败）→ 用 glob 扫 .md 文件兜底
            if not staged_rows:
                # 遍历 staging 下所有 md 文件
                for md in sorted(staging.glob("*.md")):
                    # 按文件名（不含后缀）当 item_id
                    item_id = md.stem
                    # 构造最小 metadata 行
                    staged_rows.append(
                        {
                            # item_id
                            "item_id": item_id,
                            # URL 空
                            "url": "",
                            # 标题用文件名
                            "title": item_id,
                            # 作者空
                            "author": "",
                            # 域名空
                            "domain": "",
                            # 内容文件
                            "content_file": md.name,
                        }
                    )

            # 本次 promote 成功的文档列表
            promoted: list[TopicDocument] = []
            # 当前时间戳（ms）
            import time

            # 取当前毫秒时间
            now_ms = int(time.time() * 1000)

            # metadata 追加写模式（所有新 doc 一次追加完再关）
            try:
                # 以 UTF-8 追加打开
                meta_fh = meta_file.open("a", encoding="utf-8")
            except Exception:
                # 打不开 metadata：promote 没法登记，记 warning
                _log.exception("promote: cannot open meta for append, will skip registry")
                # 用 None 当哨兵
                meta_fh = None

            # 遍历 staging 每条
            for row in staged_rows:
                # item_id（主键）
                item_id = str(row.get("item_id", "")).strip()
                # 没 item_id 跳过
                if not item_id:
                    continue
                # 已 promote 过（去重）
                if item_id in existing_ids:
                    # 跳过
                    continue
                # 正文内容文件名（默认 {item_id}.md）
                content_file = str(row.get("content_file") or f"{item_id}.md")
                # staging 下的源文件
                src_md = staging / content_file
                # 源文件不存在（爬失败了可能只有 metadata 没 md）→ 跳过
                if not src_md.is_file():
                    continue

                # 目标路径：{source_type}/{item_id}.md（若 content_file 有后缀保留）
                # 保留 content_file 的文件名（通常就是 item_id.md，但兼容带后缀 hash 等）
                target_rel = f"{source_type}/{Path(content_file).name}"
                # 绝对路径
                target_abs = d / target_rel
                # 复制文件（覆盖写：幂等）
                try:
                    # shutil 拷贝（保留文件元信息：创建时间等）
                    shutil.copy2(src_md, target_abs)
                except Exception:
                    # 拷贝失败（权限、磁盘满）→ 记日志跳过
                    _log.exception("promote: copy failed: %s -> %s", src_md, target_abs)
                    # 继续下一个
                    continue

                # 读一次正文，算字符数 + 给标题兜底
                try:
                    # 以 UTF-8 读
                    text = target_abs.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    # 读失败：空字符串
                    text = ""
                # 字符数
                size_chars = len(text)

                # 标题：优先 metadata title，再取正文第一行非空
                title = str(row.get("title") or "").strip()
                # 没标题
                if not title:
                    # 从正文第一行拿
                    for ln in text.splitlines():
                        # 去空白
                        ln = ln.strip()
                        # 非空就 break
                        if ln:
                            # 去掉 markdown 前缀 "# "
                            title = re.sub(r"^#+\s*", "", ln).strip()
                            # 取前 200 字符
                            title = title[:200]
                            # 找到就停止
                            break
                    # 最终还是空 → 用文件名
                    if not title:
                        # 用文件名（无后缀）
                        title = Path(content_file).stem
                # 作者
                author = str(row.get("author") or "").strip()
                # 来源 URL
                source_url = str(row.get("url") or "").strip()

                # 组装文档对象
                doc = TopicDocument(
                    # id=item_id（确保和 crawler item id 对齐，去重依赖它）
                    id=item_id,
                    # 主题 ID
                    topic_id=topic_id,
                    # 子目录类型
                    source_type=source_type,
                    # 相对路径
                    rel_path=target_rel,
                    # 标题
                    title=title,
                    # 作者
                    author=author,
                    # 来源 URL
                    source_url=source_url,
                    # 字符数
                    size_chars=size_chars,
                    # 入库时间
                    promoted_at=now_ms,
                )

                # 写 metadata JSONL
                if meta_fh is not None:
                    # 组装字典
                    info_dict = {
                        # ID
                        "id": doc.id,
                        # 类型
                        "source_type": doc.source_type,
                        # 相对路径
                        "rel_path": doc.rel_path,
                        # 标题
                        "title": doc.title,
                        # 作者
                        "author": doc.author,
                        # 来源 URL
                        "source_url": doc.source_url,
                        # 字符数
                        "size_chars": doc.size_chars,
                        # 入库时间
                        "promoted_at": doc.promoted_at,
                    }
                    try:
                        # 写一行 JSON
                        meta_fh.write(json.dumps(info_dict, ensure_ascii=False) + "\n")
                    except Exception:
                        # 写失败记日志
                        _log.exception("promote: append meta line failed")
                # 加入本次 promote 结果
                promoted.append(doc)
                # 登记进 existing_ids 避免本次循环里重复（极端情况）
                existing_ids.add(doc.id)

            # 关闭 metadata 文件句柄
            if meta_fh is not None:
                try:
                    # 安全关闭
                    meta_fh.close()
                except Exception:
                    # 关闭失败记日志
                    _log.exception("promote: close meta file failed")
            # 返回本次新 promote 的文档列表
            return promoted

    # --- 切块 / 向量 ---
    # 对单篇长文本做简单切块（标点/换行优先；不做 embed 依赖）
    # 参数 text：长文本字符串
    # 返回 list[tuple[str, int]]：(chunk_text, start_offset)
    def _chunk_text(self, text: str) -> list[tuple[str, int]]:
        # 空文本 → 空列表
        if not text:
            return []
        # 结果列表
        chunks: list[tuple[str, int]] = []
        # 起始游标
        start = 0
        # 文本总长度
        n = len(text)
        # 切块窗口大小
        win = self._chunk_chars
        # 重叠大小
        lap = min(self._chunk_overlap, max(0, win - 1))
        # 游标没到末尾就继续
        while start < n:
            # 窗口终点（不超文本长度）
            end = min(start + win, n)
            # 本块原始切片
            piece = text[start:end]

            # 最后一块直接收（不需要找边界，避免空块）
            if end >= n:
                # 空 piece 跳过
                if piece.strip():
                    # 加入最后一块
                    chunks.append((piece, start))
                # 结束循环
                break

            # 非最后一块：在 piece 尾部找"好的断点"（优先段落空行，其次标点换行，其次退回到 win/2）
            # 搜索尾部 20% 区间
            tail_len = max(40, win // 5)
            # 断点初值：end
            cut_pos = len(piece)
            # 候选断点子串列表
            candidates = ["\n\n", "\n。", "。\n", "\n", "。", "！", "？", ". ", "! ", "? ", ";", "；"]
            # 遍历候选
            for cand in candidates:
                # 在 piece 的后 tail_len 里找最后一次出现位置
                search_from = max(0, len(piece) - tail_len)
                # 从 search_from 开始找，但从尾找更近
                idx = piece.rfind(cand, search_from)
                # 找到了
                if idx >= search_from:
                    # 切点位置：候选词的结束
                    cut_pos = idx + len(cand)
                    # 已经找到合适的了（列表越前优先级越高）
                    break
            # 没找到任何候选（或切点太小 < 20% 窗口）→ 退回 win//2 的位置避免死循环
            if cut_pos <= win // 4:
                # 硬切在 win 位置
                cut_pos = len(piece)
            # 最终 piece：按 cut_pos 截断
            final_piece = piece[:cut_pos]
            # 空串跳过（没内容）
            if final_piece.strip():
                # 加入 chunks
                chunks.append((final_piece, start))
            # 下一块起点：end - overlap（保证重叠），但至少前进 1 字符避免死循环
            advance = max(1, cut_pos - lap)
            # 游标前进
            start += advance
        # 返回切块列表
        return chunks

    # 重新生成 topic 的向量索引（失败不抛异常，返回 bool 表示是否成功）
    # 参数 topic_id：主题 ID
    # 返回 bool：True 表示重建成功；False 表示失败/未启用
    def rebuild_vector_index(self, topic_id: str) -> bool:
        # 没向量工厂 / 没 embed → 无法重建，返回失败
        if self._vec_factory is None or self._embed_fn is None:
            # 记 warning
            _log.warning("rebuild_vector_index skipped: embed_fn or vector_store_factory not configured")
            # 返回失败
            return False
        # topic 锁（防止并发重建）
        with self._lock_for(topic_id):
            # 先确保 topic 目录存在
            topic_dir = self.ensure_topic(topic_id)
            # 向量子目录
            vec_dir = topic_dir / _VECTOR_DIRNAME
            # 确保存在
            vec_dir.mkdir(parents=True, exist_ok=True)

            # 先拿文档列表
            docs = self.list_documents(topic_id)
            # 没文档：返回成功（空索引也合理）
            if not docs:
                # 返回成功
                return True

            # --- 1. 计算 embed 维度（调用一次 embed_fn 拿维度） ---
            # embed 调用异常处理
            try:
                # 调用 embed_fn 拿第一篇前几个字符的向量
                probe_vec = self._embed_fn("probe")
            except Exception:
                # 失败记日志
                _log.exception("rebuild_vector_index: embed_fn call failed (probe)")
                # 返回失败
                return False
            # 向量得是 list[float] 或等价 Iterable
            try:
                # 转 list 拿长度
                probe_list = list(probe_vec)
            except Exception:
                # 拿不到维度
                _log.error("rebuild_vector_index: embed_fn return cannot list()")
                return False
            # 空向量不行
            if not probe_list:
                # 记错误
                _log.error("rebuild_vector_index: embed_fn returns empty vector")
                return False
            # 维度
            dims = len(probe_list)

            # --- 2. 打开/重建向量存储（先扫所有块再批量 upsert，减少 IO） ---
            # 向量存储路径（按 topic 建独立库，便于按 topic 管理）
            db_path = vec_dir / "vectors.sqlite"
            # 先尝试创建 VectorStore，失败返回 False
            try:
                # 创建向量库实例（工厂 lambda）
                vec_store = self._vec_factory(db_path, dims)
            except Exception:
                # 创建失败
                _log.exception("rebuild_vector_index: open vector store failed")
                # 返回 False
                return False

            # 先清掉旧索引（重建语义 = 删旧 + 插新）；VectorStore 自己实现 clear_all()
            try:
                # 尝试清空
                vec_store.clear_all()
            except Exception:
                # 有些后端可能不支持 clear_all；尝试删除 db_path 再重建（失败就算了）
                try:
                    # 关当前连接
                    vec_store.close()
                except Exception:
                    # 关失败忽略
                    pass
                try:
                    # 删物理文件
                    db_path.unlink(missing_ok=True)
                    # 再创建一次
                    vec_store = self._vec_factory(db_path, dims)
                except Exception:
                    # 重建失败，返回
                    _log.exception("rebuild_vector_index: recreate store after clear failed")
                    return False

            # --- 3. 每篇文档切块 → 取向量 → upsert 进向量库 ---
            # 统计成功/失败
            ok_chunks = 0
            # 失败文档数
            fail_docs = 0
            # 遍历文档
            for doc in docs:
                # 文档绝对路径
                p = topic_dir / doc.rel_path
                # 文件不存在跳过（用户手动删了文件）
                if not p.is_file():
                    continue
                # 读文档文本
                try:
                    # 以 UTF-8 读，遇坏字符替换
                    text = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    # 读失败记 warning + 统计
                    fail_docs += 1
                    # 继续
                    continue
                # 切块
                chunks = self._chunk_text(text)
                # 空文档没块跳过
                if not chunks:
                    continue
                # 块文本列表（用于 embed 批量调，避免 n 次 RPC）
                chunk_texts = [c[0] for c in chunks]
                # 块偏移
                chunk_offsets = [c[1] for c in chunks]
                # Embed（批量）
                try:
                    # 假设 embed_fn 支持 list[str]→list[list[float]]；不支持的话 fallback 逐次调
                    vecs = self._embed_fn(chunk_texts)
                except Exception:
                    # 批量失败 → 逐个 embed（更稳，但慢）
                    vecs = []
                    # 遍历每个 chunk
                    for ct in chunk_texts:
                        try:
                            # 单条 embed
                            v = self._embed_fn(ct)
                            # 加进 vecs
                            vecs.append(v)
                        except Exception:
                            # 单条失败塞 None 占位（后面跳过）
                            vecs.append(None)
                # 遍历块 + 向量，写入向量库
                for idx, (chunk, off) in enumerate(chunks):
                    # 取对应向量
                    v = vecs[idx] if idx < len(vecs) else None
                    # None 跳过
                    if v is None:
                        continue
                    # 保证是 list[float]
                    try:
                        # list 化
                        v_list = list(v)
                    except Exception:
                        # 转换失败跳过
                        continue
                    # 维度不匹配跳过
                    if len(v_list) != dims:
                        continue
                    # chunk 唯一 id = {doc.id}#{idx}
                    chunk_id = f"{doc.id}#{idx}"
                    # 元信息：保留 doc_id + source_type + start_offset，便于 retrieve 时反查
                    meta: dict[str, Any] = {
                        # 文档 ID
                        "doc_id": doc.id,
                        # 主题 ID
                        "topic_id": topic_id,
                        # 目录类型
                        "source_type": doc.source_type,
                        # 相对路径
                        "rel_path": doc.rel_path,
                        # 文档标题
                        "title": doc.title,
                        # 作者
                        "author": doc.author,
                        # 来源 URL
                        "source_url": doc.source_url,
                        # 块起始偏移（字符）
                        "start_offset": int(off),
                        # 块长度（字符）
                        "length": int(len(chunk)),
                        # 块索引
                        "chunk_index": int(idx),
                    }
                    # 写向量库
                    try:
                        # upsert（按 chunk_id 覆盖）
                        vec_store.upsert(chunk_id, v_list, chunk, meta)
                        # 成功计数+1
                        ok_chunks += 1
                    except Exception:
                        # upsert 失败记 warning
                        _log.exception("vec upsert failed: chunk_id=%s", chunk_id)
                        # 继续
                        continue
            # 关向量库
            try:
                # 安全关闭
                vec_store.close()
            except Exception:
                # 关失败忽略
                pass
            # 全部结束：记 info 日志（成功块 + 失败文档数）
            _log.info(
                "rebuild_vector_index done: topic=%s docs=%s ok_chunks=%d fail_docs=%d",
                topic_id,
                len(docs),
                ok_chunks,
                fail_docs,
            )
            # 返回成功
            return True
