# SQLite 写入 / SQLite Writer
# 两层 ID：YAML id（模板引用）→ DB id（UUID，自动生成）
# 写入顺序：先写被引用方，收集映射，再写引用方

import json
import sqlite3
import uuid

# 表定义 / Table definitions (AIGameWorld schema)
_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS scenes (
        id TEXT PRIMARY KEY, name TEXT, type TEXT, description TEXT,
        exits_json TEXT, landmarks_json TEXT, environment_json TEXT, pack_name TEXT
    );
    CREATE TABLE IF NOT EXISTS items (
        id TEXT PRIMARY KEY, name TEXT, item_type TEXT, rarity TEXT,
        weight REAL, value INTEGER, description TEXT, data_json TEXT, pack_name TEXT
    );
    CREATE TABLE IF NOT EXISTS scene_objects (
        id TEXT PRIMARY KEY, name TEXT, object_type TEXT,
        scene_id TEXT, position_x INTEGER, position_y INTEGER,
        interactable INTEGER, interact_data_json TEXT
    );
    CREATE TABLE IF NOT EXISTS player_characters (
        id TEXT PRIMARY KEY, name TEXT, role TEXT, race TEXT,
        status TEXT, scene_id TEXT, position_x INTEGER, position_y INTEGER,
        attributes_json TEXT, combat_json TEXT, character_arc_json TEXT,
        long_term_goal TEXT, values_json TEXT, personality TEXT,
        equipment_json TEXT, inventory_json TEXT, memory_count INTEGER,
        importance_accumulator REAL, relationships_json TEXT, joined_tick INTEGER
    );
    CREATE TABLE IF NOT EXISTS actors (
        id TEXT PRIMARY KEY, name TEXT, role TEXT, race TEXT,
        status TEXT, scene_id TEXT, position_x INTEGER, position_y INTEGER,
        attributes_json TEXT, combat_json TEXT, personality TEXT,
        functions_json TEXT, function_data_json TEXT, equipment_json TEXT,
        inventory_json TEXT, memory_count INTEGER, importance_accumulator REAL,
        relationships_json TEXT
    );
    CREATE TABLE IF NOT EXISTS story_arcs (
        id TEXT PRIMARY KEY, type TEXT, title TEXT, stage TEXT,
        main_cast_json TEXT, supporting_actors_json TEXT,
        key_event_ticks_json TEXT, branching_points_json TEXT, status TEXT
    );
    CREATE TABLE IF NOT EXISTS story_hooks (
        id TEXT PRIMARY KEY, planted_tick INTEGER, description TEXT,
        intended_payoff TEXT, urgency INTEGER, status TEXT
    );
"""


def _ensure_tables(conn: sqlite3.Connection):
    """确保表存在 / Ensure tables exist (CREATE IF NOT EXISTS)."""
    conn.executescript(_TABLE_DDL)


def write_template(db_path: str, data: dict, pack_name: str = "forgotten_realm") -> int:
    """写入模板数据到 SQLite。
    顺序：scenes → items → scene_objects → player_characters → actors → story_arcs → story_hooks
    维护 YAML id → DB id 映射，确保 FK 引用正确。
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_tables(conn)
    id_map: dict[str, str] = {}  # YAML id → DB id
    written = 0
    try:
        conn.execute("BEGIN")
        clear_existing(conn)

        # 1. 先写被引用方 / Write referenced entities first
        written, id_map = _write_scenes(conn, data.get("scenes", []), pack_name, id_map)
        w, id_map = _write_items(conn, data.get("items", []), pack_name, id_map)
        written += w

        # 2. 再写引用方（使用 DB id 做 FK）/ Then write dependents using DB FK ids
        w = _write_scene_objects(conn, data.get("scene_objects", []), id_map)
        written += w
        w = _write_pcs(conn, data.get("player_characters", []), id_map)
        written += w
        w = _write_actors(conn, data.get("actors", []), id_map)
        written += w

        # 3. 剧情 / Story setup
        _write_story_arcs(conn, data.get("story_setup", {}).get("arcs", []))
        _write_story_hooks(conn, data.get("story_setup", {}).get("hooks", []))

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return written


# 清空旧数据 / Clear existing rows
def clear_existing(conn: sqlite3.Connection):
    for t in ["story_hooks", "story_arcs", "actors", "player_characters", "scene_objects", "items", "scenes"]:
        conn.execute(f"DELETE FROM {t}")


# ID 工具 / ID helper
def _new_id(yaml_id: str) -> str:
    """生成 DB id / Generate DB id from YAML id prefix."""
    return f"{yaml_id}_{uuid.uuid4().hex[:8]}"


# ---- 写入各实体 / Write individual entities ----
def _write_scenes(conn, scenes, pack_name, id_map):
    n = 0
    stmt = (
        "INSERT INTO scenes (id,name,type,description,"
        "exits_json,landmarks_json,environment_json,pack_name) "
        "VALUES (?,?,?,?,?,?,?,?)"
    )
    for s in scenes:
        db_id = _new_id(s.get("id", "scene"))
        id_map[s.get("id", "")] = db_id
        conn.execute(
            stmt,
            (
                db_id,
                s.get("name", ""),
                s.get("type", ""),
                s.get("description", ""),
                _json_default(s.get("exits"), "[]"),
                _json_default(s.get("landmarks"), "[]"),
                _json_default(s.get("environment"), "{}"),
                pack_name,
            ),
        )
        n += 1
    return n, id_map


