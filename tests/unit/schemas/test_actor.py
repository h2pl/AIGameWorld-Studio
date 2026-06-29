# ActorSchema 单元测试 / Unit tests for ActorSchema

from src.schemas.actor import ActorSchema


def test_valid_actor():
    """合法 Actor 解析成功 / Valid actor parses successfully."""
    a = ActorSchema(
        id="blacksmith",
        name="铁匠",
        role="blacksmith",
        race="dwarf",
        personality="勤奋沉默",
        attributes={"str": 16, "dex": 10, "con": 16, "int": 10, "wis": 12, "cha": 8},
    )
    assert a.id == "blacksmith"
    assert a.role == "blacksmith"


def test_optional_fields_default():
    """可选字段默认值 / Optional field defaults."""
    a = ActorSchema(
        id="x",
        name="x",
        role="x",
        race="x",
        personality="x",
        attributes={"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
    )
    assert a.combat is None
    assert a.functions == []
    assert a.function_data is None
    assert a.equipment is None
    assert a.inventory == []


def test_with_functions():
    """含功能标签的 Actor / Actor with function tags."""
    a = ActorSchema(
        id="merchant",
        name="商人",
        role="merchant",
        race="human",
        personality="精明",
        attributes={"str": 8, "dex": 12, "con": 10, "int": 14, "wis": 12, "cha": 16},
        functions=["dialogue", "merchant"],
        function_data={"merchant": {"gold": 500}},
    )
    assert len(a.functions) == 2
    assert a.function_data is not None
    assert a.function_data["merchant"]["gold"] == 500
