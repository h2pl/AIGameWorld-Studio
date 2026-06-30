"""世界生成子图 / World Pack Generation Subgraph — 纯编排，无业务逻辑"""
# src/graph/subgraphs/world_pack.py
# src/graph/subgraphs/world_pack.py
# src/graph/subgraphs/world_pack.py

from typing import TypedDict

from langgraph.graph import END, StateGraph

from src.pipeline.world_pack.nodes import (
    node_generate_meta,
    node_generate_story_setup,
    node_llm_fill,
    node_skeleton,
    node_validate,
)
from src.pipeline.world_pack.params import GenerateParams


class WorldPackState(TypedDict):
    params: GenerateParams
    output_dir: str
    world_name: str
    theme: str
    retry_count: int
    max_retries: int
    errors: list[str]
    done: bool


def _node_skeleton(state: WorldPackState) -> WorldPackState:
    from pathlib import Path

    out = node_skeleton(Path(state["output_dir"]), state["params"])
    state["output_dir"] = str(out)
    state["world_name"] = state["params"].world_name
    state["theme"] = state["params"].theme or state["params"].world_name
    state["retry_count"] = 0
    state["max_retries"] = state["params"].max_retries
    state["done"] = False
    return state


async def _node_meta(state: WorldPackState) -> WorldPackState:
    from pathlib import Path

    await node_generate_meta(Path(state["output_dir"]), state["theme"])
    return state


async def _node_story(state: WorldPackState) -> WorldPackState:
    from pathlib import Path

    await node_generate_story_setup(Path(state["output_dir"]), state["world_name"], state["theme"])
    return state


async def _node_lore(state: WorldPackState) -> WorldPackState:
    from pathlib import Path

    p = state["params"]
    await node_llm_fill(
        Path(state["output_dir"]), "lore", "lore", "lore", p.num_lore, state["world_name"], state["theme"]
    )
    return state


async def _node_pcs(state: WorldPackState) -> WorldPackState:
    from pathlib import Path

    p = state["params"]
    await node_llm_fill(
        Path(state["output_dir"]),
        "pc",
        "player_characters",
        "player_character",
        p.num_pcs,
        state["world_name"],
        state["theme"],
    )
    return state


async def _node_npcs(state: WorldPackState) -> WorldPackState:
    from pathlib import Path

    p = state["params"]
    await node_llm_fill(
        Path(state["output_dir"]), "npc", "actors", "actor", p.num_actors, state["world_name"], state["theme"]
    )
    return state


async def _node_items(state: WorldPackState) -> WorldPackState:
    from pathlib import Path

    p = state["params"]
    await node_llm_fill(
        Path(state["output_dir"]), "item", "items", "item", p.num_items, state["world_name"], state["theme"]
    )
    return state


async def _node_scenes(state: WorldPackState) -> WorldPackState:
    from pathlib import Path

    p = state["params"]
    await node_llm_fill(
        Path(state["output_dir"]), "scene", "scenes", "scene", p.num_scenes, state["world_name"], state["theme"]
    )
    return state


async def _node_scene_objects(state: WorldPackState) -> WorldPackState:
    from pathlib import Path

    p = state["params"]
    await node_llm_fill(
        Path(state["output_dir"]),
        "scene_object",
        "scene_objects",
        "scene_object",
        p.num_scene_objects,
        state["world_name"],
        state["theme"],
    )
    return state


def _node_validate(state: WorldPackState) -> WorldPackState:
    from pathlib import Path

    result = node_validate(Path(state["output_dir"]))
    if result.is_valid:
        state["done"] = True
        state["errors"] = []
    else:
        state["errors"] = result.errors
    return state


def _node_retry(state: WorldPackState) -> WorldPackState:
    state["retry_count"] += 1
    return state


def _should_retry(state: WorldPackState) -> str:
    if state.get("done"):
        return "done"
    if state["retry_count"] < state["max_retries"]:
        return "retry"
    return "done"


def build_world_pack_subgraph() -> StateGraph:
    graph = StateGraph(WorldPackState)

    graph.add_node("skeleton", _node_skeleton)
    graph.add_node("meta", _node_meta)
    graph.add_node("story_setup", _node_story)
    graph.add_node("lore", _node_lore)
    graph.add_node("pcs", _node_pcs)
    graph.add_node("npcs", _node_npcs)
    graph.add_node("items", _node_items)
    graph.add_node("scenes", _node_scenes)
    graph.add_node("scene_objects", _node_scene_objects)
    graph.add_node("validate", _node_validate)
    graph.add_node("retry", _node_retry)

    graph.set_entry_point("skeleton")
    for a, b in [
        ("skeleton", "meta"),
        ("meta", "story_setup"),
        ("story_setup", "lore"),
        ("lore", "pcs"),
        ("pcs", "npcs"),
        ("npcs", "items"),
        ("items", "scenes"),
        ("scenes", "scene_objects"),
        ("scene_objects", "validate"),
    ]:
        graph.add_edge(a, b)

    graph.add_conditional_edges("validate", _should_retry, {"retry": "retry", "done": END})
    graph.add_edge("retry", "lore")

    return graph


world_pack_subgraph = build_world_pack_subgraph().compile()

# ============================================================
# Subgraph wiring: 纯编排，无业务逻辑 / Pure wiring, no logic
# skeleton → meta → story → lore → pcs → npcs → items → scenes → scenes_objs → validate → retry
# ============================================================
# retry → lore
# validate → retry or done
# entry point
# compile subgraph
