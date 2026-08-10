"""LLM-as-judge 检索质量评估（context_precision / context_relevancy）.

对标 RAGAS 的 LLM 判分指标，但轻量、复用项目现有 LLM（``src.llm.client.get_model``），
不引入 RAGAS 依赖（当前 Python 3.14 + langchain-community 0.4.2 装不上 ragas）。

适用范围
--------
本项目当前是**纯检索系统**（`search_with_meta` 只返回 chunks，无生成答案环节）。
因此 LLM-judge 聚焦评估**检索返回的 context 质量**，而非生成答案质量：

- ``judge_context_precision``：LLM 逐段判断检索返回的 context 是否与 query 相关，
  衡量「召回够不够精准」（对应 RAGAS context_precision 的确定性近似）。
- ``judge_context_relevancy``：LLM 判断检索到的 context 整体是否充分/相关于 query。

设计原则（对齐 spec-rag-eval 的「无参考」精神）：
- 不依赖人工标注的 expected，直接用 LLM 判断相关性与否——可评估真实线上查询。
- 复用 ``get_model()``，不新增 LLM 依赖；LLM 不可用/失败时**降级返回 None**，绝不阻塞评估。
- 结构化输出（要求 LLM 返回 JSON），解析失败降级；prompt 明确「只输出 JSON」。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# 让 LLM 逐段判断相关性的 prompt（简体中文，只输出 JSON）
_PRECISION_PROMPT = """你是检索质量评估员。给定一个用户问题 Q 和检索返回的若干段落 C1..CN，
判断每个段落是否与 Q 相关（能帮助回答 Q）。

规则：
- 相关记为 1，不相关记为 0；不要解释，只输出 JSON。
- 输出格式必须是合法的 JSON 数组，长度与段落数一致，每个元素为 0 或 1：
  {{"relevant": [0, 1, 1, ...]}}

Q: {query}

段落：
{numbered_contexts}
"""

# 整体相关性 prompt（返回 0~1 分数）
_RELEVANCY_PROMPT = """你是检索质量评估员。给定用户问题 Q 和检索返回的上下文 Context，
判断该上下文整体能否帮助回答 Q。

规则：只输出一个 0 到 1 之间的 JSON 数字（保留 2 位小数），不要解释：
  {{"score": 0.85}}
- 1.0 = 完全相关且充分；0.0 = 完全不相关/无法回答 Q。

Q: {query}

Context:
{context}
"""


def _numbered_contexts(contexts: list[str]) -> str:
    """把段落列表编号成 prompt 文本."""
    return "\n".join(f"C{i + 1}: {c[:500]}" for i, c in enumerate(contexts))


def _extract_json(text: str) -> Any:
    """从 LLM 输出中稳健提取 JSON（容忍 code fence / 前后杂文）."""
    if not text:
        return None
    text = text.strip()
    # 去掉 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # 截取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def judge_context_precision(
    query: str,
    contexts: list[str],
    *,
    llm: Any | None = None,
) -> dict | None:
    """用 LLM 逐段判断 context 是否与 query 相关，返回 context_precision.

    Parameters
    ----------
    query : str
        用户问题。
    contexts : list[str]
        检索返回的段落文本（与 ``search_with_meta`` 的 hit.text 对应）。
    llm : BaseChatModel | None
        LangChain LLM。None 时用 ``src.llm.client.get_model()``。

    Returns
    -------
    dict | None
        ``{"relevant": [...], "context_precision": float}``；LLM 不可用/解析失败返回 None。

    context_precision = 相关段落数 / 总段落数（简单近似，未按排名加权）。
    """
    if not contexts:
        return None
    if llm is None:
        llm = _get_llm()
    if llm is None:
        return None
    try:
        from langchain_core.messages import HumanMessage

        # 构造判分 prompt：把段落编号后拼进模板，要求 LLM 输出每段 0/1 的 JSON 数组。
        user_prompt = _PRECISION_PROMPT.format(
            query=query,
            numbered_contexts=_numbered_contexts(contexts),
        )
        resp = llm.invoke([HumanMessage(content=user_prompt)])
        data = _extract_json(getattr(resp, "content", "") or "")
        # LLM 输出非 dict 或缺 relevant 字段 → 视为解析失败，降级返回 None。
        if not isinstance(data, dict) or "relevant" not in data:
            return None
        relevant = data["relevant"]
        # 规整为与段落等长的 0/1 列表：截断超长部分，不足用 0 补齐（缺失视为不相关）。
        relevant = [1 if r else 0 for r in relevant[: len(contexts)]]
        while len(relevant) < len(contexts):
            relevant.append(0)
        # context_precision = 相关段落占比（简单近似，未按排名加权）。
        precision = round(sum(relevant) / len(relevant), 4) if relevant else 0.0
        return {"relevant": relevant, "context_precision": precision}
    except Exception as e:  # noqa: BLE001
        _log.warning("[llm-judge] context_precision 失败，降级: %s", e)
        return None


def judge_context_relevancy(
    query: str,
    context: str,
    *,
    llm: Any | None = None,
) -> float | None:
    """用 LLM 判断 context 整体与 query 的相关性，返回 0~1 分数.

    失败返回 None（降级）。
    """
    if llm is None:
        llm = _get_llm()
    if llm is None:
        return None
    try:
        from langchain_core.messages import HumanMessage

        # 整体相关性判分：context 截断到 2000 字控制 token 成本，要求 LLM 输出 0~1 分数。
        user_prompt = _RELEVANCY_PROMPT.format(query=query, context=context[:2000])
        resp = llm.invoke([HumanMessage(content=user_prompt)])
        data = _extract_json(getattr(resp, "content", "") or "")
        if not isinstance(data, dict) or "score" not in data:
            return None
        try:
            score = float(data["score"])
        except TypeError, ValueError:
            return None
        # 分数裁剪到 [0,1]，容忍 LLM 偶发越界输出。
        return max(0.0, min(1.0, score))
    except Exception as e:  # noqa: BLE001
        _log.warning("[llm-judge] context_relevancy 失败，降级: %s", e)
        return None


def _get_llm() -> Any | None:
    """懒加载项目 LLM；不可用时返回 None（降级）."""
    try:
        from src.llm.client import get_model

        return get_model()
    except Exception as e:  # noqa: BLE001
        _log.warning("[llm-judge] 获取 LLM 失败，LLM 评估降级: %s", e)
        return None
