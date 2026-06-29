# 生成器主入口 / Generator Main Entry
# GeneratorParams → generate() → YAML 模板目录

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GeneratorParams:
    """生成器输入参数，三种来源归一化为此结构 / Unified params from three sources."""

    world_name: str  # 世界名称 / World name
    ruleset: str = "d20"  # 规则集 / Ruleset
    num_pcs: int = 2  # 主角数量 / Number of PCs
    num_actors: int = 2  # 配角数量 / Number of actors
    num_scenes: int = 2  # 场景数量 / Number of scenes
    num_items: int = 5  # 物品数量 / Number of items
    num_scene_objects: int = 2  # 场景对象数量 / Number of scene objects
    lore_categories: list[str] = field(default_factory=lambda: ["geography", "history"])
    output_dir: Path = Path("output")  # 输出目录 / Output directory
