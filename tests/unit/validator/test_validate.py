# Validator 单元测试 / Unit tests for Validator

from pathlib import Path

import yaml

from src.validator.validate import ValidationResult, format_report, validate_template


def _write_yaml(path: Path, data: dict):
    """写入 YAML 文件 / Write YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


def test_valid_template_all_pass(tmp_path: Path):
    """合法模板全部通过 / Valid template passes all."""
    _write_yaml(
        tmp_path / "meta.yaml",
        {"id": "test", "name": "Test", "ruleset": "d20", "starting_scene": "s1"},
    )
    _write_yaml(
        tmp_path / "lore" / "history.yaml",
        {"id": "h1", "category": "history", "content": "Ancient..."},
    )
    _write_yaml(
        tmp_path / "scenes" / "tavern.yaml",
        {"id": "s1", "name": "Tavern", "type": "indoor", "description": "A tavern"},
    )
    _write_yaml(
        tmp_path / "player_characters" / "pc1.yaml",
        {
            "id": "warrior",
            "name": "Warrior",
            "role": "warrior",
            "race": "human",
            "personality": "Brave",
            "attributes": {"str": 16, "dex": 12, "con": 16, "int": 10, "wis": 12, "cha": 10},
        },
    )
    _write_yaml(
        tmp_path / "actors" / "smith.yaml",
        {
            "id": "smith",
            "name": "Smith",
            "role": "blacksmith",
            "race": "dwarf",
            "personality": "Quiet",
            "attributes": {"str": 16, "dex": 10, "con": 16, "int": 10, "wis": 12, "cha": 8},
        },
    )
    _write_yaml(
        tmp_path / "items" / "sword.yaml",
        {"id": "sword", "name": "Sword", "item_type": "weapon", "rarity": "common"},
    )
    _write_yaml(
        tmp_path / "scene_objects" / "chest.yaml",
        {"id": "chest1", "name": "Chest", "object_type": "chest", "scene_id": "s1"},
    )
    _write_yaml(
        tmp_path / "story_setup.yaml",
        {"arcs": [{"title": "Arc"}], "hooks": [{"description": "Hook"}]},
    )

    result = validate_template(tmp_path)
    assert result.is_valid
    assert result.failed == 0
    assert result.passed == 8


def test_missing_required_field(tmp_path: Path):
    """缺失必须字段被检出 / Missing required field detected."""
    _write_yaml(
        tmp_path / "scenes" / "tavern.yaml",
        {"name": "Tavern", "type": "indoor"},  # missing id, description
    )
    result = validate_template(tmp_path)
    assert not result.is_valid
    assert any("missing:" in e for e in result.errors)


def test_invalid_enum_value(tmp_path: Path):
    """非法枚举值被检出 / Invalid enum value detected."""
    _write_yaml(tmp_path / "scenes" / "tavern.yaml", {"id": "t", "name": "T", "type": "space", "description": "x"})
    result = validate_template(tmp_path)
    assert not result.is_valid
    assert any("invalid type" in e for e in result.errors)


def test_cross_ref_broken_scene(tmp_path: Path):
    """交叉引用断裂被检出 / Broken cross-reference detected."""
    _write_yaml(tmp_path / "meta.yaml", {"id": "test", "name": "Test", "ruleset": "d20", "starting_scene": "void"})
    result = validate_template(tmp_path)
    assert not result.is_valid
    assert any("starting_scene" in e for e in result.errors)


def test_empty_dirs_not_counted(tmp_path: Path):
    """空资源目录不计入统计 / Empty resource dirs not counted."""
    (tmp_path / "scenes").mkdir(exist_ok=True)  # 空目录 / empty dir
    result = validate_template(tmp_path)
    assert result.is_valid  # 0 文件 = 无错误


def test_format_report():
    """格式化输出正确 / Format report works."""
    result = ValidationResult(passed=3, failed=1, errors=["x.yaml ❌ missing: id"])
    report = format_report(result)
    assert "3 passed, 1 failed" in report
    assert "x.yaml" in report
