# 校验器 / Validator (no Pydantic)
# RimWorld Def: YAML dict field check

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 必须字段 / Required fields
REQUIRED_FIELDS = {
    "meta": ["id", "name", "ruleset", "starting_scene"],
    "lore": ["id", "category", "content"],
    "scenes": ["id", "name", "type", "description"],
    "player_characters": ["id", "name", "role", "race", "personality", "attributes"],
    "actors": ["id", "name", "role", "race", "personality", "attributes"],
    "items": ["id", "name", "item_type"],
    "scene_objects": ["id", "name", "object_type", "scene_id"],
    "story_setup": ["arcs", "hooks"],
}
