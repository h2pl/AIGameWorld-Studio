# actors/*.yaml 的 Pydantic 模型 / Pydantic model for actors/*.yaml
# 配角 / Actors (NPCs)


from pydantic import BaseModel, Field


class ActorSchema(BaseModel):
    """配角（NPC）/ Actor (NPC)."""

    id: str  # Actor 唯一标识 / Actor unique identifier
    name: str  # 名称 / Name
    role: str  # 身份 blacksmith/guard/merchant / Role
    race: str  # 种族 / Race
    personality: str  # 性格描述 / Personality description
    attributes: dict[str, int]  # 六维属性 / Six attributes
    combat: dict | None = None  # 战斗数值（敌人必有）/ Combat stats (required for enemies)
    functions: list[str] = Field(default_factory=list)  # 功能标签 [dialogue, merchant, quest_giver, combat]
    function_data: dict | None = None  # 按功能扩展数据 / Extended data per function
    equipment: dict | None = None  # 装备 / Equipment
    inventory: list[dict] = Field(default_factory=list)  # 背包 / Inventory
