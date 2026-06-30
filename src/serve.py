# YAML World Pack Viewer / YAML 世界包查看器（Studio 项目）
# 仅浏览 YAML pack，DB 查看器已集成到主项目 AIGameWorld 的 /view 路由

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "serve"
_JINJA = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))

SOURCE_DIR: Path  # 运行时由 run() 设置

RARITY_COLORS = {  # 稀有度颜色映射
    "common": "#9ca3af",
    "uncommon": "#22c55e",
    "rare": "#3b82f6",
    "epic": "#a855f7",
    "legendary": "#f59e0b",
}


def _load_yaml(path: Path) -> dict | None:
    """读取单个 YAML 文件."""
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text("utf-8"))


def _load_yaml_all(dir_path: Path) -> list[dict]:
    """读取目录下所有 YAML 文件."""
    if not dir_path.exists():
        return []
    return [d for f in sorted(dir_path.glob("*.yaml")) if isinstance(d := yaml.safe_load(f.read_text("utf-8")), dict)]


class ViewerHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理 — 路由 / 或 /pack/{id}."""

    def do_GET(self):  # GET 路由分发
        try:
            path = self.path.rstrip("/") or "/"
            if path == "/":
                self._render_index()
            elif path.startswith("/pack/"):
                self._render_pack(path.split("/pack/")[1])
            else:
                self.send_error(404)
        except Exception:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            import traceback

            self.wfile.write(traceback.format_exc().encode("utf-8"))

    def _render_index(self):
        # 遍历 world-packs/custom/ 目录，列出所有含 meta.yaml 的 pack
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
        html = _JINJA.get_template("index_yaml.html").render(packs=packs)
        self._respond(html)

    def _render_pack(self, pack_id: str):
        # 读取 pack 目录下所有实体 YAML 文件
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
        """发送 HTML 响应."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def run(pack_dir: str = "world-packs/custom", host: str = "127.0.0.1", port: int = 8888):
    """启动 YAML 查看器 HTTP 服务."""
    global SOURCE_DIR
    # 基于 serve.py 位置解析项目根目录，不再依赖 CWD
    _root = Path(__file__).parent.parent
    SOURCE_DIR = (_root / pack_dir).resolve() if not Path(pack_dir).is_absolute() else Path(pack_dir)
    print(f"YAML Packs: {SOURCE_DIR}")

    # 打印启动信息
    url = f"http://{host}:{port}"
    print(f"YAML World Pack Viewer: {url}")

    server = HTTPServer((host, port), ViewerHandler)
    # 阻塞运行直到 Ctrl+C
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
