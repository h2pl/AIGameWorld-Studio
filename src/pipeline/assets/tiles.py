# 瓦片集生成 / Tileset Generation
# src/pipeline/assets/tiles.py
# src/pipeline/assets/tiles.py
# src/pipeline/assets/tiles.py
# 按场景类型选配 tileset → tiles.png + tiles.json

from pathlib import Path

from src.pipeline.world_pack.params import GenerateParams

# 场景类型 → 瓦片集样式 / Scene type → tileset style
TILESET_STYLES = {
    "village": "village",
    "indoor": "indoor",
    "outdoor": "outdoor",
    "dungeon": "dungeon",
    "urban": "village",
    "wilderness": "outdoor",
    "underground": "dungeon",
}

# 基础瓦片目录 / Base tiles directory
_TILES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates" / "assets"


def generate_tileset(
    scenes: list[dict],
    output_dir: Path,
    method: str = "download",
    params: GenerateParams | None = None,
) -> dict[str, Path]:
    """生成瓦片集 / Generate tileset.

    Args:
        scenes: Scene 列表，每项含 {id, type}
        output_dir: world-pack 目录
        method: "download" | "ai" | "skip"
    """
    if method == "skip":
        return {}

    result: dict[str, Path] = {}

    # 收集场景类型 / Collect scene types
    scene_types = {s.get("type", "indoor") for s in scenes}

    # 选配瓦片集 / Select tilesets
    for stype in scene_types:
        style = TILESET_STYLES.get(stype, "indoor")  # noqa

        # TODO: 从 _TILES_DIR 复制对应 tileset 或 AI 生成
        # TODO: 生成 tiles.json 索引

    return result
