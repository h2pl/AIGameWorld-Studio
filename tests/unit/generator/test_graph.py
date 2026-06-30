# Graph 结构单元测试 / Unit tests for graph structure

from pathlib import Path

from src.generator.graph import GenState, build_graph, node_retry, node_skeleton, node_validate, should_retry
from src.generator.params import GenerateParams


def _state(params: GenerateParams | None = None) -> GenState:
    if params is None:
        params = GenerateParams()
    return GenState(
        params=params,
        output_dir=Path(),
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
    s = node_skeleton(s)
    assert s["output_dir"].exists()
    assert (s["output_dir"] / "meta.yaml").exists()
    assert s["world_name"] == "test"
    assert s["retry_count"] == 0
    assert s["done"] is False


def test_node_skeleton_uses_theme(tmp_path: Path):
    params = GenerateParams(world_name="test", theme="仙侠", output_dir=tmp_path)
    s = _state(params)
    s = node_skeleton(s)
    assert s["theme"] == "仙侠"


# === node_validate ===


def test_node_validate_passed(tmp_path: Path):
    params = GenerateParams(output_dir=tmp_path)
    s = _state(params)
    s = node_skeleton(s)
    s = node_validate(s)
    assert s["done"] is True
    assert s["errors"] == []


def test_node_validate_failed(tmp_path: Path):
    s = _state(GenerateParams(output_dir=tmp_path))
    s = node_validate(s)
    assert "errors" in s


# === node_retry ===


def test_node_retry_increments():
    s = _state()
    assert s["retry_count"] == 0
    s = node_retry(s)
    assert s["retry_count"] == 1
    s = node_retry(s)
    assert s["retry_count"] == 2


# === should_retry ===


def test_should_retry_done():
    s = _state()
    s["done"] = True
    assert should_retry(s) == "done"


def test_should_retry_under_limit():
    s = _state()
    s["retry_count"] = 1
    assert should_retry(s) == "retry"


def test_should_retry_at_limit():
    s = _state()
    s["retry_count"] = 3
    assert should_retry(s) == "done"


def test_should_retry_over_limit():
    s = _state()
    s["retry_count"] = 5
    assert should_retry(s) == "done"


# === build_graph ===


def test_build_graph_returns_valid_graph():
    graph = build_graph()
    compiled = graph.compile()
    assert compiled is not None


def test_graph_nodes_count():
    graph = build_graph()
    nodes = graph.compile().get_graph().nodes
    assert len(nodes) >= 9
    assert "skeleton" in nodes
    assert "validate" in nodes
    assert "retry" in nodes


def test_graph_skeleton_to_validate_full(tmp_path: Path):
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
    s = node_skeleton(s)
    s = node_validate(s)
    assert s["done"] is True, s.get("errors", [])
