# World Pack Viewer / 世界包查看器
# 基于 http.server + Jinja2，零依赖
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# 模板目录
import yaml
from jinja2 import Environment, FileSystemLoader

# pack 目录
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "serve"
_JINJA = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))

PACKS_DIR = Path("world-packs/custom")
# 稀有度颜色

RARITY_COLORS = {
    "common": "#9ca3af",
    "uncommon": "#22c55e",
    "rare": "#3b82f6",
    "epic": "#a855f7",
    "legendary": "#f59e0b",
}


def _load_yaml(path: Path) -> dict | None:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text("utf-8"))


def _load_all(dir_path: Path) -> list[dict]:
    if not dir_path.exists():
        return []
    return [d for f in sorted(dir_path.glob("*.yaml")) if isinstance(d := yaml.safe_load(f.read_text("utf-8")), dict)]


# HTML 响应


def _pack_summary(pack_dir: Path) -> dict:
    meta = _load_yaml(pack_dir / "meta.yaml") or {}
    total = sum(1 for _ in pack_dir.rglob("*.yaml")) - 1
    return {
        "id": pack_dir.name,
        "name": meta.get("name", pack_dir.name),
        "desc": meta.get("description", ""),
        "total": max(0, total),
    }


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
        packs = [_pack_summary(d) for d in sorted(PACKS_DIR.iterdir()) if d.is_dir() and (d / "meta.yaml").exists()]
        html = _JINJA.get_template("index.html").render(packs=packs)
        self._respond(html)

    def _render_pack(self, pack_id: str):
        pack_dir = PACKS_DIR / pack_id
        meta = _load_yaml(pack_dir / "meta.yaml") or {}
        html = _JINJA.get_template("pack.html").render(
            pack_id=pack_id,
            meta=meta,
            lore=_load_all(pack_dir / "lore"),
            pcs=_load_all(pack_dir / "player_characters"),
            actors=_load_all(pack_dir / "actors"),
            items=_load_all(pack_dir / "items"),
            scenes=_load_all(pack_dir / "scenes"),
            objects=_load_all(pack_dir / "scene_objects"),
            story=_load_yaml(pack_dir / "story_setup.yaml"),
            rarity_colors=RARITY_COLORS,
        )
        self._respond(html)

    def _respond(self, html: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # 安静模式 / Quiet mode


def run(pack_dir: str = "world-packs/custom", host: str = "127.0.0.1", port: int = 8888):
    global PACKS_DIR
    PACKS_DIR = Path(pack_dir)

    url = f"http://{host}:{port}"
    print(f"World Pack Viewer: {url}")
    print(f"目录 / Dir: {PACKS_DIR.resolve()}")
    print("Ctrl+C 退出 / Ctrl+C to quit")
    webbrowser.open(url)

    server = HTTPServer((host, port), ViewerHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
