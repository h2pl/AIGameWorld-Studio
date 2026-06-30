"""LangGraph 生成管线 / LangGraph Generation Pipeline.

StateGraph: generate_world_pack
  skeleton(template-based) → lore → pcs → npcs → items → scenes → validate → retry

两层生成策略 / Two-layer generation:
  skeleton: 模板生成合法 YAML 骨架（结构字段）
  LLM fill: 覆盖创意字段（文案/属性）
"""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph

from src.generator.params import GenerateParams
from src.generator.skeleton import generate_skeleton
from src.llm.client import llm_generate
from src.llm.utils import write_yaml_entities
from src.validator.validate import validate_template as validate_world_pack

# ═══════════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════════


class GenState(TypedDict):
    params: GenerateParams
    output_dir: Path
    world_name: str
    theme: str
    retry_count: int
    max_retries: int
    errors: list[str]
    done: bool


# ═══════════════════════════════════════════════════════════════
# Skeleton node
# ═══════════════════════════════════════════════════════════════


def node_skeleton(state: GenState) -> GenState:
    """模板生成合法 YAML 骨架 / Generate valid YAML skeleton from templates."""
    params = state["params"]
    out = Path(params.output_dir) / params.pack_id
    out.mkdir(parents=True, exist_ok=True)
    generate_skeleton(out, params)

    state["output_dir"] = out
    state["world_name"] = params.world_name
    state["theme"] = params.theme or params.world_name
    state["retry_count"] = 0
    state["max_retries"] = params.max_retries
    state["done"] = False
    return state


# ═══════════════════════════════════════════════════════════════
# LLM fill nodes
# ═══════════════════════════════════════════════════════════════


async def _llm_fill(state: GenState, template_name: str, subdir: str | None, file_prefix: str) -> GenState:
    """通用 LLM 填充节点 / Generic LLM fill: render Jinja2 prompt → LLM → overwrite YAML."""
    params = state["params"]
    count = params.count_of(file_prefix)
    if count == 0:
        return state

    target_dir = state["output_dir"] / subdir if subdir else state["output_dir"]

    result = await llm_generate(
        template_name,
        world_name=state["world_name"],
        count=count,
        theme=state["theme"],
    )
    n = write_yaml_entities(target_dir, result, file_prefix)

    # 截断多余文件 / Truncate extra files beyond count
    for i in range(count + 1, 20):
        extra = target_dir / f"{file_prefix}_{i}.yaml"
        if extra.exists():
            extra.unlink()

    print(f"  [LLM] {file_prefix}: wrote {min(n, count)}/{count} files (output {len(result)} chars)")
    return state


async def node_generate_lore(state: GenState) -> GenState:
    return await _llm_fill(state, "lore", "lore", "lore")


async def node_generate_pcs(state: GenState) -> GenState:
    return await _llm_fill(state, "pc", "player_characters", "player_character")


async def node_generate_npcs(state: GenState) -> GenState:
    return await _llm_fill(state, "npc", "actors", "actor")


async def node_generate_items(state: GenState) -> GenState:
    return await _llm_fill(state, "item", "items", "item")


async def node_generate_scenes(state: GenState) -> GenState:
    return await _llm_fill(state, "scene", "scenes", "scene")


async def node_generate_scene_objects(state: GenState) -> GenState:
    return await _llm_fill(state, "scene_object", "scene_objects", "scene_object")


async def node_generate_meta(state: GenState) -> GenState:
    """LLM 填充 meta 描述 / Fill meta description with LLM."""
    import yaml
    from langchain_core.messages import HumanMessage, SystemMessage

    from src.llm.client import get_model

    meta_path = state["output_dir"] / "meta.yaml"
    data = yaml.safe_load(meta_path.read_text("utf-8")) or {}
    prompt = (
        f"为「{data['name']}」世界的 meta.yaml 填写 description 字段。\n"
        f"主题：{state['theme']}\n"
        f"要求：一句话概括这个世界，有吸引力。只输出纯文本，不要 YAML 格式。"
    )
    model = get_model()
    sys_msg = "你是世界创作助手，输出纯文本。"
    result = await model._agenerate([SystemMessage(content=sys_msg), HumanMessage(content=prompt)])
    desc = str(result.generations[0].message.content).strip().strip('"').strip("'")
    data["description"] = desc
    meta_path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return state


