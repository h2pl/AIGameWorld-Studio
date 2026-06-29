# SceneSchema 单元测试 / Unit tests for SceneSchema

import pytest
from pydantic import ValidationError

from src.schemas.scene import SceneSchema


def test_valid_scene():
    """合法 scene 解析成功 / Valid scene parses successfully."""
    s = SceneSchema(id="tavern", name="酒馆", type="indoor", description="温暖喧闹的酒馆")
    assert s.id == "tavern"
    assert s.type == "indoor"


def test_invalid_type():
    """非法 type 值抛 ValidationError / Invalid type raises."""
    with pytest.raises(ValidationError):
        SceneSchema(id="x", name="x", type="unknown", description="x")  # type: ignore[arg-type]


def test_defaults():
    """可选字段默认值 / Optional field defaults."""
    s = SceneSchema(id="tavern", name="酒馆", type="indoor", description="x")
    assert s.environment is None
    assert s.exits == []
    assert s.landmarks == []


def test_default_factory_not_shared():
    """list 字段不跨实例共享 / List fields not shared."""
    s1 = SceneSchema(id="a", name="a", type="indoor", description="a")
    s2 = SceneSchema(id="b", name="b", type="outdoor", description="b")
    s1.exits.append({"target": "x"})
    assert s2.exits == []
