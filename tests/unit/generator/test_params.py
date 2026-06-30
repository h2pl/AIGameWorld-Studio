# GenerateParams 单元测试 / Unit tests for GenerateParams

from pathlib import Path

import pytest

from src.pipeline.world_pack.params import GenerateParams, _slugify


def test_default_values():
    p = GenerateParams()
    assert p.world_name == "my_world"
    assert p.pack_id == "my_world"
    assert p.theme == ""
    assert p.num_pcs == 2
    assert p.max_retries == 3
    assert p.output_dir == Path("world-packs") / "custom"


def test_custom_values():
    p = GenerateParams(
        world_name="仙剑世界",
        pack_id="sword_world",
        theme="仙侠修真",
        num_pcs=4,
    )
    assert p.world_name == "仙剑世界"
    assert p.pack_id == "sword_world"
    assert p.theme == "仙侠修真"


def test_pack_id_auto_from_ascii():
    """ASCII 名自动生成 pack_id / ASCII name auto-generates pack_id."""
    p = GenerateParams(world_name="Test_World_2026")
    assert p.pack_id == "test_world_2026"


def test_pack_id_auto_from_chinese():
    """中文名 pack_id 兜底 my_world / Chinese name defaults pack_id to my_world."""
    p = GenerateParams(world_name="魔兽世界")
    assert p.pack_id == "my_world"


def test_count_of():
    p = GenerateParams(num_pcs=3, num_actors=4, num_scenes=5, num_items=6, num_lore=2, num_scene_objects=7)
    assert p.count_of("meta") == 1
    assert p.count_of("story_setup") == 1
    assert p.count_of("player_character") == 3
    assert p.count_of("actor") == 4
    assert p.count_of("scene") == 5
    assert p.count_of("lore") == 2


def test_count_of_zero():
    p = GenerateParams(num_pcs=0, num_actors=0, num_scenes=0, num_items=0, num_lore=0, num_scene_objects=0)
    assert p.count_of("player_character") == 0


def test_count_of_unknown_raises():
    p = GenerateParams()
    with pytest.raises(KeyError):
        p.count_of("unknown")


def test_slugify_preserves_ascii():
    assert _slugify("hello_world") == "hello_world"
    assert _slugify("Test World 2026") == "test_world_2026"


def test_slugify_chinese_defaults():
    assert _slugify("魔兽世界") == "my_world"
    assert _slugify("") == "my_world"
