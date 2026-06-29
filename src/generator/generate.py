# 生成器 / Generator
# 预设模式：内置数据 → YAML 文件树（P1 唯一模式）
# Phase 2 加手动模式，Phase 3 加 AI 推导模式

from pathlib import Path

import yaml


def generate_preset(preset_name: str, output_dir: Path) -> Path:
    """根据内置模板名生成 YAML 文件树 / Generate YAML tree from preset."""
    from src.generator import presets

    data = presets.PRESETS.get(preset_name)
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
