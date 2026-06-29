# player_characters/*.yaml 的 Pydantic 模型 / Pydantic model for player_characters/*.yaml
# 主角团 / Player Characters


from pydantic import BaseModel, Field


class PlayerCharacterSchema(BaseModel):
    """主角团成员 / Player character."""

    id: str  # PC 唯一标识 / PC unique identifier
    name: str  # 名称 / Name
    role: str  # 职业 warrior/mage/rogue/cleric / Class
    race: str  # 种族 / Race
    personality: str  # 性格描述 / Personality description
    attributes: dict[str, int]  # 六维属性 {str, dex, con, int, wis, cha} / Six attributes
    combat: dict | None = None  # 战斗数值 {hp, ac, attack_bonus, damage_bonus}
    character_arc: dict | None = None  # 角色弧 {goal, flaw, turning_point}
    equipment: dict | None = None  # 初始装备 {weapon, armor, accessories}
    inventory: list[dict] = Field(default_factory=list)  # 背包 [{item_id, qty}]
