# 角色精灵测试 / Character sprite tests

from pathlib import Path

from PIL import Image

from src.pipeline.assets.character import (
    _BASE_TEMPLATE,
    ROLE_COLORS,
    SKIN_COLORS,
    _recolor,
    generate_character_sprites,
)


def _create_test_image() -> Image.Image:
    """创建测试用 32×32 像素小人 / Create test 32x32 sprite."""
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    pixels = img.load()
    assert pixels is not None
    for y in range(4, 12):
        for x in range(12, 22):
            pixels[x, y] = (245, 203, 167, 255)
    for y in range(12, 24):
        for x in range(10, 24):
            pixels[x, y] = (100, 100, 100, 255)
    return img


def test_base_template_exists():
    assert _BASE_TEMPLATE.exists()


def test_recolor_changes_skin(tmp_path: Path):
    img = _create_test_image()
    skin_color = SKIN_COLORS["dwarf"]
    cloth_color = ROLE_COLORS["fighter"]
    result = _recolor(img, skin_color, cloth_color)
    pixel = result.getpixel((16, 8))
    assert isinstance(pixel, tuple)
    assert pixel[:3] == skin_color


def test_recolor_changes_cloth(tmp_path: Path):
    img = _create_test_image()
    skin_color = SKIN_COLORS["human"]
    cloth_color = ROLE_COLORS["rogue"]
    result = _recolor(img, skin_color, cloth_color)
    pixel = result.getpixel((16, 18))
    assert isinstance(pixel, tuple)
    assert pixel[0] < 80


def test_recolor_preserves_alpha(tmp_path: Path):
    img = _create_test_image()
    result = _recolor(img, SKIN_COLORS["elf"], ROLE_COLORS["wizard"])
    pixel = result.getpixel((0, 0))
    assert isinstance(pixel, tuple)
    assert pixel[3] == 0


def test_generate_character_sprites(tmp_path: Path):
    """generate_character_sprites 生成文件 / Generates sprite files."""
    chars = [
        {"id": "pc1", "name": "战士", "race": "human", "role": "fighter"},
        {"id": "pc2", "name": "法师", "race": "elf", "role": "wizard"},
    ]
    result = generate_character_sprites(chars, tmp_path, method="recolor")

    assert len(result) == 2
    assert (tmp_path / "sprites/pc1.png").exists()
    assert (tmp_path / "sprites/pc2.png").exists()

    # 验证尺寸 / Verify size
    img1 = Image.open(tmp_path / "sprites/pc1.png")
    assert img1.size == (32, 32)


def test_generate_sprites_empty_list(tmp_path: Path):
    """空角色列表不报错 / Empty list does not crash."""
    result = generate_character_sprites([], tmp_path, method="recolor")
    assert len(result) == 0


def test_skin_color_count():
    """肤色映射覆盖常见种族 / Skin colors cover common races."""
    assert len(SKIN_COLORS) >= 5
    assert "human" in SKIN_COLORS
    assert "elf" in SKIN_COLORS
    assert "dwarf" in SKIN_COLORS


def test_role_color_count():
    """职业配色覆盖常见角色 / Role colors cover common roles."""
    assert len(ROLE_COLORS) >= 10
    assert "fighter" in ROLE_COLORS
    assert "rogue" in ROLE_COLORS
    assert "wizard" in ROLE_COLORS
