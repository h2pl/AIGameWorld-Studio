# SceneObjectSchema 单元测试 / Unit tests for SceneObjectSchema

import pytest
from pydantic import ValidationError

from src.schemas.scene_object import SceneObjectSchema


def test_valid_object():
    """合法场景对象解析成功 / Valid scene object parses successfully."""
    o = SceneObjectSchema(id="tavern_chest", name="酒馆宝箱", object_type="chest", scene_id="tavern")
    assert o.id == "tavern_chest"
    assert o.object_type == "chest"
    assert o.scene_id == "tavern"


def test_invalid_type():
    """非法 object_type 抛错 / Invalid object_type raises."""
    with pytest.raises(ValidationError):
        SceneObjectSchema(id="x", name="x", object_type="unknown", scene_id="s1")  # type: ignore[arg-type]


def test_defaults():
    """默认值 / Defaults."""
    o = SceneObjectSchema(id="chest", name="箱子", object_type="chest", scene_id="s1")
    assert o.interact_data is None


def test_with_interact_data():
    """含交互数据的对象 / Object with interact data."""
    o = SceneObjectSchema(
        id="locked_door",
        name="锁着的门",
        object_type="door",
        scene_id="tavern",
        interact_data={"locked": True, "dc": 15},
    )
    assert o.interact_data is not None
    assert o.interact_data["locked"] is True
    assert o.interact_data["dc"] == 15
