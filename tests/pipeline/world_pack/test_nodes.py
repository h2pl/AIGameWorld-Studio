# Nodes 测试 / Node function tests

from pathlib import Path

from src.services.generator.filler import node_skeleton, node_validate
from src.domain.params import GenerateParams


def test_node_skeleton_creates_files(tmp_path: Path):
    """node_skeleton 创建目录和文件 / Creates dirs and files."""
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
    out = node_skeleton(tmp_path, params)

    assert out.exists()
    assert (out / "meta.yaml").exists()
    assert (out / "player_characters").exists()
    assert (out / "actors").exists()


def test_node_skeleton_uses_params(tmp_path: Path):
    """node_skeleton 使用参数值 / Uses parameter values."""
    params = GenerateParams(
        world_name="仙剑世界",
        pack_id="sword_world",
        output_dir=tmp_path,
    )
    out = node_skeleton(tmp_path, params)
    assert out.name == "sword_world"


def test_node_validate_passed(tmp_path: Path):
    """node_validate 校验通过 / Validation passed."""
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
    out = node_skeleton(tmp_path, params)
    result = node_validate(out)
    assert result.is_valid
    assert result.failed == 0


def test_node_validate_failed(tmp_path: Path):
    """node_validate 校验失败 / Validation failed."""
    # 空目录 = 没有有效文件 / Empty dir = no valid files
    empty = tmp_path / "empty"
    empty.mkdir()
    result = node_validate(empty)
    assert result.passed == 0  # 空目录不报错，但也没有通过的文件
