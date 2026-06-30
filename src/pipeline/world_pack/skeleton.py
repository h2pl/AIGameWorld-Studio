# 骨架生成：模板 → 结构合法 YAML / Skeleton generator: templates → valid YAML
# 不依赖 LLM，纯模板填充 / No LLM dependency, pure template filling

from pathlib import Path

from ruamel.yaml import YAML

from src.pipeline.world_pack.params import GenerateParams

# YAML 读写（保留注释）/ YAML read/write with comment preservation
_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)

# 模板目录 / Templates directory
_TEMPLATES = Path(__file__).resolve().parent.parent.parent.parent / "templates"

# 实体规格 / Entity spec: (entity_type, template_file, output_subdir, file_prefix)
_ENTITY_SPEC: list[tuple[str, str, str | None, str]] = [
    ("meta", "meta.yaml", None, "meta"),
    ("story_setup", "story_setup.yaml", None, "story_setup"),
    ("lore", "lore.yaml", "lore", "lore"),
    ("scene", "scene.yaml", "scenes", "scene"),
    ("pc", "player_character.yaml", "player_characters", "player_character"),
    ("npc", "actor.yaml", "actors", "actor"),
    ("item", "item.yaml", "items", "item"),
    ("scene_object", "scene_object.yaml", "scene_objects", "scene_object"),
]


def generate_skeleton(out_dir: Path, params: GenerateParams) -> None:
    """用模板生成所有实体的 YAML 骨架 / Generate all entity YAML skeletons from templates.

    填充结构字段（id/name/type 等），创意字段留占位符，确保通过 Validator 校验.
    """
    for entity_type, template_file, subdir, prefix in _ENTITY_SPEC:
        count = params.count_of(prefix)
        if count == 0:
            continue
        target_dir = out_dir / subdir if subdir else out_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        template_text = (_TEMPLATES / template_file).read_text(encoding="utf-8")

        for i in range(1, count + 1):
            data = _yaml.load(template_text) or {}
            _fill_entity(data, entity_type, prefix, i, params)
            _write_entity(target_dir, entity_type, prefix, i, data)


def _fill_entity(data: dict, entity_type: str, prefix: str, idx: int, params: GenerateParams):
    """填充单个实体的结构字段 / Fill structural fields for a single entity.

    id = pack_id（ASCII 标识符）/ ID = pack_id (ASCII identifier)
    name = world_name（显示名，可为中文）/ Name = world_name (display name, can be Chinese)
    """
    if entity_type == "meta":
        data["id"] = params.pack_id
        data["name"] = params.world_name
        data["starting_scene"] = "scene_1"
    elif entity_type == "story_setup":
        return
    else:
        data["id"] = f"{prefix}_{idx}"
        data["name"] = f"{_label(entity_type)} {idx}"
        _fill_if_empty(data, "type", {"scene": "indoor"}.get(entity_type, ""))
        _fill_if_empty(data, "category", "history")
        _fill_if_empty(data, "item_type", "misc")
        _fill_if_empty(data, "object_type", "decoration")
        _fill_if_empty(data, "role", "warrior" if entity_type == "pc" else "npc")
        _fill_if_empty(data, "race", "human")
        _fill_if_empty(data, "scene_id", "scene_1")
        _fill_if_empty(data, "personality", "待 LLM 生成 / TBD")
        _fill_if_empty(data, "description", "待 LLM 生成 / TBD")
        _fill_if_empty(data, "content", "待 LLM 生成 / TBD")


def _write_entity(target_dir: Path, entity_type: str, prefix: str, idx: int, data: dict):
    """写入单个实体 YAML 文件 / Write single entity YAML file."""
    # 文件名规则: meta → meta.yaml, story_setup → story_setup.yaml, 其他 → {prefix}_{idx}.yaml
    if entity_type == "meta":
        filepath = target_dir / "meta.yaml"
    elif entity_type == "story_setup":
        filepath = target_dir / "story_setup.yaml"
    else:
        filepath = target_dir / f"{prefix}_{idx}.yaml"
    with filepath.open("w", encoding="utf-8") as f:
        _yaml.dump(data, f)


def _fill_if_empty(data: dict, key: str, value):
    """字段为空时填充默认值 / Fill field with default if empty."""
    if key in data and (data[key] is None or data[key] == ""):
        data[key] = value


def _label(entity_type: str) -> str:
    """实体类型 → 显示名前缀 / Entity type → display label."""
    return {
        "lore": "Lore",
        "scene": "Scene",
        "pc": "PC",
        "npc": "NPC",
        "item": "Item",
        "scene_object": "SceneObj",
    }.get(entity_type, entity_type)
