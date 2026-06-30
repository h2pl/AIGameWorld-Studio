"""LangGraph 生成管线 / LangGraph Generation Pipeline.

StateGraph: generate_world_pack
  skeleton → lore → pcs → npcs → items → scenes → validate → retry

技术栈对齐 AIGameWorld：langgraph + langchain-openai + Jinja2 模板。
Prompt 模板位于 prompts/ 目录下，通过 StudioLLM.generate() 渲染。
"""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph

from src.generator.generate import GenerateParams
from src.generator.generate import generate as gen_skeleton
from src.llm.client import StudioLLM
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
    """生成骨架 YAML（结构保证合法）/ Generate skeleton YAML (structurally valid)."""
    params = state["params"]
    out = gen_skeleton(params)
    state["output_dir"] = out
    state["world_name"] = params.world_name
    state["theme"] = getattr(params, "theme", params.world_name)
    state["retry_count"] = 0
    state["max_retries"] = getattr(params, "max_retries", 3)
    state["done"] = False
    return state


async def _llm_fill(state: GenState, template_name: str, subdir: str, file_prefix: str) -> GenState:
    """通用 LLM 填充节点 / Generic LLM fill node.

    用 Jinja2 模板渲染 prompt → LLM 生成 YAML 内容 → 覆盖骨架文件.
    """
    llm = _get_llm()
    dir_ = state["output_dir"]
    target_dir = dir_ / subdir

    existing = sorted(target_dir.glob(f"{file_prefix}_*.yaml")) if target_dir.exists() else []
    if not existing:
        return state

    result = await llm.generate(
        template_name,
        world_name=state["world_name"],
        count=len(existing),
        theme=state["theme"],
    )
    _write_yaml_entities(target_dir, result, file_prefix)
    return state


async def node_generate_lore(state: GenState) -> GenState:
    """LLM 填充 lore 文案 / Fill lore with LLM."""
    return await _llm_fill(state, "lore", "lore", "lore")


async def node_generate_pcs(state: GenState) -> GenState:
    """LLM 填充 PC 文案 / Fill player characters with LLM."""
    return await _llm_fill(state, "pc", "player_characters", "player_character")


async def node_generate_npcs(state: GenState) -> GenState:
    """LLM 填充 NPC 文案 / Fill NPCs with LLM."""
    return await _llm_fill(state, "npc", "actors", "actor")


async def node_generate_items(state: GenState) -> GenState:
    """LLM 填充物品文案 / Fill items with LLM."""
    return await _llm_fill(state, "item", "items", "item")


async def node_generate_scenes(state: GenState) -> GenState:
    """LLM 填充场景描述 / Fill scenes with LLM."""
    return await _llm_fill(state, "scene", "scenes", "scene")


def node_validate(state: GenState) -> GenState:
    """校验生成结果 / Validate generated world pack."""
    result = validate_world_pack(state["output_dir"])
    if result.is_valid:
        state["done"] = True
        state["errors"] = []
    else:
        state["errors"] = result.errors
    return state


def node_retry(state: GenState) -> GenState:
    """重试计数 / Increment retry counter."""
    state["retry_count"] += 1
    return state


# ═══════════════════════════════════════════════════════════════
# Edges
# ═══════════════════════════════════════════════════════════════


def should_retry(state: GenState) -> str:
    """校验失败且未超重试上限 → 回到 lore 节点重填 / Retry if validation failed and under limit."""
    if state.get("done"):
        return "done"
    if state["retry_count"] < state["max_retries"]:
        return "retry"
    return "done"


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

_LLM: StudioLLM | None = None


def _get_llm() -> StudioLLM:
    """获取/创建 LLM 客户端单例 / Get or create LLM client singleton."""
    global _LLM
    if _LLM is None:
        _LLM = StudioLLM()
    return _LLM


def _write_yaml_entities(target_dir: Path, content: str, prefix: str) -> None:
    """将 LLM 输出的 YAML 序列块写入对应文件 / Write LLM output YAML blocks to files.

    LLM 输出是 YAML 序列，每项以 "# <prefix>_N" 注释开头.
    """
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

    items = ["\n".join(b).strip() for b in blocks if b]
    for i, item in enumerate(items, 1):
        filepath = target_dir / f"{prefix}_{i}.yaml"
        filepath.write_text(item, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# Build Graph
# ═══════════════════════════════════════════════════════════════


def build_graph() -> StateGraph:
    """构建 LangGraph 生成管线 / Build LangGraph generation pipeline."""
    graph = StateGraph(GenState)

    # Nodes / 节点注册
    graph.add_node("skeleton", node_skeleton)
    graph.add_node("lore", node_generate_lore)
    graph.add_node("pcs", node_generate_pcs)
    graph.add_node("npcs", node_generate_npcs)
    graph.add_node("items", node_generate_items)
    graph.add_node("scenes", node_generate_scenes)
    graph.add_node("validate", node_validate)
    graph.add_node("retry", node_retry)

    # Edges / 边连接
    graph.set_entry_point("skeleton")
    graph.add_edge("skeleton", "lore")
    graph.add_edge("lore", "pcs")
    graph.add_edge("pcs", "npcs")
    graph.add_edge("npcs", "items")
    graph.add_edge("items", "scenes")
    graph.add_edge("scenes", "validate")

    # Conditional: validate → done or retry / 条件分支
    graph.add_conditional_edges(
        "validate",
        should_retry,
        {"retry": "retry", "done": END},
    )
    graph.add_edge("retry", "lore")  # 重试：回到 lore 重新生成全部

    return graph


async def generate_world_pack(params: GenerateParams) -> Path:
    """LangGraph 管线入口：生成骨架 + AI 填充 + 校验 / Full pipeline: skeleton → AI fill → validate."""
    graph = build_graph().compile()
    state = await graph.ainvoke(
        {"params": params},
        config={"recursion_limit": 50},
    )
    return state["output_dir"]
