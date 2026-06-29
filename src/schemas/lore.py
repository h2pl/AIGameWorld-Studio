# lore/*.yaml 的 Pydantic 模型 / Pydantic model for lore/*.yaml
# 设定集条目：类别 + 全文 / Lore entry: category + full text

from typing import Literal

from pydantic import BaseModel, Field

# 设定类别枚举 / Lore category enum
Category = Literal["geography", "history", "race", "faction", "culture", "magic", "religion"]


class LoreSchema(BaseModel):
    """设定集条目 / Lore entry."""

    id: str  # 设定条目唯一标识 / Unique lore entry identifier
    content: str  # 设定正文（全文灌 ChromaDB）/ Full text for ChromaDB
    category: Category  # 设定类别 / Lore category
    references: list[str] = Field(default_factory=list)  # 关联实体 id / Referenced entity ids
