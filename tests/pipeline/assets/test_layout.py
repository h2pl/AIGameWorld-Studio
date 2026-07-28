# 布局测试 / Layout tests

import json
from pathlib import Path

from src.services.generator.assets_layout import COLS, ROWS, generate_layout


def test_generate_layout_basic(tmp_path: Path):
    """基本布局生成 / Basic layout generation."""
    scenes = [
        {
            "id": "scene_1",
            "type": "indoor",
            "name": "酒馆",
            "landmarks": [],
            "exits": [],
        }
    ]
    result = generate_layout(scenes, tmp_path)

    assert len(result) == 1
    path = tmp_path / "layout.json"
    assert path.exists()

    data = json.loads(path.read_text("utf-8"))
    assert "scenes" in data
    assert len(data["scenes"]) == 1

    s = data["scenes"][0]
    assert s["scene_id"] == "scene_1"
    assert s["cols"] == COLS
    assert s["rows"] == ROWS
    assert len(s["map"]) == ROWS
    assert len(s["map"][0]) == COLS


def test_generate_layout_with_landmarks(tmp_path: Path):
    """带地标的布局 / Layout with landmarks."""
    scenes = [
        {
            "id": "tavern",
            "type": "indoor",
            "landmarks": [
                {"id": "bar", "name": "吧台", "desc": "老旧的木质吧台"},
                {"id": "fireplace", "name": "壁炉", "desc": "燃烧的壁炉"},
            ],
            "exits": [],
        }
    ]
    generate_layout(scenes, tmp_path)
    data = json.loads((tmp_path / "layout.json").read_text("utf-8"))
    lm = data["scenes"][0]["landmarks"]

    assert len(lm) == 2
    assert lm[0]["id"] == "bar"
    assert lm[1]["id"] == "fireplace"
    # 地标应在不同位置 / Landmarks at different positions
    assert lm[0]["x"] != lm[1]["x"]


def test_generate_layout_with_exits(tmp_path: Path):
    """带出口的布局 + 地图标记 / Layout with exits + map marker."""
    scenes = [
        {
            "id": "scene_1",
            "type": "outdoor",
            "landmarks": [],
            "exits": [
                {"target_scene": "scene_2", "condition": "向东走"},
            ],
        }
    ]
    generate_layout(scenes, tmp_path)
    data = json.loads((tmp_path / "layout.json").read_text("utf-8"))
    exits = data["scenes"][0]["exits"]

    assert len(exits) == 1
    assert exits[0]["target"] == "scene_2"
    assert exits[0]["label"] == "向东走"

    # 出口在地图边缘 / Exit at edge
    assert exits[0]["x"] == COLS - 1


def test_generate_layout_multiple_scenes(tmp_path: Path):
    """多场景布局 / Multiple scene layouts."""
    scenes = [
        {"id": "s1", "type": "indoor", "landmarks": [], "exits": []},
        {"id": "s2", "type": "outdoor", "landmarks": [], "exits": []},
        {"id": "s3", "type": "dungeon", "landmarks": [], "exits": []},
    ]
    generate_layout(scenes, tmp_path)
    data = json.loads((tmp_path / "layout.json").read_text("utf-8"))

    assert len(data["scenes"]) == 3
    # 不同场景类型有不同 tileset / Different tilesets for different types
    assert data["scenes"][0]["tileset"] == "indoor"
    assert data["scenes"][1]["tileset"] == "outdoor"
    assert data["scenes"][2]["tileset"] == "dungeon"


def test_generate_layout_empty_scenes(tmp_path: Path):
    """空场景列表 / Empty scenes list."""
    result = generate_layout([], tmp_path)
    assert len(result) == 1
    data = json.loads((tmp_path / "layout.json").read_text("utf-8"))
    assert len(data["scenes"]) == 0


def test_generate_layout_map_values(tmp_path: Path):
    """地图值在合理范围 / Map values in valid range."""
    scenes = [{"id": "s1", "type": "indoor", "landmarks": [], "exits": []}]
    generate_layout(scenes, tmp_path)
    data = json.loads((tmp_path / "layout.json").read_text("utf-8"))
    tile_map = data["scenes"][0]["map"]

    # 所有值应是整数 / All values should be integers
    for row in tile_map:
        for cell in row:
            assert isinstance(cell, int)
