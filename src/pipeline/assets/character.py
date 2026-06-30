# 角色精灵生成 / Character Sprite Generation
# src/pipeline/assets/character.py
# src/pipeline/assets/character.py
# src/pipeline/assets/character.py
#   管线 A: Recolor 换色 — 基于 character_base.png，Pillow 按 race/role 替换肤色和服装色
#   管线 B: AI 生成  — Replicate SDXL / OpenAI DALL-E

from pathlib import Path

from src.pipeline.world_pack.params import GenerateParams

# 肤色映射 / Skin color → RGB
SKIN_COLORS = {
    "human": (245, 203, 167),
    "elf": (253, 235, 208),
    "night_elf": (180, 160, 220),
    "dwarf": (212, 165, 116),
    "orc": (107, 142, 35),
    "undead": (189, 195, 199),
}

# 职业配色 / Role color → RGB
ROLE_COLORS = {
    "fighter": (180, 50, 50),
    "warrior": (180, 50, 50),
    "rogue": (50, 50, 50),
    "ranger": (34, 139, 34),
    "cleric": (255, 255, 255),
    "paladin": (218, 165, 32),
    "wizard": (100, 50, 180),
    "mage": (100, 50, 180),
    "blacksmith": (139, 90, 43),
    "guard": (100, 120, 150),
    "merchant": (50, 150, 50),
    "innkeeper": (100, 80, 60),
}

# 基础模板路径 / Base template path
_BASE_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "assets" / "character_base.png"

# 默认肤色 + 容差 / Default skin color + tolerance
_DEFAULT_SKIN = (245, 203, 167)
_TOLERANCE = 40


def generate_character_sprites(
    characters: list[dict],
    output_dir: Path,
    method: str = "recolor",
    params: GenerateParams | None = None,
) -> dict[str, Path]:
    """生成角色精灵 / Generate character sprites."""
    sprites_dir = output_dir / "sprites"
    sprites_dir.mkdir(parents=True, exist_ok=True)

    if method == "ai":
        from src.pipeline.ai_assets.character import generate_ai_sprites

        return generate_ai_sprites(characters, sprites_dir, params)

    return _generate_recolor(characters, sprites_dir)


def _generate_recolor(characters: list[dict], sprites_dir: Path) -> dict[str, Path]:
    """基于基础模板换色生成精灵 / Generate sprites by recoloring base template."""
    if not _BASE_TEMPLATE.exists():
        raise FileNotFoundError(f"基础模板不存在: {_BASE_TEMPLATE}")

    from PIL import Image

    base = Image.open(_BASE_TEMPLATE).convert("RGBA")
    result: dict[str, Path] = {}

    for char in characters:
        char_id = char.get("id", f"unknown_{len(result)}")
        race = (char.get("race") or "").lower()
        role = (char.get("role") or "").lower()

        skin = SKIN_COLORS.get(race, SKIN_COLORS["human"])
        cloth = ROLE_COLORS.get(role, (128, 128, 128))

        sprite = _recolor(base.copy(), skin, cloth)
        path = sprites_dir / f"{char_id}.png"
        sprite.save(path)
        result[char_id] = path

    return result


def _recolor(img, skin: tuple, cloth: tuple):
    """对 32×32 像素精灵换色 / Recolor a 32x32 pixel sprite."""
    pixels = img.load()
    width, height = img.size

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue

            if _match((r, g, b), _DEFAULT_SKIN, _TOLERANCE):
                pixels[x, y] = (*skin, a)
            else:
                blended = _blend((r, g, b), cloth)
                pixels[x, y] = (*blended, a)

    return img


def _match(c1: tuple, c2: tuple, tol: int) -> bool:
    return all(abs(c1[i] - c2[i]) <= tol for i in range(3))


def _blend(original: tuple, target: tuple, factor: float = 0.6) -> tuple:
    return tuple(int(original[i] * (1 - factor) + target[i] * factor) for i in range(3))
