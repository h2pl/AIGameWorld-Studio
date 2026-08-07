"""RAG 检索质量评估 harness（最小可用版）.

用法:
    python scripts/rag_eval.py --topic genshin --k 5
    python scripts/rag_eval.py --topic genshin --qa qa.jsonl --k 10

qa.jsonl 格式（每行一个 JSON）:
    {"query": "蒙德城的统治者是谁?", "relevant_doc_ids": ["<doc_uuid>", ...]}
    若不提供 qa 文件，则进入交互模式：逐行输入 query，人工判定命中（y/n）后统计。

指标:
    - Precision@K / Recall@K / MRR（基于 doc_id 粒度，而非 chunk）
    - 同时打印每次查询的命中来源（dense / bm25 / hybrid）用于诊断

依赖: 复用项目内 KnowledgeManager（dense + BM25 + RRF + rerank 全链路）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让脚本能 import src 包（不依赖 pip install -e）
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.services.knowledge.manager import KnowledgeManager  # noqa: E402


def _doc_id_of(hit: dict) -> str:
    """从 hybrid 返回结果里取 doc_id（metadata 或 payload 里）."""
    meta = hit.get("metadata") or {}
    return str(meta.get("doc_id") or meta.get("document_id") or "")


def evaluate(manager: KnowledgeManager, topic: str, qa: list[dict], k: int) -> dict:
    """对 qa 列表跑 hybrid 检索，返回指标聚合."""
    n = len(qa)
    prec_sum = rec_sum = mrr_sum = 0.0
    per_query: list[dict] = []

    for item in qa:
        query = item["query"]
        relevant = set(str(d) for d in item.get("relevant_doc_ids", []))
        hits = manager.search_with_meta(topic, query, top_k=k)
        hit_doc_ids = [_doc_id_of(h) for h in hits]
        # 去重保持顺序，统计前 K 内命中的相关文档
        seen: set[str] = set()
        topk_doc_ids: list[str] = []
        for d in hit_doc_ids:
            if d and d not in seen:
                seen.add(d)
                topk_doc_ids.append(d)
        topk_doc_ids = topk_doc_ids[:k]

        hit_set = set(topk_doc_ids) & relevant
        prec = len(hit_set) / len(topk_doc_ids) if topk_doc_ids else 0.0
        rec = len(hit_set) / len(relevant) if relevant else (1.0 if not topk_doc_ids else 0.0)
        # MRR
        mrr = 0.0
        for rank, d in enumerate(topk_doc_ids, start=1):
            if d in relevant:
                mrr = 1.0 / rank
                break
        prec_sum += prec
        rec_sum += rec
        mrr_sum += mrr
        per_query.append(
            {
                "query": query,
                "precision@k": round(prec, 4),
                "recall@k": round(rec, 4),
                "mrr": round(mrr, 4),
                "top_docs": topk_doc_ids[:3],
            }
        )

    return {
        "topic": topic,
        "k": k,
        "n_queries": n,
        "precision@k": round(prec_sum / n, 4) if n else 0.0,
        "recall@k": round(rec_sum / n, 4) if n else 0.0,
        "mrr": round(mrr_sum / n, 4) if n else 0.0,
        "per_query": per_query,
    }


def _interactive(manager: KnowledgeManager, topic: str, k: int) -> dict:
    """交互模式：用户输入 query，对返回 top-K 文档逐个判定相关（y/n），统计指标."""
    print(f"[rag_eval] 交互模式 topic={topic} k={k}（输入空行退出）")
    collected: list[dict] = []
    while True:
        try:
            query = input("\n查询> ").strip()
        except EOFError, KeyboardInterrupt:
            break
        if not query:
            break
        hits = manager.search_with_meta(topic, query, top_k=k)
        docs = []
        seen: set[str] = set()
        for h in hits:
            d = _doc_id_of(h)
            if d and d not in seen:
                seen.add(d)
                docs.append(d)
        docs = docs[:k]
        print(f"  命中文档({len(docs)}): {docs}")
        ans = input("  相关文档的编号(逗号分隔, 无则回车)> ").strip()
        if not ans:
            continue
        relevant: set[str] = set()
        for part in ans.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(docs):
                    relevant.add(docs[idx])
        collected.append({"query": query, "relevant_doc_ids": list(relevant)})
    return evaluate(manager, topic, collected, k)


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG 检索质量评估")
    ap.add_argument("--topic", required=True, help="主题 slug（如 genshin）")
    ap.add_argument("--k", type=int, default=5, help="Top-K")
    ap.add_argument("--qa", help="qa.jsonl 路径（省略则交互模式）")
    ap.add_argument("--project-root", default=None, help="项目根目录（默认 cwd）")
    args = ap.parse_args()

    root = Path(args.project_root) if args.project_root else _ROOT
    manager = KnowledgeManager(project_root=root, bootstrap_default_topics=False)

    if args.qa:
        qa: list[dict] = []
        for line in Path(args.qa).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            qa.append(json.loads(line))
        result = evaluate(manager, args.topic, qa, args.k)
    else:
        result = _interactive(manager, args.topic, args.k)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
