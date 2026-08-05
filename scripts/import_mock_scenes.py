"""批量导入场景地图到 AIGameWorld mock.db / Batch import scene maps into AIGameWorld mock.db.

用法 / Usage:
    python scripts/import_mock_scenes.py [map1.tmx map2.tmx ...]
    # 不传参数则导入默认 mock 场景地图
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from services.generator.gen_scene import (
    gen_scene,
    upsert_actors,
    upsert_scene,
    upsert_scene_objects,
)

# 目标 DB：AIGameWorld mock.db（mock 模式专用）
MOCK_DB = Path(__file__).parent.parent.parent / "AIGameWorld" / "backend" / "data" / "mock.db"
STUDIO_TMX_DIR = Path(__file__).parent.parent / "templates" / "assets" / "maps" / "tuxemon" / "maps"

# 默认 mock 场景地图 / Default mock scene maps
DEFAULT_MAPS = [
    "azure_town.tmx",
    "taba_town.tmx",
    "water_cathedral.tmx",
    "spyder_cotton_tunnel.tmx",
]


def import_scene(tmx_path: Path) -> dict:
    """生成并写入单个场景 / Generate and write one scene."""
    rec = gen_scene(str(tmx_path))
    try:
        scid = upsert_scene(rec, db_path=str(MOCK_DB))
    except Exception:
        scid = rec["id"]
    # 同步写入 NPC Actor + 可交互物体 / Sync actors & scene objects
    upsert_actors(scid, rec["world_id"], rec.get("_npcs", []), rec.get("_spawns", []), db_path=str(MOCK_DB))
    upsert_scene_objects(scid, rec["world_id"], rec.get("_interactables", []), db_path=str(MOCK_DB))
    return rec


def main():
    # 解析地图清单 / Parse map list
    if len(sys.argv) > 1:
        maps = sys.argv[1:]
    else:
        maps = DEFAULT_MAPS

    print(f"目标 DB: {MOCK_DB}")
    for m in maps:
        tmx = STUDIO_TMX_DIR / m
        if not tmx.exists():
            print(f"  [跳过] 找不到 {tmx}")
            continue
        rec = import_scene(tmx)
        ext = json.loads(rec["ext_json"])
        n_npc = len(rec.get("_npcs", []))
        n_obj = len(rec.get("_interactables", []))
        print(
            f"  [OK] {rec['id']} → {rec['name']} ({rec['type']} {rec['map_width']}×{rec['map_height']}) "
            f"tilesets={len(ext.get('tilesets', []))} npc={n_npc} obj={n_obj}"
        )
    print("完成 / Done")


if __name__ == "__main__":
    main()
