# Generator 单元测试 / Unit tests for Generator (template-driven)

from pathlib import Path

import yaml

from src.generator.generate import GenerateParams, generate


def test_generate_creates_structure(tmp_path: Path):
    """生成完整目录结构 / Generates complete directory structure."""
    out = generate(
        GenerateParams(
            world_name="test",
            num_pcs=2,
            num_actors=2,
            num_scenes=2,
            num_items=3,
            num_lore=1,
            num_scene_objects=2,
            output_dir=tmp_path,
        )
    )
    assert (out / "meta.yaml").exists()
    assert (out / "player_characters" / "player_character_1.yaml").exists()
    assert (out / "player_characters" / "player_character_2.yaml").exists()
    assert (out / "actors" / "actor_1.yaml").exists()
    assert (out / "scenes" / "scene_1.yaml").exists()
    assert (out / "items" / "item_1.yaml").exists()
    assert (out / "lore" / "lore_1.yaml").exists()
    assert (out / "scene_objects" / "scene_object_1.yaml").exists()
    assert (out / "story_setup.yaml").exists()


def test_generate_correct_counts(tmp_path: Path):
    """按参数生成正确数量 / Generates correct count per param."""
    out = generate(
        GenerateParams(
            world_name="test",
            num_pcs=3,
            num_actors=4,
            num_scenes=2,
            num_items=5,
            num_lore=2,
            num_scene_objects=3,
            output_dir=tmp_path,
        )
    )
    assert len(list((out / "player_characters").glob("*.yaml"))) == 3
    assert len(list((out / "actors").glob("*.yaml"))) == 4
    assert len(list((out / "scenes").glob("*.yaml"))) == 2
    assert len(list((out / "items").glob("*.yaml"))) == 5
    assert len(list((out / "lore").glob("*.yaml"))) == 2
    assert len(list((out / "scene_objects").glob("*.yaml"))) == 3


def test_generate_output_valid_yaml(tmp_path: Path):
    """输出是合法 YAML / Output is valid YAML."""
    out = generate(GenerateParams(world_name="test", output_dir=tmp_path))
    for yf in out.rglob("*.yaml"):
        data = yaml.safe_load(yf.read_text(encoding="utf-8"))
        assert data is not None, f"Invalid YAML: {yf}"


def test_generate_passes_validation(tmp_path: Path):
    """生成后直接通过 validate / Generated world passes validation."""
    from src.validator.validate import validate_template

    out = generate(GenerateParams(world_name="test", output_dir=tmp_path))
    result = validate_template(out)
    assert result.is_valid, f"Validation errors: {result.errors}"


def test_generate_overwrite_ok(tmp_path: Path):
    """覆盖写入正常 / Overwrite works."""
    generate(GenerateParams(world_name="test", output_dir=tmp_path))
    out = generate(GenerateParams(world_name="test", output_dir=tmp_path))
    assert (out / "meta.yaml").exists()


def test_generate_custom_name(tmp_path: Path):
    """自定义世界名 / Custom world name."""
    out = generate(GenerateParams(world_name="遗忘国度", output_dir=tmp_path))
    assert out.name == "遗忘国度"
