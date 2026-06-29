# 生成器 / Generator
# 读 YAML 模板 → 填充预设数据 → 写输出目录
# Reads YAML templates, fills with preset data, writes to output

from dataclasses import dataclass, field
from pathlib import Path

import yaml

TEMPLATE_DIR = Path("templates")


@dataclass
class GeneratorParams:
    """生成器输入参数 / Generator input parameters."""

    world_name: str
    ruleset: str = "d20"
    num_pcs: int = 2
    num_actors: int = 2
    num_scenes: int = 2
    num_items: int = 5
    num_scene_objects: int = 2
    lore_categories: list[str] = field(default_factory=lambda: ["geography", "history"])
    output_dir: Path = Path("output")


def generate_preset(preset_name: str, output_dir: Path, presets_module=None) -> Path:
    """根据内置模板名生成 YAML 文件树 / Generate YAML tree from preset."""
    if presets_module is None:
        from src.generator import presets as presets_module
    data = presets_module.PRESETS.get(preset_name)
    if data is None:
        raise ValueError(f"Unknown preset: {preset_name}")
    out = output_dir / preset_name
    out.mkdir(parents=True, exist_ok=True)

    _write_meta(out, data["meta"])
    _write_resource(out, "lore", data.get("lore", []))
    _write_resource(out, "scenes", data.get("scenes", []))
    _write_resource(out, "player_characters", data.get("player_characters", []))
    _write_resource(out, "actors", data.get("actors", []))
    _write_resource(out, "items", data.get("items", []))
    _write_resource(out, "scene_objects", data.get("scene_objects", []))
    _write_story_setup(out, data.get("story_setup", {}))

    return out


def generate_manual(params: GeneratorParams) -> Path:
    """手动模式：按数量生成骨架 YAML（占位符） / Manual mode: skeleton YAML."""
    out = params.output_dir / params.world_name
    out.mkdir(parents=True, exist_ok=True)

    _write_meta(
        out,
        {
            "id": params.world_name,
            "name": params.world_name,
            "ruleset": params.ruleset,
            "starting_scene": "",
        },
    )

    for tpl_name in ["lore", "scenes", "player_characters", "actors", "items", "scene_objects"]:
        (out / tpl_name).mkdir(exist_ok=True)

    _write_story_setup(out, {"arcs": [], "hooks": []})
    return out


def _write_meta(out: Path, data: dict):
    """写入 meta.yaml / Write meta.yaml."""
    _dump(out / "meta.yaml", data)


def _write_resource(out: Path, name: str, items: list[dict]):
    """写入资源文件 / Write resource files."""
    subdir = out / name
    subdir.mkdir(exist_ok=True)
    for item in items:
        item_id = item.get("id", f"{name}_{len(list(subdir.glob('*.yaml')))}")
        _dump(subdir / f"{item_id}.yaml", item)


def _write_story_setup(out: Path, data: dict):
    """写入 story_setup.yaml / Write story_setup.yaml."""
    _dump(out / "story_setup.yaml", data)


def _dump(filepath: Path, data: dict):
    """写入 YAML 文件（UTF-8，保留中文）."""
    filepath.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
