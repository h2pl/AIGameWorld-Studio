"""KnowledgeRAG 评估模块单元测试（recall@k / MRR / hit_rate@k 计算）."""

from pathlib import Path

import pytest

from src.services.knowledge.eval import (
    _extract_doc_ids,
    build_queries_from_audit,
    compare_reports,
    evaluate,
    format_report,
    load_queries,
)


def _mk_hit(doc_id: str = "", file_name: str = "", text: str = "x") -> dict:
    return {
        "text": text,
        "score_cosine_sim": 0.9,
        "distance": 0.1,
        "chunk_id": "c1",
        "citation": {"document_id": doc_id, "file_name": file_name},
        "metadata": {"doc_id": doc_id, "file_name": file_name},
    }


class _FakeManager:
    """伪造 manager：按顺序返回预设 hits + 预设审计日志."""

    def __init__(self, plan: list[list[dict]] | None = None, audit_logs: list[dict] | None = None):
        self._plan = list(plan) if plan else []
        self._audit_logs = audit_logs or []
        self.calls: list[tuple[str, str, int]] = []

    def search_with_meta(self, topic, query, *, top_k=5, min_score=0.0, query_rewrite=False):
        self.calls.append((topic, query, top_k))
        hits = self._plan.pop(0) if self._plan else []
        return hits

    def audit_list(self, topic, *, limit=100):
        return self._audit_logs[:limit]


class TestMetrics:
    def test_extract_doc_ids_from_meta(self):
        doc_id, fname = _extract_doc_ids(_mk_hit(doc_id="abc", file_name="chronicle.pdf"))
        assert doc_id == "abc"
        assert fname == "chronicle.pdf"

    def test_recall_mrr_hitrate_all_hit(self):
        """所有 query 都命中且 rank=1 → recall=1, MRR=1, hit_rate=1."""
        m = _FakeManager(
            [
                [_mk_hit(doc_id="d1")],
                [_mk_hit(doc_id="d2")],
            ]
        )
        queries = [
            {"query": "q1", "expected": ["d1"]},
            {"query": "q2", "expected": ["d2"]},
        ]
        rep = evaluate(m, "t", queries, top_k=5)
        assert rep.recall_at_k == 1.0
        assert rep.mrr == 1.0
        assert rep.hit_rate_at_k == 1.0
        assert rep.queries == 2

    def test_recall_partial(self):
        """2 条 query 只命中 1 条 → recall=0.5, MRR 取命中那条 rank 倒数."""
        m = _FakeManager(
            [
                [_mk_hit(doc_id="d1"), _mk_hit(doc_id="other")],
                [_mk_hit(doc_id="not_target")],
            ]
        )
        queries = [
            {"query": "q1", "expected": ["d1"]},
            {"query": "q2", "expected": ["target"]},
        ]
        rep = evaluate(m, "t", queries, top_k=5)
        assert rep.recall_at_k == 0.5
        assert rep.hit_rate_at_k == 0.5
        # q1 rank=1 → 1/1；q2 未命中 → 0；MRR = (1 + 0)/2
        assert rep.mrr == 0.5

    def test_mrr_uses_rank(self):
        """首个命中在 rank 2 → MRR 贡献 1/2."""
        m = _FakeManager([[_mk_hit(doc_id="a"), _mk_hit(doc_id="target")]])
        queries = [{"query": "q", "expected": ["target"]}]
        rep = evaluate(m, "t", queries, top_k=5)
        assert rep.recall_at_k == 1.0
        assert rep.mrr == 0.5
        assert rep.per_query[0].hit_rank == 2

    def test_match_by_file_name(self):
        """expected 可用 file_name 匹配（不绑死 UUID）."""
        m = _FakeManager([[_mk_hit(doc_id="", file_name="chronicle_vol2.pdf")]])
        queries = [{"query": "q", "expected": ["chronicle_vol2.pdf"]}]
        rep = evaluate(m, "t", queries, top_k=5)
        assert rep.recall_at_k == 1.0
        assert rep.per_query[0].hit

    def test_empty_queries_raises(self):
        m = _FakeManager([])
        with pytest.raises(ValueError):
            evaluate(m, "t", [], top_k=5)

    def test_expected_normalization(self):
        """expected 去空白、空值；mixed doc_id + file_name."""
        m = _FakeManager([[_mk_hit(doc_id="abc")]])
        queries = [{"query": "q", "expected": ["", "  ", "abc"]}]
        rep = evaluate(m, "t", queries, top_k=5)
        assert rep.recall_at_k == 1.0


