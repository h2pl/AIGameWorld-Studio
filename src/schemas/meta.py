# meta.yaml 的 Pydantic 模型 / Pydantic model for meta.yaml
# 世界元信息：id/名称/规则集/起始场景 / World metadata

from pydantic import BaseModel


class MetaSchema(BaseModel):
    """世界元信息 / World metadata."""

    id: str  # 世界唯一标识 / World unique identifier
    name: str  # 显示名称 / Display name
    ruleset: str  # 规则集 d20/custom / Ruleset
    starting_scene: str  # 起始场景 id / Starting scene id
    description: str = ""  # 一句话描述 / One-line description
