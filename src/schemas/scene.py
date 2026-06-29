# scenes/*.yaml 的 Pydantic 模型 / Pydantic model for scenes/*.yaml
# 场景 / Scene

from typing import Literal

from pydantic import BaseModel, Field

SceneType = Literal["indoor", "outdoor", "dungeon", "urban", "wilderness"]


class SceneSchema(BaseModel):
    """场景 / Scene."""

    id: str  # 场景唯一标识 / Scene unique identifier
    name: str  # 场景名称 / Scene name
    type: SceneType  # 场景类型 / Scene type
    description: str  # 场景描述（灌 ChromaDB）/ Scene description for ChromaDB
    environment: dict | None = None  # 环境属性（光照/天气/温度）/ Environment (light/weather/temp)
    exits: list[dict] = Field(default_factory=list)  # 出口 [{target_scene, condition}]
    landmarks: list[dict] = Field(default_factory=list)  # 地标 [{id, name, desc}]
