# story_setup.yaml 的 Pydantic 模型 / Pydantic model for story_setup.yaml
# 初始剧情：主线弧 + 伏笔 / Story setup: arcs + hooks

from pydantic import BaseModel, Field


class StoryArc(BaseModel):
    """主线弧 / Story arc."""

    type: str = "main"  # 类型 main/side / Arc type
    title: str  # 标题 / Title
    stage: str = "hook"  # 阶段 / Stage
    main_cast: list[str] = Field(default_factory=list)  # 主角团 / Main cast PC ids
    branching_points: list[dict] = Field(default_factory=list)  # 分支点 [{trigger, next}]


class StoryHook(BaseModel):
    """初始伏笔 / Story hook."""

    description: str  # 描述 / Description
    urgency: str = "medium"  # 紧迫度 low/medium/high / Urgency


class StorySetupSchema(BaseModel):
    """初始剧情 / Story setup."""

    arcs: list[StoryArc] = Field(default_factory=list)  # 主线弧列表 / Story arcs
    hooks: list[StoryHook] = Field(default_factory=list)  # 初始伏笔列表 / Initial hooks
