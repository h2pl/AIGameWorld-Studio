# 骨架生成单元测试 / Unit tests for skeleton generation

from pathlib import Path

import yaml

from src.generator.params import GenerateParams
from src.generator.skeleton import generate_skeleton
from src.validator.validate import validate_template


def test_generate_creates_all_entity_types(tmp_path: Path):
    p = GenerateParams(
        pack_id="test",
        world_name="测试世界",
        output_dir=tmp_path,
        num_pcs=2,
        num_actors=2,
        num_scenes=2,
        num_items=3,
        num_lore=2,
        num_scene_objects=2,
    )
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    assert (out / "meta.yaml").exists()
    assert (out / "story_setup.yaml").exists()
    assert len(list((out / "lore").glob("*.yaml"))) == 2
    assert len(list((out / "scenes").glob("*.yaml"))) == 2
    assert len(list((out / "player_characters").glob("*.yaml"))) == 2
    assert len(list((out / "actors").glob("*.yaml"))) == 2
    assert len(list((out / "items").glob("*.yaml"))) == 3
    assert len(list((out / "scene_objects").glob("*.yaml"))) == 2


def test_generate_passes_validation(tmp_path: Path):
    p = GenerateParams(
        pack_id="test",
        world_name="test",
        output_dir=tmp_path,
        num_pcs=2,
        num_actors=2,
        num_scenes=2,
        num_items=3,
        num_lore=2,
        num_scene_objects=2,
    )
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)
    r = validate_template(out)
    assert r.is_valid, r.errors


def test_generate_meta_content(tmp_path: Path):
    """meta: id=pack_id, name=world_name / Meta uses pack_id for id, world_name for name."""
    p = GenerateParams(pack_id="wow", world_name="魔兽世界", output_dir=tmp_path)
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    meta = yaml.safe_load((out / "meta.yaml").read_text("utf-8"))
    assert meta["id"] == "wow"
    assert meta["name"] == "魔兽世界"
    assert meta["starting_scene"] == "scene_1"


def test_generate_pc_content(tmp_path: Path):
    """PC: id=prefix_n, name=世界名 - PC n."""
    p = GenerateParams(world_name="仙剑世界", num_pcs=1, output_dir=tmp_path)
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    pc = yaml.safe_load((out / "player_characters/player_character_1.yaml").read_text("utf-8"))
    assert pc["id"] == "player_character_1"
    assert pc["name"] == "PC 1"
    assert pc["role"] == "warrior"


def test_generate_scene_content(tmp_path: Path):
    p = GenerateParams(world_name="test", num_scenes=1, output_dir=tmp_path)
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    scene = yaml.safe_load((out / "scenes/scene_1.yaml").read_text("utf-8"))
    assert scene["id"] == "scene_1"
    assert scene["name"] == "Scene 1"
    assert scene["type"] == "indoor"


def test_generate_npc_content(tmp_path: Path):
    p = GenerateParams(world_name="test", num_actors=1, output_dir=tmp_path)
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    actor = yaml.safe_load((out / "actors/actor_1.yaml").read_text("utf-8"))
    assert actor["role"] == "npc"
    assert actor["name"] == "NPC 1"


def test_generate_item_content(tmp_path: Path):
    p = GenerateParams(world_name="test", num_items=1, output_dir=tmp_path)
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    item = yaml.safe_load((out / "items/item_1.yaml").read_text("utf-8"))
    assert item["item_type"] == "misc"


def test_generate_scene_object_content(tmp_path: Path):
    p = GenerateParams(world_name="test", num_scene_objects=1, output_dir=tmp_path)
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    so = yaml.safe_load((out / "scene_objects/scene_object_1.yaml").read_text("utf-8"))
    assert so["object_type"] == "decoration"
    assert so["scene_id"] == "scene_1"


def test_generate_zero_entities(tmp_path: Path):
    p = GenerateParams(
        pack_id="test",
        world_name="test",
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
    assert not (out / "player_characters").exists()


def test_generate_output_files_are_valid_yaml(tmp_path: Path):
    p = GenerateParams(
        pack_id="test",
        world_name="test",
        output_dir=tmp_path,
        num_pcs=2,
        num_actors=2,
        num_scenes=2,
        num_items=2,
        num_lore=2,
        num_scene_objects=2,
    )
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    for f in out.rglob("*.yaml"):
        data = yaml.safe_load(f.read_text("utf-8"))
        assert data is not None, f"Invalid YAML: {f}"


def test_generate_cross_ref_valid(tmp_path: Path):
    p = GenerateParams(pack_id="test", world_name="test", num_scenes=1, num_scene_objects=1, output_dir=tmp_path)
    out = tmp_path / p.pack_id
    generate_skeleton(out, p)

    r = validate_template(out)
    assert r.is_valid, r.errors


def test_pack_id_auto_slug():
    """pack_id 自动从中文名生成 / pack_id auto-generated from Chinese name."""
    p = GenerateParams(world_name="魔兽世界")
    assert p.pack_id == "my_world"  # 全中文 → 默认值

    p2 = GenerateParams(world_name="Test World 2026")
    assert p2.pack_id == "test_world_2026"

    p3 = GenerateParams(world_name="仙剑-世界")
    assert p3.pack_id == "my_world"


def test_pack_id_explicit():
    """pack_id 显式指定 / pack_id explicit."""
    p = GenerateParams(world_name="魔兽世界", pack_id="warcraft_world")
    assert p.pack_id == "warcraft_world"