def _write_items(conn, items, pack_name, id_map):
    n = 0
    stmt = (
        "INSERT INTO items (id,name,item_type,rarity,weight,value,description,data_json,pack_name) "
        "VALUES (?,?,?,?,?,?,?,?,?)"
    )
    for i in items:
        db_id = _new_id(i.get("id", "item"))
        id_map[i.get("id", "")] = db_id
        conn.execute(
            stmt,
            (
                db_id,
                i.get("name", ""),
                i.get("item_type", ""),
                i.get("rarity", "common"),
                i.get("weight", 0),
                i.get("value", 0),
                i.get("description", ""),
                _json_default(i.get("data"), "{}"),
                pack_name,
            ),
        )
        n += 1
    return n, id_map


def _write_scene_objects(conn, objects, id_map):
    n = 0
    stmt = (
        "INSERT INTO scene_objects "
        "(id,name,object_type,scene_id,position_x,position_y,interactable,interact_data_json) "
        "VALUES (?,?,?,?,?,?,?,?)"
    )
    for o in objects:
        db_id = _new_id(o.get("id", "obj"))
        # YAML scene_id → DB scene_id
        scene_db_id = id_map.get(o.get("scene_id", ""), o.get("scene_id", ""))
        conn.execute(
            stmt,
            (
                db_id,
                o.get("name", ""),
                o.get("object_type", ""),
                scene_db_id,
                0,
                0,
                1,
                _json(o.get("interact_data")),
            ),
        )
        n += 1
    return n


# ---- 角色写入 / Character insert ----
def _write_pcs(conn, pcs, id_map):
    n = 0
    stmt = (
        "INSERT INTO player_characters "
        "(id,name,role,race,status,scene_id,position_x,position_y,"
        "attributes_json,combat_json,character_arc_json,long_term_goal,"
        "values_json,personality,equipment_json,inventory_json,"
        "memory_count,importance_accumulator,relationships_json,joined_tick) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    for pc in pcs:
        arc = pc.get("character_arc", {}) or {}
        db_id = _new_id(pc.get("id", "pc"))
        id_map[pc.get("id", "")] = db_id
        # YAML scene_id → DB scene_id
        scene_db_id = id_map.get(pc.get("starting_scene", "")) or _get_first(conn, "scenes")
        conn.execute(
            stmt,
            (
                db_id,
                pc.get("name", ""),
                pc.get("role", ""),
                pc.get("race", ""),
                "active",
                pc.get("scene_id", "") or scene_db_id,
                0,
                0,
                _json_default(pc.get("attributes"), "{}"),
                _json(pc.get("combat")),
                _json_default(pc.get("character_arc"), "{}"),
                arc.get("goal", ""),
                "[]",
                pc.get("personality", ""),
                _json_default(pc.get("equipment"), "{}"),
                _json_default(pc.get("inventory"), "[]"),
                0,
                0,
                "{}",
                0,
            ),
        )
        n += 1
    return n


# ---- NPC 写入 / Actor insert ----
def _write_actors(conn, actors, id_map):
    n = 0
    scene_db_id = _get_first(conn, "scenes")
    stmt = (
        "INSERT INTO actors "
        "(id,name,role,race,status,scene_id,position_x,position_y,"
        "attributes_json,combat_json,personality,functions_json,function_data_json,"
        "equipment_json,inventory_json,memory_count,importance_accumulator,"
        "relationships_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    for a in actors:
        db_id = _new_id(a.get("id", "actor"))
        id_map[a.get("id", "")] = db_id
        conn.execute(
            stmt,
            (
                db_id,
                a.get("name", ""),
                a.get("role", ""),
                a.get("race", ""),
                "active",
                scene_db_id,
                0,
                0,
                _json_default(a.get("attributes"), "{}"),
                _json(a.get("combat")),
                a.get("personality", ""),
                _json_default(a.get("functions"), "[]"),
                _json(a.get("function_data")),
                _json_default(a.get("equipment"), "{}"),
                _json_default(a.get("inventory"), "[]"),
                0,
                0,
                "{}",
            ),
        )
        n += 1
    return n


# ---- 剧情 / Story ----
def _write_story_arcs(conn, arcs):
    stmt = (
        "INSERT INTO story_arcs "
        "(id,type,title,stage,main_cast_json,supporting_actors_json,"
        "key_event_ticks_json,branching_points_json,status) "
        "VALUES (?,?,?,?,?,?,?,?,?)"
    )
    for arc in arcs:
        conn.execute(
            stmt,
            (
                _new_id("arc"),
                arc.get("type", "main"),
                arc.get("title", ""),
                arc.get("stage", "hook"),
                _json_default(arc.get("main_cast"), "[]"),
                "[]",
                "[]",
                _json_default(arc.get("branching_points"), "[]"),
                "setup",
            ),
        )


# ---- 剧情钩子 / Story hooks ----
def _write_story_hooks(conn, hooks):
    urgency_map = {"low": 3, "medium": 5, "high": 8}
    stmt = "INSERT INTO story_hooks (id,planted_tick,description,intended_payoff,urgency,status) VALUES (?,?,?,?,?,?)"
    for h in hooks:
        conn.execute(
            stmt,
            (
                _new_id("hook"),
                0,
                h.get("description", ""),
                h.get("intended_payoff", ""),
                urgency_map.get(h.get("urgency", "medium"), 5),
                "planted",
            ),
        )


# JSON 序列化辅助 / JSON serialization helpers
def _get_first(conn, table):
    """获取表第一条记录的 id / Get first row id."""
    row = conn.execute(f"SELECT id FROM {table} LIMIT 1").fetchone()
    return row[0] if row else ""


def _json(obj):
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False)


def _json_default(obj, default="{}"):
    if obj is None:
        return default
    return json.dumps(obj, ensure_ascii=False)
