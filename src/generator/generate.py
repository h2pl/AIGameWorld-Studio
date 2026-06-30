# 生成器 / Generator
# 读 templates/ 下模板 YAML 蓝图，按参数生成 N 个实例骨架
# 使用 ruamel.yaml 保留模板注释 / Uses ruamel.yaml to preserve template comments

from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


def _templates_dir() -> Path:
    """模板目录 / Templates directory."""
    return Path(__file__).resolve().parent.parent.parent / "templates"


@dataclass
class GenerateParams:
    """生成参数 / Generation parameters."""

    world_name: str = "my_world"
    num_pcs: int = 2
    num_actors: int = 2
    num_scenes: int = 2
    num_items: int = 5
    num_scene_objects: int = 2
    num_lore: int = 1
    output_dir: Path = Path("worlds") / "custom"


# ---- 模板名 → (文件名, 输出子目录) / Template name → (file, output subdir) ----
_TEMPLATE_SPEC = [
    ("meta", "meta.yaml", None),
    ("lore", "lore.yaml", "lore"),
    ("scene", "scene.yaml", "scenes"),
    ("player_character", "player_character.yaml", "player_characters"),
    ("actor", "actor.yaml", "actors"),
    ("item", "item.yaml", "items"),
    ("scene_object", "scene_object.yaml", "scene_objects"),
    ("story_setup", "story_setup.yaml", None),
]

# ---- 默认值 / Defaults (确保骨架通过 validate) ----


def _defaults(entity_type: str, idx: int, world_name: str) -> dict:
    """返回该实体类型的默认填充值，覆盖模板里的空占位符。"""
    if entity_type == "meta":
        return {"id": world_name, "name": world_name, "starting_scene": "scene_1", "description": "待编辑 / TBD"}
    if entity_type == "player_character":
        roles = ["warrior", "mage", "rogue", "cleric"]
        return {"role": roles[(idx - 1) % len(roles)], "race": "human", "personality": "待编辑 / TBD"}
    if entity_type == "actor":
        return {"role": "npc", "race": "human", "personality": "待编辑 / TBD"}
    if entity_type == "scene":
        return {"type": "indoor", "description": "待编辑 / TBD"}
    if entity_type == "item":
        return {"item_type": "misc"}
    if entity_type == "scene_object":
        return {"object_type": "decoration", "scene_id": "scene_1"}
    if entity_type == "lore":
        return {"category": "history", "content": "待编辑 / TBD"}
    return {}


# ---- 主入口 / Main entry ----


def generate(params: GenerateParams) -> Path:
    """从模板生成世界 YAML 文件树 / Generate world YAML tree from templates."""
    td = _templates_dir()
    out = Path(params.output_dir) / params.world_name
    out.mkdir(parents=True, exist_ok=True)

    for key, filename, subdir in _TEMPLATE_SPEC:
        template_path = td / filename
        count = _count(key, params)
        _gen_entities(out, key, template_path, count, subdir, params.world_name)

    return out


def _count(entity_type: str, params: GenerateParams) -> int:
    """实体类型 → 生成数量 / Entity type → generation count."""
    return {
        "meta": 1,
        "story_setup": 1,
        "player_character": params.num_pcs,
        "actor": params.num_actors,
        "scene": params.num_scenes,
        "item": params.num_items,
        "scene_object": params.num_scene_objects,
        "lore": params.num_lore,
    }[entity_type]


# ---- 实体生成 / Entity generation ----


def _gen_entities(out: Path, entity_type: str, template_path: Path, count: int, subdir: str | None, world_name: str):
    """从一个模板生成 count 个实例文件。"""
    target_dir = out / subdir if subdir else out
    target_dir.mkdir(exist_ok=True)

    # 读取模板原文，重新解析每个实例以保证注释独立
    template_text = template_path.read_text(encoding="utf-8")

    for i in range(1, count + 1):
        data = _yaml.load(template_text) or {}
        defaults = _defaults(entity_type, i, world_name)
        _apply_defaults(data, defaults)
        # 覆盖 id 和 name
        if entity_type == "meta":
            data["id"] = world_name
            data["name"] = world_name
        elif entity_type != "story_setup":
            label = _label(entity_type)
            data["id"] = f"{entity_type}_{i}"
            data["name"] = f"{label} {i}"

        if entity_type == "meta":
            filepath = target_dir / "meta.yaml"
        elif entity_type == "story_setup":
            filepath = target_dir / "story_setup.yaml"
        else:
            filepath = target_dir / f"{entity_type}_{i}.yaml"

        _dump(filepath, data)


def _apply_defaults(data, defaults: dict):
    """就地设置默认值 / Apply defaults in-place (recursive for nested)."""
    for k, v in defaults.items():
        if k in data and (data[k] is None or data[k] == ""):
            data[k] = v
        elif k in data and isinstance(data[k], dict) and isinstance(v, dict):
            _apply_defaults(data[k], v)


# ---- 文件读写 / File I/O ----


def _dump(path: Path, data):
    """写入 YAML（保留注释）/ Write YAML with comment preservation."""
    with path.open("w", encoding="utf-8") as f:
        _yaml.dump(data, f)


def _label(entity_type: str) -> str:
    """实体类型 → 显示名前缀 / Entity type → display name prefix."""
    return {
        "lore": "Lore",
        "scene": "Scene",
        "player_character": "PC",
        "actor": "Actor",
        "item": "Item",
        "scene_object": "SceneObj",
    }.get(entity_type, entity_type)


# ---- 兼容旧接口（测试用）/ Keep for test compatibility ----
generate_preset = None  # 测试用，直接引用 presets.generate_preset