async def node_generate_story_setup(state: GenState) -> GenState:
    """LLM 填充故事框架 / Fill story setup with LLM."""
    import yaml
    from langchain_core.messages import HumanMessage, SystemMessage

    from src.llm.client import get_model

    sp_path = state["output_dir"] / "story_setup.yaml"
    data = yaml.safe_load(sp_path.read_text("utf-8")) or {}
    prompt = (
        f"为「{state['world_name']}」世界编写初始剧情框架。\n"
        f"主题：{state['theme']}\n"
        f"请输出严格的 YAML 格式：\n"
        f"arcs:\n"
        f"  - type: main\n"
        f'    title: "主线标题"\n'
        f"    stage: hook\n"
        f"    main_cast: []\n"
        f"    branching_points: []"
    )
    model = get_model()
    sys_msg = "你是世界创作助手，输出严格的 YAML 格式。"
    result = await model._agenerate([SystemMessage(content=sys_msg), HumanMessage(content=prompt)])
    content = str(result.generations[0].message.content).strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3].strip()
    new_data = yaml.safe_load(content)
    if new_data:
        data.update(new_data)
    sp_path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return state


# ═══════════════════════════════════════════════════════════════
# Validate & Retry
# ═══════════════════════════════════════════════════════════════


def node_validate(state: GenState) -> GenState:
    result = validate_world_pack(state["output_dir"])
    if result.is_valid:
        state["done"] = True
        state["errors"] = []
    else:
        state["errors"] = result.errors
    return state


def node_retry(state: GenState) -> GenState:
    state["retry_count"] += 1
    return state


def should_retry(state: GenState) -> str:
    if state.get("done"):
        return "done"
    if state["retry_count"] < state["max_retries"]:
        return "retry"
    return "done"


# ═══════════════════════════════════════════════════════════════
# Graph Builder
# ═══════════════════════════════════════════════════════════════


def build_graph() -> StateGraph:
    graph = StateGraph(GenState)

    graph.add_node("skeleton", node_skeleton)
    graph.add_node("meta", node_generate_meta)
    graph.add_node("story_setup", node_generate_story_setup)
    graph.add_node("lore", node_generate_lore)
    graph.add_node("pcs", node_generate_pcs)
    graph.add_node("npcs", node_generate_npcs)
    graph.add_node("items", node_generate_items)
    graph.add_node("scenes", node_generate_scenes)
    graph.add_node("scene_objects", node_generate_scene_objects)
    graph.add_node("validate", node_validate)
    graph.add_node("retry", node_retry)

    graph.set_entry_point("skeleton")
    graph.add_edge("skeleton", "meta")
    graph.add_edge("meta", "story_setup")
    graph.add_edge("story_setup", "lore")
    graph.add_edge("lore", "pcs")
    graph.add_edge("pcs", "npcs")
    graph.add_edge("npcs", "items")
    graph.add_edge("items", "scenes")
    graph.add_edge("scenes", "scene_objects")
    graph.add_edge("scene_objects", "validate")

    graph.add_conditional_edges("validate", should_retry, {"retry": "retry", "done": END})
    graph.add_edge("retry", "lore")

    return graph


# ═══════════════════════════════════════════════════════════════
# Entry
# ═══════════════════════════════════════════════════════════════


async def generate_world_pack(params: GenerateParams) -> Path:
    """管线入口 / Full pipeline: template skeleton → LLM fill → validate."""
    graph = build_graph().compile()
    state = await graph.ainvoke({"params": params}, config={"recursion_limit": 50})
    return state["output_dir"]
