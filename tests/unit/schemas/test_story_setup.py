# StorySetup 单元测试 / Unit tests for StorySetupSchema

from src.schemas.story_setup import StoryArc, StoryHook, StorySetupSchema


def test_valid_arc():
    """合法 StoryArc 解析成功 / Valid arc parses successfully."""
    arc = StoryArc(type="main", title="失踪的商队", stage="hook", main_cast=["warrior", "mage"])
    assert arc.title == "失踪的商队"
    assert len(arc.main_cast) == 2


def test_arc_defaults():
    """StoryArc 默认值 / StoryArc defaults."""
    arc = StoryArc(title="test")
    assert arc.type == "main"
    assert arc.stage == "hook"
    assert arc.main_cast == []
    assert arc.branching_points == []


def test_valid_hook():
    """合法 StoryHook 解析成功 / Valid hook parses successfully."""
    hook = StoryHook(description="森林里有奇怪的声音", urgency="high")
    assert hook.urgency == "high"


def test_hook_default():
    """StoryHook 默认 urgency / StoryHook default urgency."""
    hook = StoryHook(description="test")
    assert hook.urgency == "medium"


def test_valid_setup():
    """合法 StorySetup 解析成功 / Valid setup parses successfully."""
    setup = StorySetupSchema(
        arcs=[StoryArc(title="主线", main_cast=["pc1"])],
        hooks=[StoryHook(description="起点")],
    )
    assert len(setup.arcs) == 1
    assert len(setup.hooks) == 1


def test_empty_setup():
    """空 arcs/hooks 合法 / Empty arcs and hooks is valid."""
    setup = StorySetupSchema()
    assert setup.arcs == []
    assert setup.hooks == []
