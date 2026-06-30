"""素材生成子图 / Assets Generation Subgraph — 纯编排，无业务逻辑"""

# 编排: sprites → tileset → layout
from typing import TypedDict

from langgraph.graph import END, StateGraph

from src.pipeline.world_pack.params import GenerateParams


class AssetsState(TypedDict):
    params: GenerateParams
    output_dir: str
    assets_method: str


def _node_sprites(state: AssetsState) -> AssetsState:
    from pathlib import Path

    from src.pipeline.assets.character import generate_character_sprites

    chars = _read_yaml_dir(Path(state["output_dir"]) / "player_characters") + _read_yaml_dir(
        Path(state["output_dir"]) / "actors"
    )
    if state["assets_method"] == "ai":
        # 角色精灵 / Character sprites
        from src.pipeline.ai_assets.character import generate_ai_sprites

        sprites_dir = Path(state["output_dir"]) / "sprites"
        sprites_dir.mkdir(exist_ok=True)
        generate_ai_sprites(chars, sprites_dir, state["params"])
    elif chars:
        generate_character_sprites(chars, Path(state["output_dir"]), method="recolor")
    return state


def _node_tileset(state: AssetsState) -> AssetsState:
    if state["assets_method"] == "ai":
        return state
    from pathlib import Path

    from src.pipeline.assets.tiles import generate_tileset

    # 瓦片集 / Tileset
    scenes = _read_yaml_dir(Path(state["output_dir"]) / "scenes")
    if scenes:
        generate_tileset(scenes, Path(state["output_dir"]))
    return state


def _node_layout(state: AssetsState) -> AssetsState:
    if state["assets_method"] == "ai":
        return state
    from pathlib import Path

    from src.pipeline.assets.layout import generate_layout

    # 布局 / Layout
    scenes = _read_yaml_dir(Path(state["output_dir"]) / "scenes")
    if scenes:
        generate_layout(scenes, Path(state["output_dir"]))
    return state


def _read_yaml_dir(dir_path) -> list[dict]:
    import yaml

    if not dir_path.exists():
        return []
    result = []
    for f in dir_path.glob("*.yaml"):
        # 读取 YAML / Read YAML
        d = yaml.safe_load(f.read_text("utf-8"))
        if isinstance(d, dict):
            result.append(d)
    return result


def build_assets_subgraph() -> StateGraph:
    graph = StateGraph(AssetsState)

    graph.add_node("sprites", _node_sprites)
    graph.add_node("tileset", _node_tileset)
    graph.add_node("layout", _node_layout)

    graph.set_entry_point("sprites")
    graph.add_edge("sprites", "tileset")
    graph.add_edge("tileset", "layout")
    graph.add_edge("layout", END)

    return graph


assets_subgraph = build_assets_subgraph().compile()
