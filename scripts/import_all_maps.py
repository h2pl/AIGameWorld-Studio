"""批量导入 Studio 所有 TMX 地图到 AIGameWorld mock.db / Import all Studio TMX maps into mock.db.

用法 / Usage:
    python scripts/import_all_maps.py

遍历 templates/assets/maps/tuxemon/maps/ 下所有 .tmx，
逐张调用 gen_scene 生成并写入 mock.db（world_id 保持 gen_scene 默认值，待后续分配）。
跳过解析失败的地图并记录到 import_report.txt。
"""

import sys
from pathlib import Path

# 将项目 src 加入导入路径，便于直接 import services.*
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from services.generator.gen_scene import (
    gen_scene,
    upsert_actors,
    upsert_scene,
    upsert_scene_objects,
)

MOCK_DB = Path(__file__).parent.parent.parent / "AIGameWorld" / "backend" / "data" / "mock.db"
STUDIO_TMX_DIR = Path(__file__).parent.parent / "templates" / "assets" / "maps" / "tuxemon" / "maps"
REPORT = Path(__file__).parent / "import_report.txt"


def main():
    tmx_files = sorted(STUDIO_TMX_DIR.glob("*.tmx"))
    print(f"共 {len(tmx_files)} 张地图 / Total {len(tmx_files)} maps")
    print(f"目标 DB: {MOCK_DB}")

    ok, fail = 0, 0
    lines = []
    for tmx in tmx_files:
        try:
            # 解析 TMX 并生成场景记录
            rec = gen_scene(str(tmx))
            scid = upsert_scene(rec, db_path=str(MOCK_DB))
            upsert_actors(scid, rec["world_id"], rec.get("_npcs", []), rec.get("_spawns", []), db_path=str(MOCK_DB))
            upsert_scene_objects(scid, rec["world_id"], rec.get("_interactables", []), db_path=str(MOCK_DB))
            n_npc = len(rec.get("_npcs", [])) + len(rec.get("_spawns", []))
            n_obj = len(rec.get("_interactables", []))
            ok += 1
            lines.append(f"[OK] {rec['id']} | {rec['name']} | type={rec['type']} | npc={n_npc} obj={n_obj}")
            print(f"  [OK] {rec['id']} npc={n_npc} obj={n_obj}")
        except Exception as e:
            fail += 1
            lines.append(f"[FAIL] {tmx.name} | {str(e)[:200]}")
            print(f"  [FAIL] {tmx.name}: {str(e)[:120]}")

    # 写出导入报告（成功/失败清单）
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(f"total={len(tmx_files)} ok={ok} fail={fail}\n")
        f.write("\n".join(lines) + "\n")
    print(f"\n完成 / Done: ok={ok} fail={fail}，报告见 {REPORT}")


if __name__ == "__main__":
    main()
