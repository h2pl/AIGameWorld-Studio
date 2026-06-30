# 全链路集成测试 / Full pipeline integration test

import subprocess
import sys
from pathlib import Path

import yaml

from src.generator.params import GenerateParams
from src.generator.skeleton import generate_skeleton
from src.validator.validate import validate_template


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
    )


# === skeleton → validate (CLI) ===


def test_generate_skeleton_then_validate_cli(tmp_path: Path):
    """骨架生成 → CLI 校验全链路 / Skeleton → CLI validate full flow."""
    p = GenerateParams(
        world_name="test_world",
        output_dir=tmp_path,
        num_pcs=2,
        num_actors=2,
        num_scenes=2,
        num_items=3,
        num_lore=2,
        num_scene_objects=1,
    )
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    r = _run("validate", str(out))
    assert r.returncode == 0, r.stderr
    assert "0 failed" in r.stdout


# === validate on hand-built world ===


def test_validate_on_hand_built_world(tmp_path: Path):
    """手工构建合法 world-pack → 校验通过 / Hand-built valid world-pack passes validation."""
    world = tmp_path / "test_world"
    world.mkdir()

    (world / "meta.yaml").write_text(
        yaml.dump({"id": "test", "name": "Test", "rule_set": "d20", "starting_scene": "s1"}, allow_unicode=True),
        encoding="utf-8",
    )

    (world / "story_setup.yaml").write_text(
        yaml.dump({"arcs": [{"title": "A"}]}, allow_unicode=True),
        encoding="utf-8",
    )

    (world / "scenes").mkdir()
    (world / "scenes/s1.yaml").write_text(
        yaml.dump({"id": "s1", "name": "Start", "type": "indoor", "description": "A room"}, allow_unicode=True),
        encoding="utf-8",
    )

    for d in ["player_characters", "actors", "items", "lore", "scene_objects"]:
        (world / d).mkdir()

    r = _run("validate", str(world))
    assert r.returncode == 0, r.stderr
    assert "0 failed" in r.stdout


# === error cases ===


def test_validate_missing_world():
    """不存在路径返回 1 / Missing path returns 1."""
    r = _run("validate", "no_such_world")
    assert r.returncode == 1


# === programmatic full flow (no LLM) ===


def test_skeleton_validate_programmatic(tmp_path: Path):
    """骨架生成 → 校验 程序化全链路 / Skeleton → validate programmatic full flow."""
    p = GenerateParams(
        world_name="integ_test",
        output_dir=tmp_path,
        num_pcs=3,
        num_actors=3,
        num_scenes=3,
        num_items=4,
        num_lore=2,
        num_scene_objects=2,
    )
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    r = validate_template(out)
    assert r.is_valid, r.errors
    assert r.passed >= 17, f"Expected >= 17 files, got {r.passed}"
    assert r.failed == 0

    # 验证具体文件数 / Verify file count
    all_yaml = list(out.rglob("*.yaml"))
    # meta + story_setup + pcs + actors + scenes + items + lore + scene_objects
    expected = 1 + 1 + 3 + 3 + 3 + 4 + 2 + 2
    assert len(all_yaml) == expected, f"Expected {expected} files, got {len(all_yaml)}"


# === zero entities edge case ===


def test_skeleton_zero_entities(tmp_path: Path):
    """全部实体数为 0 仅生成 meta + story_setup / All zero only generates meta + story_setup."""
    p = GenerateParams(
        world_name="minimal",
        output_dir=tmp_path,
        num_pcs=0,
        num_actors=0,
        num_scenes=0,
        num_items=0,
        num_lore=0,
        num_scene_objects=0,
    )
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    assert (out / "meta.yaml").exists()
    assert (out / "story_setup.yaml").exists()

    # 不应该有其他子目录 / No other subdirs should exist
    for sd in ["scenes", "player_characters", "actors", "items", "lore", "scene_objects"]:
        assert not (out / sd).exists(), f"{sd} should not exist"
