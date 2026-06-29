# PlayerCharacterSchema 单元测试 / Unit tests for PlayerCharacterSchema

import pytest
from pydantic import ValidationError

from src.schemas.pc import PlayerCharacterSchema


def test_valid_pc():
    """合法 PC 解析成功 / Valid PC parses successfully."""
    pc = PlayerCharacterSchema(
        id="warrior",
        name="战士",
        role="warrior",
        race="human",
        personality="勇敢正直",
        attributes={"str": 16, "dex": 12, "con": 16, "int": 10, "wis": 12, "cha": 10},
    )
    assert pc.id == "warrior"
    assert pc.attributes["str"] == 16


def test_optional_fields_default():
    """可选字段默认为 None 或空列表 / Optional fields default to None or empty."""
    pc = PlayerCharacterSchema(
        id="x",
        name="x",
        role="warrior",
        race="human",
        personality="x",
        attributes={"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
    )
    assert pc.combat is None
    assert pc.character_arc is None
    assert pc.equipment is None
    assert pc.inventory == []


def test_missing_required():
    """缺少必须字段抛错 / Missing required field raises."""
    with pytest.raises(ValidationError):
        PlayerCharacterSchema(id="x", name="x", role="warrior")  # type: ignore[call-arg]
