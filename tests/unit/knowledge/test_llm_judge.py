"""LLM-as-judge 评估模块单元测试（context_precision / context_relevancy）."""

from src.services.knowledge.llm_judge import (
    _extract_json,
    judge_context_precision,
    judge_context_relevancy,
)


class _FakeLLM:
    """伪造 LLM：按预设返回 content."""

    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages, **kwargs):
        class _Resp:
            content = self._content

        return _Resp()


class TestExtractJson:
    def test_plain_json(self):
        assert _extract_json('{"relevant": [1, 0]}') == {"relevant": [1, 0]}

    def test_code_fence(self):
        assert _extract_json('```json\n{"score": 0.8}\n```') == {"score": 0.8}

    def test_surrounding_text(self):
        assert _extract_json('结果如下：{"relevant": [1]} 完毕') == {"relevant": [1]}

    def test_invalid_returns_none(self):
        assert _extract_json("not json") is None
        assert _extract_json("") is None


class TestJudgeContextPrecision:
    def test_returns_precision(self):
        llm = _FakeLLM('{"relevant": [1, 0, 1]}')
        out = judge_context_precision("q", ["a", "b", "c"], llm=llm)
        assert out is not None
        assert out["context_precision"] == 0.6667
        assert out["relevant"] == [1, 0, 1]

    def test_empty_contexts_returns_none(self):
        assert judge_context_precision("q", [], llm=_FakeLLM("x")) is None

    def test_invalid_llm_output_returns_none(self):
        out = judge_context_precision("q", ["a"], llm=_FakeLLM("not json"))
        assert out is None

    def test_relevant_list_shorter_than_contexts_padded(self):
        llm = _FakeLLM('{"relevant": [1]}')
        out = judge_context_precision("q", ["a", "b", "c"], llm=llm)
        assert out is not None
        assert out["relevant"] == [1, 0, 0]
        assert out["context_precision"] == round(1 / 3, 4)


class TestJudgeContextRelevancy:
    def test_returns_score(self):
        llm = _FakeLLM('{"score": 0.85}')
        assert judge_context_relevancy("q", "ctx", llm=llm) == 0.85

    def test_clamps_to_0_1(self):
        llm = _FakeLLM('{"score": 1.5}')
        assert judge_context_relevancy("q", "ctx", llm=llm) == 1.0
        llm2 = _FakeLLM('{"score": -0.5}')
        assert judge_context_relevancy("q", "ctx", llm=llm2) == 0.0

    def test_invalid_returns_none(self):
        assert judge_context_relevancy("q", "ctx", llm=_FakeLLM("oops")) is None
