# Graph 结构单元测试 / Unit tests for graph structure

from pathlib import Path

from src.graph.graph import build_graph
from src.graph.subgraphs.world_pack import (
    WorldPackState,
    _node_retry,
    _node_skeleton,
    _node_validate,
    _should_retry,
    build_world_pack_subgraph,
)
from src.pipeline.world_pack.params import GenerateParams


def _state(params: GenerateParams | None = None) -> WorldPackState:
    if params is None:
        params = GenerateParams()
    return WorldPackState(
        params=params,
        output_dir="",
        world_name=params.world_name,
        theme=params.theme or params.world_name,
        retry_count=0,
        max_retries=params.max_retries,
        errors=[],
        done=False,
    )


# === node_skeleton ===


def test_node_skeleton_creates_output(tmp_path: Path):
    params = GenerateParams(
        world_name="test",
        output_dir=tmp_path,
        num_pcs=1,
        num_actors=1,
        num_scenes=1,
        num_items=1,
        num_lore=1,
        num_scene_objects=1,
    )
    s = _state(params)
    s = _node_skeleton(s)
    out = Path(s["output_dir"])
    assert out.exists()
    assert (out / "meta.yaml").exists()
    assert s["world_name"] == "test"
    assert s["retry_count"] == 0
    assert s["done"] is False


def test_node_skeleton_uses_theme(tmp_path: Path):
    params = GenerateParams(world_name="test", theme="仙侠", output_dir=tmp_path)
    s = _state(params)
    s = _node_skeleton(s)
    assert s["theme"] == "仙侠"


# === node_validate ===


def test_node_validate_passed(tmp_path: Path):
    params = GenerateParams(output_dir=tmp_path)
    s = _state(params)
    s = _node_skeleton(s)
    s = _node_validate(s)
    assert s["done"] is True
    assert s["errors"] == []


def test_node_validate_failed(tmp_path: Path):
    s = _state(GenerateParams(output_dir=tmp_path))
    s["output_dir"] = str(tmp_path / "nonexistent")
    s = _node_validate(s)
    assert "errors" in s


# === node_retry ===


def test_node_retry_increments():
    s = _state()
    assert s["retry_count"] == 0
    s = _node_retry(s)
    assert s["retry_count"] == 1
    s = _node_retry(s)
    assert s["retry_count"] == 2


# === should_retry ===


def test_should_retry_done():
    s = _state()
    s["done"] = True
    assert _should_retry(s) == "done"


def test_should_retry_under_limit():
    s = _state()
    s["retry_count"] = 1
    assert _should_retry(s) == "retry"


def test_should_retry_at_limit():
    s = _state()
    s["retry_count"] = 3
    assert _should_retry(s) == "done"


def test_should_retry_over_limit():
    s = _state()
    s["retry_count"] = 5
    assert _should_retry(s) == "done"


# === build_graph (main orchestrator) ===


def test_build_graph_returns_valid_graph():
    graph = build_graph()
    compiled = graph.compile()
    assert compiled is not None


def test_main_graph_has_subgraphs():
    graph = build_graph()
    nodes = graph.compile().get_graph().nodes
    assert len(nodes) >= 3  # world_pack + assets + __start__ + __end__
    assert "world_pack" in nodes
    assert "assets" in nodes


def test_world_pack_subgraph_nodes():
    graph = build_world_pack_subgraph()
    nodes = graph.compile().get_graph().nodes
    assert len(nodes) >= 11
    assert "skeleton" in nodes
    assert "validate" in nodes
    assert "retry" in nodes
