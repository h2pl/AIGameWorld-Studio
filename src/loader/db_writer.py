# SQLite 写入 / SQLite Writer
# YAML dict → SQLite 15 表，6 表同一事务

import json
import sqlite3


def write_template(db_path: str, data: dict) -> int:
    """写入模板数据到 SQLite / Write template data to SQLite.

    Returns: 总写入行数 / Total rows written.
    """
    conn = sqlite3.connect(db_path)
    written = 0
    try:
        conn.execute("BEGIN")
        clear_existing(conn)
        written += _write_scenes(conn, data.get("scenes", []))
        written += _write_items(conn, data.get("items", []))
        written += _write_scene_objects(conn, data.get("scene_objects", []))
        written += _write_pcs(conn, data.get("player_characters", []))
        written += _write_actors(conn, data.get("actors", []))
        written += _write_story_arcs(conn, data.get("story_setup", {}).get("arcs", []))
        written += _write_story_hooks(conn, data.get("story_setup", {}).get("hooks", []))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return written


def clear_existing(conn: sqlite3.Connection):
    """清空旧数据 / Clear old data."""
    tables = ["story_hooks", "story_arcs", "actors", "player_characters",
              "scene_objects", "items", "scenes"]
    for table in tables:
        conn.execute(f"DELETE FROM {table}")


def _write_scenes(conn: sqlite3.Connection, scenes: list[dict]) -> int:
    written = 0
    for s in scenes:
        conn.execute(
            """INSERT INTO scenes (id, name, type, description, exits_json, landmarks_json, environment_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (s.get("id", ""), s.get("name", ""), s.get("type", ""), s.get("description", ""),
             _json(s.get("exits")), _json(s.get("landmarks")), _json(s.get("environment"))),
        )
        written += 1
    return written


def _write_items(conn: sqlite3.Connection, items: list[dict]) -> int:
    written = 0
    for i in items:
        conn.execute(
            """INSERT INTO items (id, name, item_type, rarity, weight, value, data_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (i.get("id", ""), i.get("name", ""), i.get("item_type", ""),
             i.get("rarity", ""), i.get("weight", 0.0), i.get("value", 0),
             _json(i.get("data"))),
        )
        written += 1
    return written


def _write_scene_objects(conn: sqlite3.Connection, objects: list[dict]) -> int:
    written = 0
    for o in objects:
        conn.execute(
            """INSERT INTO scene_objects (id, name, object_type, scene_id, interact_data_json)
               VALUES (?, ?, ?, ?, ?)""",
            (o.get("id", ""), o.get("name", ""), o.get("object_type", ""),
             o.get("scene_id", ""), _json(o.get("interact_data"))),
        )
        written += 1
    return written


def _write_pcs(conn: sqlite3.Connection, pcs: list[dict]) -> int:
    written = 0
    for pc in pcs:
        conn.execute(
            """INSERT INTO player_characters
               (id, name, role, race, personality, attributes_json, combat_json,
                character_arc_json, equipment_json, inventory_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pc.get("id", ""), pc.get("name", ""), pc.get("role", ""),
             pc.get("race", ""), pc.get("personality", ""),
             _json(pc.get("attributes")), _json(pc.get("combat")),
             _json(pc.get("character_arc")), _json(pc.get("equipment")),
             _json(pc.get("inventory"))),
        )
        written += 1
    return written


def _write_actors(conn: sqlite3.Connection, actors: list[dict]) -> int:
    written = 0
    for a in actors:
        conn.execute(
            """INSERT INTO actors
               (id, name, role, race, personality, attributes_json, combat_json,
                functions_json, function_data_json, equipment_json, inventory_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (a.get("id", ""), a.get("name", ""), a.get("role", ""),
             a.get("race", ""), a.get("personality", ""),
             _json(a.get("attributes")), _json(a.get("combat")),
             _json(a.get("functions")), _json(a.get("function_data")),
             _json(a.get("equipment")), _json(a.get("inventory"))),
        )
        written += 1
    return written


def _write_story_arcs(conn: sqlite3.Connection, arcs: list[dict]) -> int:
    written = 0
    for arc in arcs:
        conn.execute(
            """INSERT INTO story_arcs (type, title, stage, main_cast_json, branching_points_json)
               VALUES (?, ?, ?, ?, ?)""",
            (arc.get("type", "main"), arc.get("title", ""), arc.get("stage", "hook"),
             _json(arc.get("main_cast")), _json(arc.get("branching_points"))),
        )
        written += 1
    return written


def _write_story_hooks(conn: sqlite3.Connection, hooks: list[dict]) -> int:
    written = 0
    for hook in hooks:
        conn.execute(
            """INSERT INTO story_hooks (description, urgency)
               VALUES (?, ?)""",
            (hook.get("description", ""), hook.get("urgency", "medium")),
        )
        written += 1
    return written


def _json(obj):
    """序列化为 JSON 字符串，保留中文."""
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False)
