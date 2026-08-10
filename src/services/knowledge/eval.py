"""知识库 RAG 效果量化评估（离线，确定性指标）.

对标 spec-rag-eval：对任一 topic 跑出 recall@k / MRR / hit_rate@k，
作为检索改动（chunk_size / 重排 / 融合权重）的前后对比基线。

- 评估集：``knowledge-bases/<topic>/eval/qa.jsonl``，每行一条 golden query。
- 指标只算 **doc 级召回**（不要求精确 chunk，避免过度严苛）。
- 复用现有 ``KnowledgeManager.search_with_meta``，不另写检索；
  关键：eval 必须 ``min_score=0.0``（只取 top_k），与线上默认一致。
- 纯本地计算，无 LLM 调用，秒级出结果；支持 ``--json`` 便于 CI / 前后对比存档。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


@dataclass
class QueryEval:
    """单条 query 的评估结果."""

    query: str
    expected: list[str]
    hit_rank: int | None  # 首个命中在 top_k 中的 1-based rank；未命中 None
    hit: bool
    retrieved_doc_ids: list[str]


@dataclass
class EvalReport:
    """整体评估报告."""

    topic: str
    top_k: int
    queries: int
    recall_at_k: float
    mrr: float
    hit_rate_at_k: float
    per_query: list[QueryEval] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# 评估集加载
# ---------------------------------------------------------------------------


def _default_eval_path(project_root: Path, topic: str) -> Path:
    return project_root / "knowledge-bases" / topic / "eval" / "qa.jsonl"


def load_queries(eval_file: Path) -> list[dict]:
    """从 JSONL 加载评估集.

    Raises
    ------
    FileNotFoundError
        评估集缺失时抛出，由 CLI 给出清晰报错。
    """
    if not eval_file.exists():
        raise FileNotFoundError(f"评估集不存在: {eval_file}（请先创建 {eval_file}，格式见 spec-rag-eval）")
    queries: list[dict] = []
    with open(eval_file, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                _log.warning("跳过非法行 %d: %s", lineno, e)
                continue
            q = (item.get("query") or "").strip()
            expected = item.get("expected_doc_ids") or item.get("expected_docs") or []
            if not q or not expected:
                continue
            queries.append({"query": q, "expected": list(expected), "note": item.get("note", "")})
    if not queries:
        raise ValueError(f"评估集为空或格式非法: {eval_file}")
    return queries


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------


def _extract_doc_ids(hit: dict) -> tuple[str, str]:
    """从单个 hit 提取 (document_id, file_name)，用于与 expected 比对.

    hit 结构：{text, score_cosine_sim, chunk_id, citation{...}, metadata{...}}。
    metadata 里父块/子块入库时都注入了 doc_id / file_name。
    """
    # 命中判定支持两种 key：内部 UUID（doc_id/document_id）或人类可读 file_name。
    # metadata 与 citation 两处都探测，兼容不同入库路径（父块/子块均注入 doc_id）。
    meta = hit.get("metadata") or {}
    citation = hit.get("citation") or {}
    doc_id = meta.get("doc_id") or meta.get("document_id") or citation.get("document_id") or ""
    file_name = meta.get("file_name") or citation.get("file_name") or ""
    return str(doc_id or ""), str(file_name or "")


def _normalize_expected(expected: list[str]) -> set[str]:
    """归一化 expected_doc_ids：去掉空白、空值；允许 file_name 或 doc_id."""
    out: set[str] = set()
    for e in expected:
        e = str(e or "").strip()
        if e:
            out.add(e)
    return out


def evaluate(
    manager: Any,
    topic: str,
    queries: list[dict],
    *,
    top_k: int = 5,
    query_rewrite: bool = False,
) -> EvalReport:
    """对每条 query 检索并计算 recall@k / MRR / hit_rate@k.

    Parameters
    ----------
    manager : KnowledgeManager
        用于调 ``search_with_meta``。
    queries : list[dict]
        ``[{"query":..., "expected":[...]}, ...]``。
    top_k : int
        检索 top_k（指标基于此）。
    """
    n = len(queries)
    if n == 0:
        raise ValueError("无评估 query")

    # 三个聚合指标的累加器：recall_hits 命中数 / mrr_sum 首个命中 rank 倒数累加 / hit_rate 命中数
    recall_hits = 0.0
    mrr_sum = 0.0
    hit_rate_hits = 0
    per_query: list[QueryEval] = []

    for item in queries:
        q = item["query"]
        expected = _normalize_expected(item["expected"])

        # 关键：min_score=0.0（只取 top_k，不按分数过滤），与线上默认行为一致，
        # 保证评估基线对齐实际检索配置。
        hits = manager.search_with_meta(topic, q, top_k=top_k, min_score=0.0, query_rewrite=query_rewrite)
        retrieved_doc_ids: list[str] = []
        hit_rank: int | None = None

        # 在 top_k 结果里找首个命中 expected 的位置（doc_id 或 file_name 任一匹配即算命中）。
        # rank 从 1 开始；MRR 用该 rank 的倒数。
        for rank, h in enumerate(hits, 1):
            doc_id, file_name = _extract_doc_ids(h)
            retrieved_doc_ids.append(doc_id or file_name)
            if expected and (doc_id in expected or file_name in expected):
                hit_rank = rank
                break

        hit = hit_rank is not None
        if hit:
            recall_hits += 1.0
            hit_rate_hits += 1
            mrr_sum += 1.0 / hit_rank  # type: ignore[operator]
        per_query.append(
            QueryEval(
                query=q,
                expected=sorted(expected),
                hit_rank=hit_rank,
                hit=hit,
                retrieved_doc_ids=retrieved_doc_ids,
            )
        )

    recall = recall_hits / n
    mrr = mrr_sum / n
    hit_rate = hit_rate_hits / n

    return EvalReport(
        topic=topic,
        top_k=top_k,
        queries=n,
        recall_at_k=round(recall, 4),
        mrr=round(mrr, 4),
        hit_rate_at_k=round(hit_rate, 4),
        per_query=per_query,
    )


def format_report(report: EvalReport, *, json_out: bool = False) -> str:
    """把评估报告格式化为人类可读表格或 JSON."""
    if json_out:
        return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    lines: list[str] = []
    lines.append(f"topic={report.topic}  top_k={report.top_k}  queries={report.queries}")
    lines.append(f"recall@{report.top_k}   = {report.recall_at_k}")
    lines.append(f"MRR              = {report.mrr}")
    lines.append(f"hit_rate@{report.top_k} = {report.hit_rate_at_k}")
    # 逐条明细（仅未命中/错位的简短提示）
    misses = [pq for pq in report.per_query if not pq.hit]
    if misses:
        lines.append(f"-- {len(misses)}/{report.queries} 条未命中 --")
        for pq in misses:
            lines.append(f"  [miss] {pq.query!r}  期望={pq.expected}")
    return "\n".join(lines)


def compare_reports(new: EvalReport, old: dict | EvalReport) -> str:
    """与历史报告对比，输出指标变化（delta），用于检索改动的回归/增益判断.

    old 可以是 dict（读自 --save 的 JSON 文件）或 EvalReport。
    仅对比三个聚合指标；评估集/top_k 不一致时给出警告。
    """
    # 统一从 dict（--save 的 JSON）或 EvalReport 对象读取旧指标，避免 CLI/库两种调用形态重复实现。
    old_recall = float(old.get("recall_at_k") if isinstance(old, dict) else old.recall_at_k)
    old_mrr = float(old.get("mrr") if isinstance(old, dict) else old.mrr)
    old_hit = float(old.get("hit_rate_at_k") if isinstance(old, dict) else old.hit_rate_at_k)

    # 输出每项 Δ（新-旧），正值=提升、负值=回退；用于检索改动的回归/增益判断。
    old_top_k = old.get("top_k") if isinstance(old, dict) else old.top_k
    lines: list[str] = []
    lines.append(f"对比 topic={new.topic}  (new top_k={new.top_k} vs old top_k={old_top_k})")
    lines.append(f"  recall@{new.top_k}  {old_recall} -> {new.recall_at_k}   (Δ {new.recall_at_k - old_recall:+.4f})")
    lines.append(f"  MRR           {old_mrr} -> {new.mrr}   (Δ {new.mrr - old_mrr:+.4f})")
    lines.append(f"  hit_rate@{new.top_k} {old_hit} -> {new.hit_rate_at_k}   (Δ {new.hit_rate_at_k - old_hit:+.4f})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 从线上审计日志构建评估集（真实查询弱监督）
# ---------------------------------------------------------------------------


def build_queries_from_audit(
    manager: Any,
    topic: str,
    *,
    limit: int = 200,
    min_chars: int = 2,
    top_hits: int = 1,
) -> list[dict]:
    """从 kb_audit 真实查询日志构建评估集（弱监督标注）.

    企业级评估集的第一优先来源是**真实用户查询**（比手写/LLM 合成更贴近线上分布）。

    - 从 ``manager.audit_list(topic)`` 取 ``op == 'retrieve'`` 的 query_text，去重。
    - 对每条 query 用 ``search_with_meta(top_k=top_hits)`` 取 top 命中文档作**弱 expected**：
      假设"用户搜了这个 query，说明该内容存在于知识库且应被召回"，top1 即为弱标注。
    - 仅保留检索有命中的 query（无命中则无法标注 expected，跳过并计入 skipped）。
    - 返回与 ``load_queries`` 兼容的 dict 列表，可直接写回 qa.jsonl 或喂给 ``evaluate``。

    .. note::
        这是**弱监督**基线：expected 来自检索自身 top 命中，存在"自我印证"偏差（指标天然偏高）。
        建议人工抽检/精修后使用，或作为回归冒烟集而非严格质量门槛。
    """
    logs = manager.audit_list(topic, limit=limit) if hasattr(manager, "audit_list") else []
    seen: set[str] = set()
    queries: list[dict] = []
    skipped = 0

    for row in logs or []:
        op = row.get("op") or ""
        q = (row.get("query_text") or "").strip()
        if op != "retrieve" or not q or len(q) < min_chars or q in seen:
            continue
        seen.add(q)
        # 弱标注：取检索 top1 的 doc_id（优先）或 file_name
        try:
            hits = manager.search_with_meta(topic, q, top_k=top_hits, min_score=0.0)
        except Exception as e:
            _log.warning("[eval] query=%r 检索失败，跳过: %s", q[:50], e)
            skipped += 1
            continue
        if not hits:
            skipped += 1
            continue
        doc_id, file_name = _extract_doc_ids(hits[0])
        expected = [doc_id] if doc_id else ([file_name] if file_name else [])
        if not expected:
            skipped += 1
            continue
        queries.append(
            {
                "query": q,
                "expected": expected,
                "note": "from_audit(weak-label)",
            }
        )

    if not queries:
        raise ValueError(f"从审计日志未提取到有效评估 query（topic={topic}, 日志 {len(logs)} 条, 跳过 {skipped}）")
    _log.info("[eval] 从审计日志构建评估集: %d 条 (去重后), 跳过 %d", len(queries), skipped)
    return queries
