# YAML 读取 / YAML Reader
# 解析模板目录下所有 YAML 文件 → dict 集合

from pathlib import Path

import yaml


def load_all(template_dir: Path) -> dict:
    """加载模板目录下所有 YAML / Load all YAML from template directory.

    Returns: {entity_type: [list of dicts]}
    """
    result = {
        "meta": {},
        "lore": [],
        "scenes": [],
        "player_characters": [],
        "actors": [],
        "items": [],
        "scene_objects": [],
        "story_setup": {"arcs": [], "hooks": []},
    }
    if not template_dir.exists():
        return result

    _load_single(template_dir / "meta.yaml", result, "meta")
    _load_dir(template_dir / "lore", result, "lore")
    _load_dir(template_dir / "scenes", result, "scenes")
    _load_dir(template_dir / "player_characters", result, "player_characters")
    _load_dir(template_dir / "actors", result, "actors")
    _load_dir(template_dir / "items", result, "items")
    _load_dir(template_dir / "scene_objects", result, "scene_objects")
    _load_single(template_dir / "story_setup.yaml", result, "story_setup")

    return result


def _load_single(filepath: Path, result: dict, key: str):
    """加载单个 YAML 文件."""
    if filepath.exists():
        data = yaml.safe_load(filepath.read_text(encoding="utf-8")) or {}
        result[key] = data


def _load_dir(dirpath: Path, result: dict, key: str):
    """加载目录下所有 YAML 文件."""
    if not dirpath.exists():
        return
    for yf in sorted(dirpath.glob("*.yaml")):
        data = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
        result[key].append(data)
