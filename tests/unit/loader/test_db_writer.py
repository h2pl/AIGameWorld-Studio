# DB Writer 单元测试 / Unit tests

import sqlite3

from src.loader.db_writer import write_template


def _create_tables(conn):
    conn.executescript("""
        CREATE TABLE scenes (id TEXT, name TEXT, type TEXT, description TEXT,
            exits_json TEXT, landmarks_json TEXT, environment_json TEXT, pack_name TEXT);
        CREATE TABLE items (id TEXT, name TEXT, item_type TEXT, rarity TEXT,
            weight REAL, value INTEGER, description TEXT, data_json TEXT, pack_name TEXT);
        CREATE TABLE scene_objects (id TEXT, name TEXT, object_type TEXT,
            scene_id TEXT, position_x INTEGER, position_y INTEGER, interactable INTEGER,
            interact_data_json TEXT);
        CREATE TABLE player_characters (id TEXT, name TEXT, role TEXT, race TEXT,
            status TEXT, scene_id TEXT, position_x INTEGER, position_y INTEGER,
            attributes_json TEXT, combat_json TEXT, character_arc_json TEXT,
            long_term_goal TEXT, values_json TEXT, personality TEXT,
            equipment_json TEXT, inventory_json TEXT, memory_count INTEGER,
            importance_accumulator REAL, relationships_json TEXT, joined_tick INTEGER);
        CREATE TABLE actors (id TEXT, name TEXT, role TEXT, race TEXT,
            status TEXT, scene_id TEXT, position_x INTEGER, position_y INTEGER,
            attributes_json TEXT, combat_json TEXT, personality TEXT,
            functions_json TEXT, function_data_json TEXT, equipment_json TEXT,
            inventory_json TEXT, memory_count INTEGER, importance_accumulator REAL,
            relationships_json TEXT);
        CREATE TABLE story_arcs (id TEXT, type TEXT, title TEXT, stage TEXT,
            main_cast_json TEXT, supporting_actors_json TEXT,
            key_event_ticks_json TEXT, branching_points_json TEXT, status TEXT);
        CREATE TABLE story_hooks (id TEXT, planted_tick INTEGER, description TEXT,
            intended_payoff TEXT, urgency INTEGER, status TEXT);
    """)


def _setup_db(path):
    conn = sqlite3.connect(path)
    _create_tables(conn)
    conn.close()


def test_write_scenes(tmp_path):
    db = str(tmp_path / "t.db")
    _setup_db(db)
    n = write_template(db, {"scenes": [{"id": "s1", "name": "T", "type": "indoor", "description": "x"}]})
    assert n == 1
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT id, name FROM scenes").fetchone()
    assert row[0].startswith("s1_")  # DB id = s1_<uuid>
    conn.close()


def test_write_full_template(tmp_path):
    db = str(tmp_path / "t.db")
    _setup_db(db)
    data = {
        "scenes": [{"id": "s1", "name": "T", "type": "indoor", "description": "x"}],
        "items": [{"id": "sword", "name": "S", "item_type": "weapon"}],
        "scene_objects": [{"id": "c1", "name": "C", "object_type": "chest", "scene_id": "s1"}],
        "player_characters": [{"id": "p1", "name": "Hero", "role": "warrior", "race": "human",
                                "personality": "", "scene_id": "s1", "attributes": {}}],
        "actors": [{"id": "a1", "name": "NPC", "role": "guard", "race": "human", "personality": ""}],
        "story_setup": {"arcs": [{"title": "Arc"}], "hooks": [{"description": "Hook"}]},
    }
    n = write_template(db, data)
    assert n == 5  # scenes + items + scene_objects + pcs + actors
