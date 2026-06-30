# LLM utils 单元测试 / Unit tests for LLM utils

from pathlib import Path

import yaml

from src.llm.utils import write_yaml_entities


def test_write_single_entity(tmp_path: Path):
    """单个实体正常写入 / Write single entity correctly."""
    content = """# item_1
id: item_1
name: 寒铁长剑
item_type: weapon
rarity: uncommon
description: 以北冥寒铁锻造
"""
    write_yaml_entities(tmp_path, content, "item")
    assert (tmp_path / "item_1.yaml").exists()
    data = yaml.safe_load((tmp_path / "item_1.yaml").read_text("utf-8"))
    assert data["id"] == "item_1"
    assert data["name"] == "寒铁长剑"
    assert data["item_type"] == "weapon"


def test_write_multiple_entities(tmp_path: Path):
    """多个实体按前缀分割写入 / Multiple entities split by prefix comments."""
    content = """# lore_1
id: lore_1
category: history
content: 上古传说

# lore_2
id: lore_2
category: faction
content: 江湖门派
"""
    write_yaml_entities(tmp_path, content, "lore")
    assert (tmp_path / "lore_1.yaml").exists()
    assert (tmp_path / "lore_2.yaml").exists()
    d1 = yaml.safe_load((tmp_path / "lore_1.yaml").read_text("utf-8"))
    d2 = yaml.safe_load((tmp_path / "lore_2.yaml").read_text("utf-8"))
    assert d1["category"] == "history"
    assert d2["category"] == "faction"


def test_write_overwrites_existing(tmp_path: Path):
    """覆盖已存在文件 / Overwrites existing files."""
    (tmp_path / "item_1.yaml").write_text("old", encoding="utf-8")
    content = "# item_1\nid: item_1\nname: 新物品\nitem_type: misc\n"
    write_yaml_entities(tmp_path, content, "item")
    data = yaml.safe_load((tmp_path / "item_1.yaml").read_text("utf-8"))
    assert data["name"] == "新物品"


def test_write_empty_content_no_files(tmp_path: Path):
    """空内容不写出有效文件 / Empty content writes no meaningful files."""
    write_yaml_entities(tmp_path, "", "lore")
    # 空行解析后 blocks 为空列表，不应创建任何文件
    files = list(tmp_path.glob("*.yaml"))
    assert len(files) == 0, f"Expected 0 files, got {len(files)}/{files}"


def test_write_prefix_not_in_content(tmp_path: Path):
    """无前缀注释时保留唯一块 / Content without prefix comment keeps one block."""
    content = "id: lore_1\ncategory: history\ncontent: test\n"
    write_yaml_entities(tmp_path, content, "lore")
    assert (tmp_path / "lore_1.yaml").exists()


def test_write_with_extra_spaces(tmp_path: Path):
    """前缀注释前后有空格仍正确匹配 / Prefix with spaces still matches."""
    content = "  # item_1  \nid: item_1\nname: test\nitem_type: misc\n"
    write_yaml_entities(tmp_path, content, "item")
    assert (tmp_path / "item_1.yaml").exists()
