# Generator 单元测试 / Unit tests for Generator

from pathlib import Path

import pytest
import yaml

from src.generator.generate import GeneratorParams, generate_manual, generate_preset


def test_generate_forgotten_realm(tmp_path: Path):
    """内置模板生成完整 / Preset generates complete template."""
    out = generate_preset("forgotten_realm", tmp_path)
    assert (out / "meta.yaml").exists()
    assert (out / "lore" / "forest_history.yaml").exists()
    assert (out / "lore" / "dwarf_culture.yaml").exists()
    assert (out / "scenes" / "tavern.yaml").exists()
    assert (out / "scenes" / "forest.yaml").exists()
    assert (out / "player_characters" / "warrior.yaml").exists()
    assert (out / "player_characters" / "mage.yaml").exists()
    assert (out / "actors" / "blacksmith.yaml").exists()
    assert (out / "actors" / "innkeeper.yaml").exists()
    assert (out / "items" / "longsword.yaml").exists()
    assert (out / "items" / "healing_potion.yaml").exists()
    assert (out / "scene_objects" / "tavern_chest.yaml").exists()
    assert (out / "scene_objects" / "forest_trap.yaml").exists()
    assert (out / "story_setup.yaml").exists()


def test_generate_output_valid_yaml(tmp_path: Path):
    """输出是合法 YAML / Output is valid YAML."""
    out = generate_preset("forgotten_realm", tmp_path)
    for yf in out.rglob("*.yaml"):
        data = yaml.safe_load(yf.read_text(encoding="utf-8"))
        assert data is not None, f"Invalid YAML: {yf}"


def test_unknown_preset_raises(tmp_path: Path):
    """未知 preset 抛出 ValueError / Unknown preset raises ValueError."""
    with pytest.raises(ValueError, match="Unknown preset"):
        generate_preset("non_existent", tmp_path)


def test_manual_mode_generates_dirs(tmp_path: Path):
    """手动模式生成目录结构 / Manual mode generates directories."""
    params = GeneratorParams(world_name="test_world", output_dir=tmp_path)
    out = generate_manual(params)
    assert (out / "meta.yaml").exists()
    assert (out / "lore").is_dir()
    assert (out / "scenes").is_dir()
    assert (out / "story_setup.yaml").exists()


def test_output_overwrite_ok(tmp_path: Path):
    """覆盖写入正常 / Overwrite works."""
    preset_dir = tmp_path / "forgotten_realm"
    preset_dir.mkdir()
    out = generate_preset("forgotten_realm", tmp_path)
    assert (out / "meta.yaml").exists()
