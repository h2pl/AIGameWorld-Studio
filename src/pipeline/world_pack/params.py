# 生成参数 / Generation parameters

import re
from dataclasses import dataclass, field
from pathlib import Path


def _slugify(text: str) -> str:
    """中文名 → ASCII slug / Chinese name → ASCII slug."""
    # 保留 ASCII 字母数字，其余替换为下划线
    slug = re.sub(r"[^a-zA-Z0-9]", "_", text.lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "my_world"


@dataclass
class GenerateParams:
    """世界生成参数 / World generation parameters."""

    world_name: str = "my_world"  # 世界显示名称 / World display name (可为中文)
    pack_id: str = ""  # pack 目录名 + YAML id（ASCII）/ Pack directory name + YAML id
    theme: str = ""  # 世界主题描述 / World theme description
    num_pcs: int = 2
    num_actors: int = 2
    num_scenes: int = 2
    num_items: int = 5
    num_scene_objects: int = 2
    # Lore 条数 / Number of lore entries
    num_lore: int = 1
    # 输出根目录 / Output root directory
    output_dir: Path = field(default_factory=lambda: Path("world-packs") / "custom")
    max_retries: int = 3
    assets_method: str = "recolor"  # 素材方式: recolor | ai | skip  # LLM 最大重试次数 / Max LLM retries

    def __post_init__(self):
        # pack_id 为空时自动生成 / Auto-generate pack_id if empty
        if not self.pack_id:
            self.pack_id = _slugify(self.world_name)

    # 实体类型 → 数量映射 / Entity type → count mapping
    def count_of(self, entity_type: str) -> int:
        """返回实体类型的生成数量."""
        return {
            "meta": 1,
            "story_setup": 1,
            "lore": self.num_lore,
            "scene": self.num_scenes,
            "player_character": self.num_pcs,
            "actor": self.num_actors,
            "item": self.num_items,
            "scene_object": self.num_scene_objects,
        }[entity_type]
