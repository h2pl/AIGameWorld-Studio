# 场景布局生成 / Layout Generator
# src/pipeline/assets/layout.py
# src/pipeline/assets/layout.py
# src/pipeline/assets/layout.py
# 为每个 scene 生成 30×20 瓦片矩阵 + 地标位置 + 出口 → layout.json

import json
from pathlib import Path

COLS = 30
ROWS = 20

# 场景类型 → 瓦片集名称 / Scene type → tileset name
TILESET_MAP = {
    "village": "village",
    "indoor": "indoor",
    "outdoor": "outdoor",
    "dungeon": "dungeon",
    "urban": "village",
    "wilderness": "outdoor",
}

# 场景类型 → 默认瓦片填充 / Scene type → default tile fill
DEFAULT_TILE = {
    "village": 0,
    "indoor": 3,
    "outdoor": 0,
    "dungeon": 5,
    "urban": 1,
    "wilderness": 0,
}


def generate_layout(scenes: list[dict], output_dir: Path) -> dict[str, Path]:
    """生成场景布局 → layout.json."""
    layout_data = {"scenes": []}

    for scene in scenes:
        sid = scene.get("id", "unknown")
        stype = scene.get("type", "indoor")
        default = DEFAULT_TILE.get(stype, 0)
        tileset = TILESET_MAP.get(stype, "indoor")

        # 瓦片矩阵 / Tile matrix
        tile_map = [[default] * COLS for _ in range(ROWS)]

        # 地标 / Landmarks
        landmarks = scene.get("landmarks", [])
        lm_entries = []
        for i, lm in enumerate(landmarks):
            if isinstance(lm, dict) and lm.get("id"):
                x = (i + 1) * (COLS // max(len(landmarks) + 1, 2))
                y = ROWS // 2
                lm_entries.append({"id": lm["id"], "name": lm.get("name", ""), "x": x, "y": y})

        # 出口 / Exits
        exits = scene.get("exits", [])
        exit_entries = []
        for i, ex in enumerate(exits):
            if isinstance(ex, dict):
                target = ex.get("target_scene", ex.get("target", ""))
                label = ex.get("condition", "")
                if i == 0:
                    x, y = COLS - 1, ROWS // 2
                elif i == 1:
                    x, y = 0, ROWS // 2
                else:
                    x, y = COLS // 2, ROWS - 1
                exit_entries.append({"target": target, "x": x, "y": y, "label": label})
                tile_map[y][min(x, COLS - 1)] = 2  # 门

        layout_data["scenes"].append(
            {
                "scene_id": sid,
                "tileset": tileset,
                "cols": COLS,
                "rows": ROWS,
                "map": tile_map,
                "landmarks": lm_entries,
                "exits": exit_entries,
            }
        )

    path = output_dir / "layout.json"
    path.write_text(json.dumps(layout_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {str(path): path}
