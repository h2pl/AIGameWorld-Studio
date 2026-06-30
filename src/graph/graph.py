"""生成器主图 / Generator Main Graph — 串接子图

world_pack_subgraph → [assets/recolor | assets/ai | skip] → END
"""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph

from src.graph.subgraphs.assets import assets_subgraph
from src.graph.subgraphs.world_pack import world_pack_subgraph
from src.pipeline.world_pack.params import GenerateParams


class GenState(TypedDict):
    params: GenerateParams
    output_dir: str
    world_name: str
    theme: str
    assets_method: str


def _should_generate_assets(state: GenState) -> str:
    """判断是否生成素材 / Decide whether to generate assets."""
    method = state.get("assets_method", "recolor")
    if method == "skip":
        return "skip"
    return method


def build_graph() -> StateGraph:
    """构建主图：world_pack → [assets|skip] / Build main graph."""
    graph = StateGraph(GenState)

    # 子图注册 / Subgraph registration
    graph.add_node("world_pack", world_pack_subgraph)
    graph.add_node("assets", assets_subgraph)

    # 入口 / Entry point
    graph.set_entry_point("world_pack")

    # 条件路由 / Conditional routing
    graph.add_conditional_edges(
        "world_pack",
        _should_generate_assets,
        {"recolor": "assets", "ai": "assets", "skip": END},
    )
    graph.add_edge("assets", END)

    return graph


async def generate_world_pack(params: GenerateParams) -> Path:
    """管线入口 / Entry point."""
    # 编译并运行 / Compile and invoke
    graph = build_graph().compile()
    state = await graph.ainvoke(
        {"params": params, "assets_method": params.assets_method},
        config={"recursion_limit": 200},
    )
    return Path(state["output_dir"])
