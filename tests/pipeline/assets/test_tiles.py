# 瓦片集测试 / Tileset tests

from pathlib import Path

from src.pipeline.assets.tiles import TILESET_STYLES, generate_tileset


def test_tileset_styles_coverage():
    """TILESET_STYLES 覆盖所有场景类型 / Covers all scene types."""
    expected = {"village", "indoor", "outdoor", "dungeon", "urban", "wilderness", "underground"}
    assert set(TILESET_STYLES.keys()) >= expected


def test_generate_tileset_skip(tmp_path: Path):
    """skip 模式不生成文件 / Skip mode generates no files."""
    scenes = [{"id": "s1", "type": "indoor"}]
    result = generate_tileset(scenes, tmp_path, method="skip")
    assert len(result) == 0


def test_generate_tileset_empty_scenes(tmp_path: Path):
    """空场景列表 / Empty scenes list."""
    result = generate_tileset([], tmp_path, method="download")
    assert len(result) == 0