class TestLoadQueries:
    def test_load_valid_jsonl(self, tmp_path: Path):
        f = tmp_path / "qa.jsonl"
        f.write_text(
            '{"query": "阿尔萨斯", "expected_doc_ids": ["d1"]}\n'
            '{"query": "燃烧军团", "expected_doc_ids": ["chronicle.pdf"], "note": "x"}\n',
            encoding="utf-8",
        )
        qs = load_queries(f)
        assert len(qs) == 2
        assert qs[0]["query"] == "阿尔萨斯"
        assert qs[0]["expected"] == ["d1"]
        assert qs[1]["expected"] == ["chronicle.pdf"]

    def test_skip_blank_and_comment_lines(self, tmp_path: Path):
        f = tmp_path / "qa.jsonl"
        f.write_text('# comment\n\n{"query": "a", "expected_doc_ids": ["d"]}\n', encoding="utf-8")
        qs = load_queries(f)
        assert len(qs) == 1

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_queries(tmp_path / "nope.jsonl")

    def test_empty_file_raises(self, tmp_path: Path):
        f = tmp_path / "qa.jsonl"
        f.write_text("\n# only comment\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_queries(f)

    def test_skips_invalid_json_line(self, tmp_path: Path):
        f = tmp_path / "qa.jsonl"
        f.write_text('{"query": "ok", "expected_doc_ids": ["d"]}\nnot-json\n', encoding="utf-8")
        qs = load_queries(f)
        assert len(qs) == 1
        assert qs[0]["query"] == "ok"


class TestFormatReport:
    def test_json_output(self, tmp_path: Path):
        m = _FakeManager([[_mk_hit(doc_id="d1")]])
        rep = evaluate(m, "t", [{"query": "q", "expected": ["d1"]}], top_k=5)
        import json

        out = json.loads(format_report(rep, json_out=True))
        assert out["recall_at_k"] == 1.0
        assert out["topic"] == "t"
        assert out["queries"] == 1

    def test_text_output_contains_metrics(self, tmp_path: Path):
        m = _FakeManager([[_mk_hit(doc_id="d1")]])
        rep = evaluate(m, "t", [{"query": "q", "expected": ["d1"]}], top_k=5)
        out = format_report(rep)
        assert "recall@5" in out
        assert "MRR" in out
        assert "hit_rate@5" in out


class TestCompareReports:
    """--compare 前后对比（检索改动的回归/增益判断）."""

    def test_compare_with_dict(self):
        from src.services.knowledge.eval import EvalReport

        old = {"top_k": 5, "recall_at_k": 0.667, "mrr": 0.625, "hit_rate_at_k": 0.667}
        new = EvalReport(topic="t", top_k=5, queries=12, recall_at_k=1.0, mrr=0.958, hit_rate_at_k=1.0)
        out = compare_reports(new, old)
        assert "Δ +0.3330" in out  # recall 提升
        assert "recall@5" in out

    def test_compare_with_report_object(self):
        from src.services.knowledge.eval import EvalReport

        old = EvalReport(topic="t", top_k=5, queries=12, recall_at_k=0.8, mrr=0.5, hit_rate_at_k=0.8)
        new = EvalReport(topic="t", top_k=5, queries=12, recall_at_k=0.8, mrr=0.6, hit_rate_at_k=0.8)
        out = compare_reports(new, old)
        assert "Δ +0.1000" in out  # MRR 提升
        assert "Δ +0.0000" in out  # recall 不变


class TestBuildFromAudit:
    """从 kb_audit 真实查询日志构建评估集（弱监督标注）."""

    def test_extracts_retrieve_queries(self):
        """只取 op=='retrieve' 的 query，去重，弱标注 top1 的 doc_id."""
        logs = [
            {"op": "retrieve", "query_text": "阿尔萨斯"},
            {"op": "retrieve", "query_text": "阿尔萨斯"},  # 重复应去重
            {"op": "retrieve", "query_text": "燃烧军团"},
            {"op": "index_done", "query_text": "不应被取"},  # 非 retrieve 跳过
            {"op": "retrieve", "query_text": "a"},  # 过短跳过
        ]
        m = _FakeManager(
            audit_logs=logs,
            plan=[
                [_mk_hit(doc_id="d_arthas")],
                [_mk_hit(doc_id="d_burning")],
            ],
        )
        qs = build_queries_from_audit(m, "t", min_chars=2)
        # "阿尔萨斯" 去重后 1 条 + "燃烧军团" 1 条 = 2 条
        assert len(qs) == 2
        assert qs[0]["query"] == "阿尔萨斯"
        assert qs[0]["expected"] == ["d_arthas"]
        assert qs[1]["query"] == "燃烧军团"
        assert qs[1]["expected"] == ["d_burning"]
        assert qs[0]["note"] == "from_audit(weak-label)"

    def test_skips_query_with_no_hits(self):
        """检索无命中的 query 跳过（无法弱标注 expected）."""
        logs = [{"op": "retrieve", "query_text": "不存在的内容"}]
        m = _FakeManager(audit_logs=logs, plan=[[]])
        with pytest.raises(ValueError):
            build_queries_from_audit(m, "t")

    def test_empty_audit_raises(self):
        """无审计日志或无有效 query → 抛 ValueError."""
        m = _FakeManager(audit_logs=[], plan=[])
        with pytest.raises(ValueError):
            build_queries_from_audit(m, "t")

    def test_uses_file_name_when_no_doc_id(self):
        """top1 无 doc_id 时用 file_name 作弱标注."""
        logs = [{"op": "retrieve", "query_text": "泰坦"}]
        m = _FakeManager(audit_logs=logs, plan=[[_mk_hit(doc_id="", file_name="chronicle.pdf")]])
        qs = build_queries_from_audit(m, "t")
        assert qs[0]["expected"] == ["chronicle.pdf"]
