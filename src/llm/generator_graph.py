"""LangGraph 生成管线 / LangGraph Generation Pipeline.

StateGraph: generate_world_pack
  skeleton → lore → pcs → npcs → items → scenes → validate → retry
"""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph

from src.generator.generate import GenerateParams
from src.generator.generate import generate as gen_skeleton
from src.llm.client import StudioLLM
from src.llm.prompts import (
    ITEM_PROMPT,
    LORE_PROMPT,
    NPC_PROMPT,
    PC_PROMPT,
    SCENE_PROMPT,
    SYSTEM_PROMPT,
)
from src.validator.validate import validate_template as validate_world_pack

# ═══════════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════════


class GenState(TypedDict):
    """生成管线状态 / Generation pipeline state."""

    params: GenerateParams  # 用户参数
    output_dir: Path  # 输出目录
    world_name: str  # 世界名
    theme: str  # 世界主题描述
    retry_count: int  # 当前重试次数
    max_retries: int  # 最大重试次数
    errors: list[str]  # 校验错误列表
    done: bool  # 是否完成


# ═══════════════════════════════════════════════════════════════
# Nodes
# ═══════════════════════════════════════════════════════════════


async def node_skeleton(state: GenState) -> GenState:
    """生成骨架 YAML（结构保证合法）."""
    params = state["params"]
    out = gen_skeleton(params)
    state["output_dir"] = out
    state["world_name"] = params.world_name
    state["theme"] = getattr(params, "theme", params.world_name)
    state["retry_count"] = 0
    state["max_retries"] = getattr(params, "max_retries", 3)
    state["done"] = False
    return state


async def node_generate_lore(state: GenState) -> GenState:
    """LLM 填充 lore 文案."""
    llm = _get_llm(state)
    dir_ = state["output_dir"]
    lore_dir = dir_ / "lore"

    # 读取骨架占位符个数
    existing = sorted(lore_dir.glob("lore_*.yaml")) if lore_dir.exists() else []
    if not existing:
        return state

    count = len(existing)
    prompt = LORE_PROMPT.format(
        world_name=state["world_name"],
        count=count,
        theme=state["theme"],
    )

    result = await llm.generate(SYSTEM_PROMPT, prompt)
    _write_yaml_entities(lore_dir, result, "lore")
    return state


async def node_generate_pcs(state: GenState) -> GenState:
    """LLM 填充 PC 文案."""
    llm = _get_llm(state)
    dir_ = state["output_dir"]
    pc_dir = dir_ / "player_characters"

    existing = sorted(pc_dir.glob("*.yaml")) if pc_dir.exists() else []
    if not existing:
        return state

    count = len(existing)
    prompt = PC_PROMPT.format(
        world_name=state["world_name"],
        count=count,
        theme=state["theme"],
        prefix="player_character",
        existing_pcs="无",
    )
    result = await llm.generate(SYSTEM_PROMPT, prompt)
    _write_yaml_entities(pc_dir, result, "player_character")
    return state


async def node_generate_npcs(state: GenState) -> GenState:
    """LLM 填充 NPC 文案."""
    llm = _get_llm(state)
    dir_ = state["output_dir"]
    npc_dir = dir_ / "actors"

    existing = sorted(npc_dir.glob("*.yaml")) if npc_dir.exists() else []
    if not existing:
        return state

    count = len(existing)
    prompt = NPC_PROMPT.format(
        world_name=state["world_name"],
        count=count,
        theme=state["theme"],
        prefix="actor",
        existing_npcs="无",
    )
    result = await llm.generate(SYSTEM_PROMPT, prompt)
    _write_yaml_entities(npc_dir, result, "actor")
    return state


async def node_generate_items(state: GenState) -> GenState:
    """LLM 填充物品文案."""
    llm = _get_llm(state)
    dir_ = state["output_dir"]
    item_dir = dir_ / "items"

    existing = sorted(item_dir.glob("*.yaml")) if item_dir.exists() else []
    if not existing:
        return state

    count = len(existing)
    prompt = ITEM_PROMPT.format(
        world_name=state["world_name"],
        count=count,
        theme=state["theme"],
        prefix="item",
        existing_items="无",
    )
    result = await llm.generate(SYSTEM_PROMPT, prompt)
    _write_yaml_entities(item_dir, result, "item")
    return state


