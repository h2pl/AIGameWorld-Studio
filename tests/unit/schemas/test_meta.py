# MetaSchema 单元测试 / Unit tests for MetaSchema

import pytest
from pydantic import ValidationError

from src.schemas.meta import MetaSchema


def test_valid_meta():
    """合法 meta 解析成功 / Valid meta parses successfully."""
    m = MetaSchema(id="forgotten_realm", name="遗忘国度", ruleset="d20", starting_scene="tavern")
    assert m.id == "forgotten_realm"
    assert m.name == "遗忘国度"
    assert m.ruleset == "d20"
    assert m.starting_scene == "tavern"


def test_description_default():
    """description 默认为空字符串 / description defaults to empty string."""
    m = MetaSchema(id="test", name="test", ruleset="d20", starting_scene="s1")
    assert m.description == ""


def test_missing_required_field():
    """缺少必须字段抛 ValidationError / Missing required field raises."""
    with pytest.raises(ValidationError):
        MetaSchema(name="test", ruleset="d20", starting_scene="s1")  # type: ignore[call-arg]
