# DB Writer 单元测试 / Unit tests for DB Writer

import sqlite3

from src.loader.db_writer import write_template


def _create_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scenes (id TEXT, name TEXT, type TEXT, description TEXT,
            exits_json TEXT, landmarks_json TEXT, environment_json TEXT);
        CREATE TABLE IF NOT EXISTS items (id TEXT, name TEXT, item_type TEXT,
            rarity TEXT, weight REAL, value INTEGER, data_json TEXT);
        CREATE TABLE IF NOT EXISTS scene_objects (id TEXT, name TEXT, object_type TEXT,
            scene_id TEXT, interact_data_json TEXT);
        CREATE TABLE IF NOT EXISTS player_characters (id TEXT, name TEXT, role TEXT,
            race TEXT, personality TEXT, attributes_json TEXT, combat_json TEXT,
            character_arc_json TEXT, equipment_json TEXT, inventory_json TEXT);
        CREATE TABLE IF NOT EXISTS actors (id TEXT, name TEXT, role TEXT, race TEXT,
            personality TEXT, attributes_json TEXT, combat_json TEXT,
            functions_json TEXT, function_data_json TEXT, equipment_json TEXT, inventory_json TEXT);
        CREATE TABLE IF NOT EXISTS story_arcs (type TEXT, title TEXT, stage TEXT,
            main_cast_json TEXT, branching_points_json TEXT);
        CREATE TABLE IF NOT EXISTS story_hooks (description TEXT, urgency TEXT);
    """)


def test_write_scenes(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    _create_tables(conn)
    conn.close()

    data = {"scenes": [{"id": "s1", "name": "Tavern", "type": "indoor",
                         "description": "Warm", "exits": [], "landmarks": []}]}
    n = write_template(db_path, data)
    assert n == 1

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id, name, type FROM scenes").fetchone()
    assert row[0] == "s1"
    conn.close()


def test_write_full_template(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    _create_tables(conn)
    conn.close()

    data = {
        "scenes": [{"id": "s1", "name": "Tavern", "type": "indoor", "description": "x", "exits": [], "landmarks": []}],
        "items": [{"id": "sword", "name": "Sword", "item_type": "weapon"}],
        "scene_objects": [{"id": "c1", "name": "Chest", "object_type": "chest", "scene_id": "s1"}],
        "player_characters": [{"id": "pc1", "name": "Hero", "role": "warrior", "race": "human", "personality": ""}],
        "actors": [{"id": "a1", "name": "NPC", "role": "guard", "race": "human", "personality": ""}],
        "story_setup": {"arcs": [{"title": "Arc"}], "hooks": [{"description": "Hook"}]},
    }
    n = write_template(db_path, data)
    assert n >= 6