async def node_generate_scenes(state: GenState) -> GenState:
    """LLM 填充场景描述."""
    llm = _get_llm(state)
    dir_ = state["output_dir"]
    scene_dir = dir_ / "scenes"

    existing = sorted(scene_dir.glob("*.yaml")) if scene_dir.exists() else []
    if not existing:
        return state

    count = len(existing)
    prompt = SCENE_PROMPT.format(
        world_name=state["world_name"],
        count=count,
        theme=state["theme"],
        prefix="scene",
        existing_scenes="无",
    )
    result = await llm.generate(SYSTEM_PROMPT, prompt)
    _write_yaml_entities(scene_dir, result, "scene")
    return state


def node_validate(state: GenState) -> GenState:
    """校验生成结果."""
    result = validate_world_pack(state["output_dir"])
    if result.is_valid:
        state["done"] = True
        state["errors"] = []
    else:
        state["errors"] = result.errors
    return state


def node_retry(state: GenState) -> GenState:
    """重试计数."""
    state["retry_count"] += 1
    return state


# ═══════════════════════════════════════════════════════════════
# Edges
# ═══════════════════════════════════════════════════════════════


def should_retry(state: GenState) -> str:
    """校验失败且未超重试上限 → 回到 lore 节点重填."""
    if state.get("done"):
        return "done"
    if state["retry_count"] < state["max_retries"]:
        return "retry"
    return "done"


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

_LLM: StudioLLM | None = None


def _get_llm(state: GenState) -> StudioLLM:
    """获取/创建 LLM 客户端单例."""
    global _LLM
    if _LLM is None:
        _LLM = StudioLLM()
    return _LLM


def _write_yaml_entities(target_dir: Path, content: str, prefix: str) -> None:
    """将 LLM 输出的 YAML 序列块写入对应文件."""
    # LLM 输出是一个 YAML 序列（多项以 --- 或注释分隔）
    # 每项以 "# <prefix>_N" 注释开头
    items = _split_yaml_items(content, prefix)
    for i, item in enumerate(items, 1):
        # 被拆分的项可能包含多项（以 YAML 的 --- 分隔或注释分隔）
        filepath = target_dir / f"{prefix}_{i}.yaml"
        filepath.write_text(item, encoding="utf-8")


def _split_yaml_items(content: str, prefix: str) -> list[str]:
    """按 # <prefix>_N 注释分割 LLM 输出的 YAML 块."""
    import re

    pattern = rf"^#\s*{prefix}_\d+"
    lines = content.split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if re.match(pattern, line.strip()):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)

    return ["\n".join(b).strip() for b in blocks if b]


# ═══════════════════════════════════════════════════════════════
# Build Graph
# ═══════════════════════════════════════════════════════════════


def build_graph() -> StateGraph:
    """构建 LangGraph 生成管线."""
    graph = StateGraph(GenState)

    # Nodes
    graph.add_node("skeleton", node_skeleton)
    graph.add_node("lore", node_generate_lore)
    graph.add_node("pcs", node_generate_pcs)
    graph.add_node("npcs", node_generate_npcs)
    graph.add_node("items", node_generate_items)
    graph.add_node("scenes", node_generate_scenes)
    graph.add_node("validate", node_validate)
    graph.add_node("retry", node_retry)

    # Edges
    graph.set_entry_point("skeleton")
    graph.add_edge("skeleton", "lore")
    graph.add_edge("lore", "pcs")
    graph.add_edge("pcs", "npcs")
    graph.add_edge("npcs", "items")
    graph.add_edge("items", "scenes")
    graph.add_edge("scenes", "validate")

    # Conditional: validate → done or retry
    graph.add_conditional_edges(
        "validate",
        should_retry,
        {"retry": "retry", "done": END},
    )
    graph.add_edge("retry", "lore")  # 重试：回到 lore 重新生成全部

    return graph


async def generate_world_pack(params: GenerateParams) -> Path:
    """LangGraph 管线入口：生成 + AI 填充 + 校验."""
    graph = build_graph().compile()
    # 使用 test_run=True 来获取返回值
    state = await graph.ainvoke(
        {"params": params},
        config={"recursion_limit": 50},
    )
    return state["output_dir"]
