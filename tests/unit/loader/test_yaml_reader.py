# YAML Reader 单元测试 / Unit tests for YAML Reader

from pathlib import Path

import yaml

from src.loader.yaml_reader import load_all


def _write(tmp: Path, rel: str, data: dict):
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


def test_load_all(tmp_path: Path):
    _write(tmp_path, "meta.yaml", {"id": "test", "name": "Test", "ruleset": "d20", "starting_scene": "s1"})
    _write(tmp_path, "scenes/tavern.yaml", {"id": "s1", "name": "Tavern", "type": "indoor", "description": "A tavern"})
    _write(
        tmp_path,
        "player_characters/pc1.yaml",
        {"id": "pc1", "name": "PC", "role": "warrior", "race": "human", "personality": "", "attributes": {}},
    )
    _write(tmp_path, "story_setup.yaml", {"arcs": [], "hooks": []})

    data = load_all(tmp_path)
    assert data["meta"]["id"] == "test"
    assert len(data["scenes"]) == 1
    assert data["scenes"][0]["id"] == "s1"
    assert len(data["player_characters"]) == 1


def test_empty_dirs(tmp_path: Path):
    (tmp_path / "actors").mkdir()
    data = load_all(tmp_path)
    assert data["actors"] == []
    assert data["meta"] == {}


def test_missing_optional_dirs(tmp_path: Path):
    data = load_all(tmp_path)
    assert data["lore"] == []
