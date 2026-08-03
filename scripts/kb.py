"""AIGameWorld-Studio · 知识库公共 CLI 工具（长期复用，不是一次性脚本）。

子命令
------
status <topic>                      查看 topic 的元数据 + chunk 数 + Qdrant 点数
clear  <topic>                      清空 topic（Qdrant 删 collection；kb_document 软删 + kb_chunk 真删）
ingest <topic> <file...>            把 1~N 个 PDF/MD/TXT 文件写入知识库（双写 SQLite 元数据 + Qdrant 向量）
  --chunk-size N                    每块字符数，默认 800
  --chunk-overlap N                 重叠字符数，默认 120
search <topic> <query>              语义检索（BGE-M3 dense embedding + Qdrant dense top-k）
  --top K                           返回 top-K，默认 3
  --with-sqlite-preview             命中的 chunk 无正文时回退查 kb_chunk.text_preview

约定
----
- CLI 层只负责：解析参数 → 构造 KnowledgeManager → 调用方法 → 格式化输出。
- 所有业务逻辑（文件加载、元数据识别、SQL 操作、向量检索）均在 service 层实现。
- 默认向量后端：Qdrant（Docker 本机 :6333，由 KBVectorStoreFactory 负责）。
- SQLite 元数据 DB：data/studio.db（migration 自动执行）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# 避免 Qdrant 客户端走代理
for _nk in ("NO_PROXY", "no_proxy"):
    _v = os.environ.get(_nk, "")
    os.environ[_nk] = (_v + ",127.0.0.1,localhost,::1") if _v else "127.0.0.1,localhost,::1"
for _nk in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_nk, None)


# ---------------------------------------------------------------------------
# 唯一的 service 构造入口
# ---------------------------------------------------------------------------
def _make_manager():
    """构造 KnowledgeManager 实例（CLI 层唯一的服务入口）."""
    from src.services.knowledge import KnowledgeManager

    return KnowledgeManager(
        project_root=PROJECT_ROOT,
        auto_init_schema=True,
        created_by="cli",
    )


# ---------------------------------------------------------------------------
# 子命令：纯调用 + 格式化输出
# ---------------------------------------------------------------------------
def cmd_status(args) -> int:
    kb = _make_manager()
    try:
        info = kb.status_detail(args.topic)
        print(f"topic              = {info['topic']}")
        print(f"vector backend     = {info['backend']} ({info.get('qdrant_url') or '—'})")
        print(f"kb_document  done  = {info['documents_done']}")
        print(f"kb_document  other = {info['documents_other']}")
        print(f"kb_document  del.  = {info['documents_deleted']}")
        print(f"kb_chunk   total   = {info['chunks_total']}")
        pts = info.get("qdrant_points")
        print(f"Qdrant collection  = {info['collection']}  points = {pts if pts is not None else '（不可达或不存在）'}")

        recent = info.get("recent_docs") or []
        if recent:
            print("  docs（最近 10 条）:")
            for d in recent:
                print(
                    f"    • v{d['version']} id={d['id'][:10]}… {d['title'][:36]}"
                    f"  {d['file_name'][:32]}  {d['file_size_kb']}KB"
                )
        return 0
    finally:
        kb.close()


def cmd_clear(args) -> int:
    kb = _make_manager()
    try:
        r = kb.clear(args.topic)
        sd = r["soft_deleted_documents"]
        cr = r["total_chunks"]
        print(f"KnowledgeManager.clear() => ok={r['ok']}  soft_deleted={sd}  chunks_remaining={cr}")

        # 清空后复查
        info = kb.status_detail(args.topic)
        pts = info.get("qdrant_points")
        print(
            f"之后: kb_document(done)={info['documents_done']}  "
            f"kb_chunk={info['chunks_total']}  "
            f"Qdrant.points={pts if pts is not None else '—'}"
        )
        return 0
    finally:
        kb.close()


def cmd_ingest(args) -> int:
    files = [Path(f) for f in args.files]
    kb = _make_manager()
    try:
        # 检查向量后端
        info = kb.status_detail(args.topic)
        if info.get("backend") != "qdrant":
            print(
                f"[WARN] 当前向量后端={info.get('backend')}，不是 qdrant。"
                "请确认 Docker 容器 ags_qdrant_studio 是否正常运行并监听 6333。",
                file=sys.stderr,
            )

        print(f"读取 {len(files)} 个文件…")
        r = kb.ingest_files(
            args.topic,
            files,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )

        if not r.get("ok"):
            print(f"[FATAL] {r.get('error', '未知错误')}")
            return 2

        print(
            f"ingest_files() OK: docs={len(r['doc_ids'])}  chunks={r['chunks']}"
            f"  耗时 {r.get('elapsed', 0):.1f}s（含 embedding + SQLite 双写 + Qdrant upsert）"
        )

        # 完成后复查
        info = kb.status_detail(args.topic)
        pts = info.get("qdrant_points")
        print(
            f"完成: kb_document(done)={info['documents_done']}  "
            f"kb_chunk={info['chunks_total']}  "
            f"Qdrant.points={pts if pts is not None else '—'}"
        )
        return 0
    finally:
        kb.close()


def cmd_search(args) -> int:
    kb = _make_manager()
    try:
        hits = kb.search_with_meta(
            args.topic,
            args.query,
            top_k=args.top,
        )
        collection = f"kb_{args.topic}"
        print(f"Q: {args.query}  (top-{args.top} dense @ {collection})")
        if not hits:
            print("  （0 条结果）")
            return 0
        for rank, h in enumerate(hits, 1):
            meta = h.get("metadata") or {}
            vol = meta.get("chronicle_volume") or ""
            page = meta.get("page_number") or meta.get("page_label") or "?"
            fn = meta.get("file_name") or ""
            score = float(h.get("score_cosine_sim") or 0.0)
            text = h.get("text") or ""
            head = " ".join(text.strip().split())[:160]
            if vol:
                extra = f"  [{vol} p{page}]"
            else:
                extra = f"  [{(fn or '?')[:28]} p{page}]"
            print(f"  #{rank}  s={score:.3f}{extra}\n      {head}…")
        return 0
    finally:
        kb.close()


# ---------------------------------------------------------------------------
# argparse 入口
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="AIGameWorld-Studio 知识库公共 CLI（长期工具，非一次性脚本）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", metavar="status|clear|ingest|search")

    p_st = sub.add_parser("status", help="查看 topic 状态")
    p_st.add_argument("topic")
    p_st.set_defaults(func=cmd_status)

    p_cl = sub.add_parser("clear", help="清空 topic（双写 SQLite 软删 + Qdrant collection 删）")
    p_cl.add_argument("topic")
    p_cl.add_argument("--created-by", default="cli_kb_tool")
    p_cl.set_defaults(func=cmd_clear)

    p_in = sub.add_parser(
        "ingest",
        help="把 PDF/MD/TXT 文件写入知识库（chunk → BGE-M3 embedding → SQLite 双写 + Qdrant）",
    )
    p_in.add_argument("topic")
    p_in.add_argument("files", nargs="+", help="本地 PDF/MD/TXT 文件路径，支持多个")
    p_in.add_argument("--chunk-size", type=int, default=800)
    p_in.add_argument("--chunk-overlap", type=int, default=120)
    p_in.set_defaults(func=cmd_ingest)

    p_sr = sub.add_parser("search", help="语义检索（BGE-M3 dense + Qdrant top-k）")
    p_sr.add_argument("topic")
    p_sr.add_argument("query")
    p_sr.add_argument("--top", type=int, default=3)
    p_sr.add_argument("--with-sqlite-preview", action="store_true", default=True)
    p_sr.set_defaults(func=cmd_search)

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
