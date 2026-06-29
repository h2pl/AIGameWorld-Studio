# LoreSchema 单元测试 / Unit tests for LoreSchema

import pytest
from pydantic import ValidationError

from src.schemas.lore import LoreSchema


def test_valid_lore():
    """合法 lore 解析成功 / Valid lore parses successfully."""
    lore = LoreSchema(id="history_01", category="history", content="古老的历史... / Ancient history...")
    assert lore.id == "history_01"
    assert lore.category == "history"


def test_invalid_category():
    """非法 category 值抛 ValidationError / Invalid category raises."""
    with pytest.raises(ValidationError):
        LoreSchema(id="x", category="unknown_category", content="test")  # type: ignore[arg-type]


def test_defaults():
    """references 默认为空列表 / references defaults to empty list."""
    lore = LoreSchema(id="x", category="geography", content="test")
    assert lore.references == []


def test_default_factory_not_shared():
    """references default_factory 不跨实例共享 / references not shared."""
    l1 = LoreSchema(id="a", category="history", content="a")
    l2 = LoreSchema(id="b", category="geography", content="b")
    l1.references.append("ref1")
    assert l2.references == []
