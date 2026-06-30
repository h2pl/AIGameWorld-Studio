# Validator / YAML dict field check (RimWorld Def, no Pydantic)
# 检查必填字段 + 枚举值 + 跨文件引用 / Check required fields, enum values, cross-references
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 各实体类型的必填字段 / Required fields per entity type
REQUIRED_FIELDS = {
    "meta": ["id", "name", "ruleset", "starting_scene"],
    "lore": ["id", "category", "content"],
    "scenes": ["id", "name", "type", "description"],
    "player_characters": ["id", "name", "role", "race", "personality", "attributes"],
    "actors": ["id", "name", "role", "race", "personality", "attributes"],
    "items": ["id", "name", "item_type"],
    "scene_objects": ["id", "name", "object_type", "scene_id"],
    "story_setup": ["arcs"],
}

# 枚举字段的允许值 / Valid enum values
VALID_VALUES = {
    "type": ["indoor", "outdoor", "dungeon", "urban", "wilderness"],
    "category": ["geography", "history", "race", "faction", "culture", "magic", "religion"],
    "item_type": ["weapon", "armor", "shield", "potion", "scroll", "key", "consumable", "misc"],
    "object_type": ["chest", "door", "trap", "mechanism", "decoration"],
    "rarity": ["common", "uncommon", "rare", "legendary"],
    "urgency": ["low", "medium", "high"],
}


@dataclass
class ValidationResult:
    passed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self):
        return self.failed == 0


# 主校验入口 / Main validation entry
def validate_template(template_dir):
    errors = []
    passed = 0
    failed = 0
    all_data = _load_all(Path(template_dir))
    for fp, data in all_data.items():
        rel = str(fp.relative_to(template_dir))
        if e := _validate_file(rel, data):
            errors.extend(e)
            failed += 1
        else:
            passed += 1
    if ce := _validate_cross_refs(all_data):
        errors.extend(ce)
        failed += len({x.split(":")[0] for x in ce})
    return ValidationResult(passed=passed, failed=failed, errors=errors)


# 加载目录下所有 YAML 文件 / Load all YAML files
def _load_all(d):
    r = {}
    if not d.exists():
        return r
    for f in d.rglob("*.yaml"):
        try:
            r[f] = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            r[f] = {}
    return r


def _validate_file(rel, data):
    e = []
    et = _entity_type(rel)
    if not et:
        return e
    for k in REQUIRED_FIELDS.get(et, []):
        if k not in data or data[k] is None or data[k] == "":
            e.append(f"{rel} missing: {k}")
    for k, v in data.items():
        if k in VALID_VALUES and isinstance(v, str) and v:
            if v not in VALID_VALUES[k]:
                e.append(f"{rel} invalid {k}: '{v}'")
    return e


def _validate_cross_refs(all_data):
    e = []
    scene_ids = {d.get("id", "") for d in all_data.values() if isinstance(d, dict)}
    for fp, d in all_data.items():
        rel = str(fp)
        if isinstance(d, dict) and d.get("scene_id"):
            sid = d["scene_id"]
            if sid not in scene_ids:
                e.append(f"[cross-ref] {rel}: scene_id '{sid}' not found")
        if "meta" in rel and isinstance(d, dict) and d.get("starting_scene"):
            sid = d["starting_scene"]
            if sid not in scene_ids:
                e.append(f"[cross-ref] {rel}: starting_scene '{sid}' not found")
    return e


def _entity_type(rel):
    for t in REQUIRED_FIELDS:
        if t in rel:
            return t
    return None


def format_report(result):
    lines = [f"  {e}" for e in result.errors]
    lines.append("-" * 50)
    lines.append(f"{result.passed} passed, {result.failed} failed")
    return "\n".join(lines)
