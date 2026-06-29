# items/*.yaml 的 Pydantic 模型 / Pydantic model for items/*.yaml
# 物品 / Items

from typing import Literal

from pydantic import BaseModel

ItemType = Literal["weapon", "armor", "potion", "key", "quest", "misc"]
Rarity = Literal["common", "uncommon", "rare", "legendary"]


class ItemSchema(BaseModel):
    """物品 / Item."""

    id: str  # 物品唯一标识 / Item unique identifier
    name: str  # 名称 / Name
    item_type: ItemType  # 物品类型 / Item type
    rarity: Rarity = "common"  # 稀有度 / Rarity
    weight: float = 0.0  # 重量 / Weight
    value: int = 0  # 价值（金币）/ Value in gold
    data: dict | None = None  # 按类型扩展（武器 damage_dice，药水 effect）
