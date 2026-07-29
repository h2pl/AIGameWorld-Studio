"""
AIGameWorld-Studio · Qdrant 向量库启动器
========================================

跨平台 Python 脚本（Windows / macOS / Linux），包装 ``docker compose`` 管理
Studio 知识库使用的 Qdrant 容器。

环境变量（可选）
----------------
KB_QDRANT_URL       Qdrant 地址（默认 http://127.0.0.1:6333 ）
COMPOSE_FILE        指定 compose 文件路径（默认项目根 docker-compose.yml）

用法
----
# 启动（后台）+ 健康检查 + 自动打开 Dashboard
uv run python scripts/start_qdrant.py start
uv run python scripts/start_qdrant.py            # 无参数等价于 start

# 只启动，不开浏览器
uv run python scripts/start_qdrant.py start --no-browser

# 停止
uv run python scripts/start_qdrant.py stop

# 重启
uv run python scripts/start_qdrant.py restart

# 状态
uv run python scripts/start_qdrant.py status

# 实时日志
uv run python scripts/start_qdrant.py logs

数据持久化：./data/qdrant/storage（与 docker-compose.yml 对齐）
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

# ---------- 常量 ----------
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # scripts/.. -> Studio 根
DEFAULT_COMPOSE = PROJECT_ROOT / "docker-compose.yml"
DEFAULT_QDRANT_URL = os.getenv("KB_QDRANT_URL") or "http://127.0.0.1:6333"
CONTAINER_NAME = "ags_qdrant_studio"
SERVICE_NAME = "qdrant"
HEALTH_PATH = "/healthz"
DASHBOARD_PATH = "/dashboard"

BANNER = r"""
╔══════════════════════════════════════════════════════════════════╗
║    AIGameWorld-Studio · Qdrant Vector Store (launcher)          ║
╠══════════════════════════════════════════════════════════════════╣
║  Project:   {project}
║  Compose:   {compose}
║  HTTP URL:  {url}
║  Dashboard: {url}{dash}
║  Container: {name}
╚══════════════════════════════════════════════════════════════════╝
""".format(
    project=str(PROJECT_ROOT),
    compose=str(DEFAULT_COMPOSE),
    url=DEFAULT_QDRANT_URL.rstrip("/"),
    dash=DASHBOARD_PATH,
    name=CONTAINER_NAME,
)


# ---------- 工具函数 ----------
def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """统一调用子进程，输出 stdout/stderr 合流."""
    kwargs.setdefault("cwd", str(PROJECT_ROOT))
    kwargs.setdefault("text", True)
    print(f"›  {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, **kwargs)


def _docker_compose_cmd() -> list[str]:
    """优先用 ``docker compose``（插件），找不到再试 ``docker-compose``."""
    # 检查 docker compose 插件（v2 官方）
    check = shutil.which("docker")
    if check:
        try:
            r = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                return ["docker", "compose"]
        except Exception:
            pass
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    print(
        "❌ 未找到 docker compose / docker-compose。\n"
        "   请先安装 Docker Desktop（Windows/macOS）或 docker-compose-plugin（Linux）。",
        file=sys.stderr,
    )
    sys.exit(1)


def _port_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _health_check(timeout_s: int = 60) -> bool:
    """轮询 Qdrant /healthz 直到返回 OK 或超时."""
    url = DEFAULT_QDRANT_URL.rstrip("/") + HEALTH_PATH
    deadline = time.time() + timeout_s
    last_err = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
                # Qdrant /healthz 返回类似 {"title":"qdrant - ...", "status":"ok"} 或纯文本 ok
                if resp.status < 300 and ("ok" in body.lower() or "health" in body.lower()):
                    return True
                last_err = f"HTTP {resp.status}: {body[:80]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(1)
    print(f"\n⚠️  健康检查超时（{timeout_s}s）：{last_err}", file=sys.stderr)
    return False


def _open_browser() -> None:
    url = DEFAULT_QDRANT_URL.rstrip("/") + DASHBOARD_PATH
    print(f"\n🌐 打开 Dashboard：{url}")
    try:
        webbrowser.open_new_tab(url)
    except Exception as e:
        print(f"   （自动打开失败：{e}，请手动访问上面 URL）")


# ---------- 子命令实现 ----------
def cmd_start(open_browser: bool = True) -> int:
    print(BANNER)
    if not DEFAULT_COMPOSE.exists():
        print(f"❌ 找不到 compose 文件：{DEFAULT_COMPOSE}", file=sys.stderr)
        return 2

    # Qdrant 默认监听在 6333，从 URL 解析一下
    from urllib.parse import urlparse
    u = urlparse(DEFAULT_QDRANT_URL)
    port = u.port or 6333
    host = u.hostname or "127.0.0.1"

    dc = _docker_compose_cmd()
    compose_args = []
    if DEFAULT_COMPOSE != (PROJECT_ROOT / "docker-compose.yml"):
        compose_args = ["-f", str(DEFAULT_COMPOSE)]

    # 检查端口占用（占用了但不是 Qdrant 时提示）
    if not _port_free(host, port):
        # 尝试发个 health，看是不是已经是 Qdrant
        try:
            url = DEFAULT_QDRANT_URL.rstrip("/") + HEALTH_PATH
            with urllib.request.urlopen(url, timeout=2) as resp:
                body = resp.read().decode(errors="ignore")
                if resp.status < 300 and ("ok" in body.lower() or "health" in body.lower()):
                    print(f"✅ {DEFAULT_QDRANT_URL} 已经是可用的 Qdrant 服务（无需重复启动）")
                    if open_browser:
                        _open_browser()
                    return 0
        except Exception:
            pass
        print(f"⚠️  端口 {host}:{port} 被其他进程占用，可能导致容器启动失败。")

    # docker compose up -d [--wait 可选]
    r = _run(dc + compose_args + ["up", "-d", SERVICE_NAME])
    if r.returncode != 0:
        print(f"❌ docker compose up 失败，exit={r.returncode}", file=sys.stderr)
        return r.returncode

    print("\n⏳ 等待 Qdrant 就绪（轮询 /healthz）", end="", flush=True)
    ok = _health_check(timeout_s=90)
    print()
    if ok:
        print("✅ Qdrant 已就绪")
    else:
        print("⚠️  健康检查未通过，尝试查看日志：start_qdrant.py logs")

    if open_browser:
        _open_browser()
    print("\n按 Ctrl+C 退出（容器将继续后台运行）。")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n已退出 launcher（容器仍在后台）。停止请用：start_qdrant.py stop")
    return 0


def cmd_stop() -> int:
    dc = _docker_compose_cmd()
    compose_args = []
    if DEFAULT_COMPOSE != (PROJECT_ROOT / "docker-compose.yml"):
        compose_args = ["-f", str(DEFAULT_COMPOSE)]
    print(f"⏹  停止 {SERVICE_NAME} 服务（down，保留 volume 数据）")
    r = _run(dc + compose_args + ["down"])
    return r.returncode


def cmd_restart(open_browser: bool = True) -> int:
    rc = cmd_stop()
    if rc != 0:
        print(f"⚠️  stop 返回 {rc}，仍然尝试 start...")
    return cmd_start(open_browser=open_browser)


def cmd_status() -> int:
    dc = _docker_compose_cmd()
    compose_args = []
    if DEFAULT_COMPOSE != (PROJECT_ROOT / "docker-compose.yml"):
        compose_args = ["-f", str(DEFAULT_COMPOSE)]

    r = _run(dc + compose_args + ["ps", SERVICE_NAME])
    print()
    url = DEFAULT_QDRANT_URL.rstrip("/") + HEALTH_PATH
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = resp.read().decode(errors="ignore")
            print(f"✅ HTTP 健康：HTTP {resp.status}  body={body[:120]!r}")
    except Exception as e:
        print(f"❌ HTTP 不可达：{type(e).__name__}: {e}")
    return r.returncode


def cmd_logs(follow: bool = True, tail: int = 200) -> int:
    dc = _docker_compose_cmd()
    compose_args = []
    if DEFAULT_COMPOSE != (PROJECT_ROOT / "docker-compose.yml"):
        compose_args = ["-f", str(DEFAULT_COMPOSE)]
    args = ["logs", f"--tail={tail}"]
    if follow:
        args.append("-f")
    args.append(SERVICE_NAME)
    try:
        p = subprocess.Popen(dc + compose_args + args, cwd=str(PROJECT_ROOT))
        p.wait()
        return p.returncode or 0
    except KeyboardInterrupt:
        return 0


# ---------- CLI ----------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="AIGameWorld-Studio · Qdrant 向量库启动器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", metavar="start|stop|restart|status|logs")

    p_start = sub.add_parser("start", help="后台启动 Qdrant（默认行为）")
    p_start.add_argument("--no-browser", action="store_true", help="只启动，不自动打开 Dashboard")

    p_restart = sub.add_parser("restart", help="重启 Qdrant")
    p_restart.add_argument("--no-browser", action="store_true")

    sub.add_parser("stop", help="停止 Qdrant 容器（保留数据）")
    sub.add_parser("status", help="查看容器 + HTTP 健康状态")

    p_logs = sub.add_parser("logs", help="查看实时日志")
    p_logs.add_argument("--no-follow", action="store_true", help="只打印最近日志，不跟随")
    p_logs.add_argument("--tail", type=int, default=200, help="起始行数（默认 200）")

    args = ap.parse_args()
    cmd = args.cmd or "start"

    if cmd == "start":
        return cmd_start(open_browser=not getattr(args, "no_browser", False))
    if cmd == "stop":
        return cmd_stop()
    if cmd == "restart":
        return cmd_restart(open_browser=not getattr(args, "no_browser", False))
    if cmd == "status":
        return cmd_status()
    if cmd == "logs":
        return cmd_logs(follow=not args.no_follow, tail=args.tail)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
