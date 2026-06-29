# ItemSchema 单元测试 / Unit tests for ItemSchema

import pytest
from pydantic import ValidationError

from src.schemas.item import ItemSchema


def test_valid_item():
    """合法 item 解析成功 / Valid item parses successfully."""
    i = ItemSchema(id="longsword", name="长剑", item_type="weapon", rarity="common")
    assert i.id == "longsword"
    assert i.item_type == "weapon"


def test_invalid_type():
    """非法 item_type 抛错 / Invalid item_type raises."""
    with pytest.raises(ValidationError):
        ItemSchema(id="x", name="x", item_type="unknown", rarity="common")  # type: ignore[arg-type]


def test_invalid_rarity():
    """非法 rarity 抛错 / Invalid rarity raises."""
    with pytest.raises(ValidationError):
        ItemSchema(id="x", name="x", item_type="weapon", rarity="mythic")  # type: ignore[arg-type]


def test_defaults():
    """默认值 / Defaults."""
    i = ItemSchema(id="sword", name="剑", item_type="weapon", rarity="common")
    assert i.rarity == "common"
    assert i.weight == 0.0
    assert i.value == 0
    assert i.data is None


def test_with_data():
    """含扩展数据的 item / Item with data."""
    i = ItemSchema(
        id="longsword",
        name="长剑",
        item_type="weapon",
        rarity="uncommon",
        weight=3.0,
        value=15,
        data={"damage_dice": "1d8", "damage_type": "slashing"},
    )
    assert i.data is not None
    assert i.data["damage_dice"] == "1d8"
