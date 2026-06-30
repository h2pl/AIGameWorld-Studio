# World Pack Viewer / 世界包查看器
# 支持两种数据源：YAML 目录 / SQLite DB

import json
import sqlite3
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "serve"
_JINJA = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))

SOURCE_DIR = Path("world-packs/custom")
DB_PATH: str | None = None

RARITY_COLORS = {
    "common": "#9ca3af",
    "uncommon": "#22c55e",
    "rare": "#3b82f6",
    "epic": "#a855f7",
    "legendary": "#f59e0b",
}


# ═══════════════════════════════════════════════════════════════
# YAML 数据源
# ═══════════════════════════════════════════════════════════════


def _load_yaml(path: Path) -> dict | None:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text("utf-8"))


def _load_yaml_all(dir_path: Path) -> list[dict]:
    if not dir_path.exists():
        return []
    return [d for f in sorted(dir_path.glob("*.yaml")) if isinstance(d := yaml.safe_load(f.read_text("utf-8")), dict)]


# ═══════════════════════════════════════════════════════════════
# DB 数据源
# ═══════════════════════════════════════════════════════════════


def _db_query(sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _db_meta():
    rows = _db_query("SELECT key, value FROM world_meta")
    return {
        "id": "db",
        "name": next((r["value"] for r in rows if r["key"] == "pack_name"), "DB View"),
        "description": "Imported runtime data",
    }


def _db_chars(table: str) -> list[dict]:
    rows = _db_query(
        f"SELECT id, name, role, race, scene_id, personality, attributes_json, combat_json, character_arc_json, \
        equipment_json, functions_json, function_data_json FROM {table}"
    )
    result = []
    for r in rows:
        # JSON 解析
        d = dict(r)
        for k in (
            "attributes_json",
            "combat_json",
            "equipment_json",
            "character_arc_json",
            "functions_json",
            "function_data_json",
        ):
            if d.get(k):
                try:
                    d[k.replace("_json", "")] = json.loads(d[k])
                # items
                except json.JSONDecodeError, TypeError:
                    pass
        d["attributes"] = d.get("attributes", {})
        d["combat"] = d.get("combat", {})
        d["equipment"] = d.get("equipment", {})
        d["character_arc"] = d.get("character_arc", {})
        d["functions"] = d.get("functions", [])
        d["function_data"] = d.get("function_data", {})
        result.append(d)
    # scenes
    return result


def _db_items() -> list[dict]:
    rows = _db_query("SELECT id, name, item_type, rarity, weight, value, description, data_json FROM items")
    result = []
    for r in rows:
        d = dict(r)
        if d.get("data_json"):
            try:
                d["data"] = json.loads(d["data_json"])
            except json.JSONDecodeError, TypeError:
                d["data"] = {}
        result.append(d)
    return result


def _db_scenes() -> list[dict]:
    rows = _db_query("SELECT id, name, type, description FROM scenes")
    return [dict(r) for r in rows]


def _db_objects() -> list[dict]:
    rows = _db_query("SELECT id, name, object_type, scene_id, interactable, interact_data_json FROM scene_objects")
    result = []
    for r in rows:
        d = dict(r)
        if d.get("interact_data_json"):
            try:
                d["interact_data"] = json.loads(d["interact_data_json"])
            except json.JSONDecodeError, TypeError:
                pass
        result.append(d)
    return result


def _db_story() -> dict | None:
    rows = _db_query("SELECT id, type, title, stage, main_cast_json FROM story_arcs")
    arcs = []
    for r in rows:
        d = dict(r)
        if d.get("main_cast_json"):
            try:
                d["main_cast"] = json.loads(d["main_cast_json"])
            except json.JSONDecodeError, TypeError:
                d["main_cast"] = []
        arcs.append(d)
    return {"arcs": arcs} if arcs else None


# ═══════════════════════════════════════════════════════════════
# Viewer
# ═══════════════════════════════════════════════════════════════


class ViewerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.rstrip("/") or "/"
        if path == "/":
            self._render_index()
        elif path.startswith("/pack/"):
            self._render_pack(path.split("/pack/")[1])
        else:
            self.send_error(404)

    def _render_index(self):
        if DB_PATH:
            meta = _db_meta()
            packs = [
                {
                    "id": "db",
                    "name": meta["name"],
                    "desc": meta["description"],
                    "total": sum(
                        1
                        for t in ("player_characters", "actors", "items", "scenes", "scene_objects")
                        for r in _db_query(f"SELECT id FROM {t}")
                    ),
                }
            ]
        else:
            packs = [
                {
                    "id": d.name,
                    "name": (_load_yaml(d / "meta.yaml") or {}).get("name", d.name),
                    "desc": (_load_yaml(d / "meta.yaml") or {}).get("description", ""),
                    "total": max(0, sum(1 for _ in d.rglob("*.yaml")) - 1),
                }
                for d in sorted(SOURCE_DIR.iterdir())
                if d.is_dir() and (d / "meta.yaml").exists()
            ]
        html = _JINJA.get_template("index.html").render(packs=packs)
        self._respond(html)

    def _render_pack(self, pack_id: str):
        if DB_PATH:
            meta = _db_meta()
            pcs = _db_chars("player_characters")
            actors = _db_chars("actors")
            items = _db_items()
            scenes = _db_scenes()
            objects = _db_objects()
            story = _db_story()
            lore = []
        else:
            pack_dir = SOURCE_DIR / pack_id
            meta = _load_yaml(pack_dir / "meta.yaml") or {}
            pcs = _load_yaml_all(pack_dir / "player_characters")
            actors = _load_yaml_all(pack_dir / "actors")
            items = _load_yaml_all(pack_dir / "items")
            scenes = _load_yaml_all(pack_dir / "scenes")
            objects = _load_yaml_all(pack_dir / "scene_objects")
            story = _load_yaml(pack_dir / "story_setup.yaml")
            lore = _load_yaml_all(pack_dir / "lore")

        html = _JINJA.get_template("pack.html").render(
            pack_id=pack_id,
            meta=meta,
            lore=lore,
            pcs=pcs,
            actors=actors,
            items=items,
            scenes=scenes,
            objects=objects,
            story=story,
            rarity_colors=RARITY_COLORS,
        )
        self._respond(html)

    def _respond(self, html: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def run(pack_dir: str = "world-packs/custom", db_path: str | None = None, host: str = "127.0.0.1", port: int = 8888):
    global SOURCE_DIR, DB_PATH
    if db_path:
        DB_PATH = db_path
        print(f"DB Mode: {db_path}")
    else:
        SOURCE_DIR = Path(pack_dir)
        print(f"YAML Mode: {SOURCE_DIR.resolve()}")

    url = f"http://{host}:{port}"
    print(f"World Pack Viewer: {url}")
    webbrowser.open(url)

    server = HTTPServer((host, port), ViewerHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
