# scene_objects/*.yaml 的 Pydantic 模型 / Pydantic model for scene_objects/*.yaml
# 场景对象：宝箱/门/陷阱/机关 / Scene objects

from typing import Literal

from pydantic import BaseModel

ObjectType = Literal["chest", "door", "trap", "mechanism", "decoration"]


class SceneObjectSchema(BaseModel):
    """场景对象 / Scene object."""

    id: str  # 对象唯一标识 / Object unique identifier
    name: str  # 名称 / Name
    object_type: ObjectType  # 对象类型 / Object type
    scene_id: str  # 所属场景（外键 → scenes.id）/ Parent scene FK
    interact_data: dict | None = None  # 交互数据（宝箱 loot_table/DC，门 locked/DC/key_id）
