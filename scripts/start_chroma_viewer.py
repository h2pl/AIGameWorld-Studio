"""一键启动 AIGameWorld-Studio 知识库 Chroma Server + 自动打开 Viewer UI.

纯 Python 启动器（不依赖 bash/PowerShell/Git Bash；不提前 import chromadb/fastapi，避免 --help 太重）：
  cd E:\\Projects\\AIGameWorld-Studio
  uv run python scripts\\start_chroma_viewer.py           # 默认 8001 + 自动开浏览器
  uv run python scripts\\start_chroma_viewer.py --help    # 看支持的参数
  python scripts\\start_chroma_viewer.py --port 9001 --no-browser

如果没有 uv，只要激活 virtualenv 再跑也行：
  .venv\\Scripts\\activate
  python scripts\\start_chroma_viewer.py
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════
# 路径自定位：找到 AIGameWorld-Studio 根目录
# ═══════════════════════════════════════════════════════════════════════
def _locate_studio_root() -> Path:
    # 本脚本在 Studio/scripts/ 下 → 上一级就是 Studio 根
    here = Path(__file__).resolve().parent
    if (here / "chroma_server.py").exists() and (here.parent / "pyproject.toml").exists():
        return here.parent
    if (here / "pyproject.toml").exists():
        return here
    # 如果用户把脚本移到别处：向上找 6 层内有 pyproject.toml 且 scripts/chroma_server.py 的目录
    p = here
    for _ in range(6):
        if (p / "pyproject.toml").exists() and (p / "scripts" / "chroma_server.py").exists():
            return p
        p = p.parent
    raise FileNotFoundError(
        "找不到 AIGameWorld-Studio 根目录（需要包含 pyproject.toml 和 scripts/chroma_server.py）。\n"
        f"请确认 start_chroma_viewer.py 还在 AIGameWorld-Studio/scripts/ 下。当前脚本目录: {here}"
    )


STUDIO_ROOT = _locate_studio_root()
os.chdir(STUDIO_ROOT)  # 保持与 chroma_server.py 里的 cwd 一致


# ═══════════════════════════════════════════════════════════════════════
# 独立 argparse（不 import chroma_server，避免 --help 触发 chromadb/opentelemetry 大依赖加载）
# ═══════════════════════════════════════════════════════════════════════
def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="start_chroma_viewer",
        description=(
            "AIGameWorld-Studio 知识库一键启动器：检测端口占用 → 选 python → 调起 chroma_server.py 并 --open-browser。\n"
            "不做任何依赖安装，如果缺依赖（uvicorn/chromadb/fastapi 等）请先 `uv sync`。"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--host", default="0.0.0.0", help="绑定地址")
    p.add_argument("--port", type=int, default=8001, help="监听端口（知识库默认 8001）")
    p.add_argument(
        "--persist-path",
        type=Path,
        default=None,
        help="ChromaDB 持久化目录（不传默认 Studio/data/chroma）",
    )
    p.add_argument(
        "--log-level",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        default="info",
        help="chroma_server 日志级别",
    )
    p.add_argument(
        "--open-browser",
        action="store_true",
        help="server 起来后自动用默认浏览器打开 /viewer（不传也默认开，传 --no-browser 可关）",
    )
    p.add_argument("--no-browser", action="store_true", help="不要自动开浏览器")
    p.add_argument(
        "--no-viewer", action="store_true", help="不注册 /viewer 和 /_/* 路由，只保留纯 Chroma HTTP + health"
    )
    p.add_argument(
        "--no-kill",
        action="store_true",
        help="检测到端口被占用时不要自动杀进程（默认会自动杀 LISTENING 的占用进程）",
    )
    p.add_argument(
        "--wait",
        action="store_true",
        help="server 进程结束后不立即退出，等待用户按回车（适合 .py 脚本被双击打开的场景）",
    )
    return p


_args = _build_arg_parser().parse_args()

# 默认：没传 --no-browser / -h / --help → 自动开浏览器
if not _args.no_browser and "-h" not in sys.argv and "--help" not in sys.argv:
    _args.open_browser = True


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect((host, port))
        except ConnectionRefusedError, OSError, TimeoutError:
            return False
        else:
            return True


# ═══════════════════════════════════════════════════════════════════════
# 跨平台杀占用端口的 LISTENING 进程（纯标准库，不依赖 psutil）
# ═══════════════════════════════════════════════════════════════════════
import re as _re
import shutil as _shutil


def _kill_process_on_port(port: int, host: str = "127.0.0.1") -> int:
    """杀掉在 host:port 上 LISTEN 的进程。返回杀掉的进程数（0 = 没找到，-1 = 失败）。"""
    pids: list[str] = []
    try:
        if sys.platform.startswith("win"):
            out = subprocess.check_output(["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                if parts[0] != "TCP":
                    continue
                local = parts[1]
                # 0.0.0.0:port / 127.0.0.1:port / [::]:port 都算
                if not (local.endswith(f":{port}")):
                    continue
                if parts[3] != "LISTENING":
                    continue
                pid = parts[4]
                if pid.isdigit() and pid not in pids:
                    pids.append(pid)
            if pids:
                subprocess.run(
                    ["taskkill", "/F"] + [tok for pid in pids for tok in ("/PID", pid)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
        else:
            # macOS / Linux
            if _shutil.which("lsof"):
                out = subprocess.check_output(
                    ["lsof", "-iTCP", f":{port}", "-sTCP:LISTEN", "-t"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                pids = [p for p in out.splitlines() if p.strip().isdigit()]
            elif _shutil.which("ss"):
                out = subprocess.check_output(
                    ["ss", "-ltnpH", f"sport = :{port}"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                for m in _re.finditer(r"pid=(\d+)", out):
                    pid = m.group(1)
                    if pid.isdigit() and pid not in pids:
                        pids.append(pid)
            if pids:
                subprocess.run(["kill", "-9"] + pids, check=False, capture_output=True, text=True)
        return len(pids)
    except Exception:
        return -1


def _banner() -> None:
    host_alias = "127.0.0.1" if _args.host == "0.0.0.0" else _args.host
    viewer = f"http://{host_alias}:{_args.port}/viewer"
    health = f"http://{host_alias}:{_args.port}/health"
    if _args.persist_path:
        data_dir = Path(_args.persist_path).expanduser().resolve()
    else:
        data_dir = STUDIO_ROOT / "data" / "chroma"
    print()
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║     AIGameWorld-Studio · 知识库 Chroma Viewer (launcher)      ║")
    print("╠════════════════════════════════════════════════════════════════╣")
    print(f"║  Project:   {STUDIO_ROOT}")
    print(f"║  Health:    {health}")
    print(f"║  Viewer:    {viewer}")
    print(f"║  Data dir:  {data_dir}")
    print(f"║  Port free: {'no (ALREADY IN USE!)' if _is_port_in_use(_args.port) else 'yes'}")
    print("╚════════════════════════════════════════════════════════════════╝")
    print()


_banner()


# ═══════════════════════════════════════════════════════════════════════
# 端口占用检测 + 默认自动杀（传 --no-kill 关掉自动杀）
# ═══════════════════════════════════════════════════════════════════════
def _ensure_port_free(port: int, host: str = "127.0.0.1", *, allow_kill: bool) -> None:
    if not _is_port_in_use(port, host):
        return
    if allow_kill:
        print(f"  🔪 端口 {port} 被占用，尝试杀 LISTENING 进程 ...")
        killed = _kill_process_on_port(port, host)
        if killed < 0:
            print("  ⚠️  杀进程失败（异常）")
        elif killed == 0:
            print("  ⚠️  没找到在 LISTEN 的进程（可能是其他状态占用）")
        else:
            print(f"  ✅ 杀了 {killed} 个占用进程，等待释放 ...")
            time.sleep(1.5)
        # 再检测 1 次
        if not _is_port_in_use(port, host):
            return
    print(
        f"\n[ERROR] 端口 {port} 仍被占用（自动杀失败或你传了 --no-kill）。\n"
        f"       方案 A：换端口再试 → python scripts\\start_chroma_viewer.py --port {port + 1}\n"
        f"       方案 B：手动杀掉占用进程后重跑（Windows: taskkill /F /PID <LISTENING PID>）\n",
        file=sys.stderr,
    )
    sys.exit(3)


_ensure_port_free(_args.port, _args.host, allow_kill=not _args.no_kill)


# ═══════════════════════════════════════════════════════════════════════
# 选择 python 解释器（优先：uv run python → .venv/Scripts/python → sys.executable）
# ═══════════════════════════════════════════════════════════════════════
def _pick_python() -> list[str]:
    # 1) 如果 pyproject.toml 存在且 uv 可用，优先 uv run
    pyproject = STUDIO_ROOT / "pyproject.toml"
    if pyproject.exists():
        try:
            r = subprocess.run(["uv", "--version"], check=False, capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return ["uv", "run", "python"]
        except FileNotFoundError, PermissionError:
            pass

    # 2) .venv/Scripts/python.exe (Windows) / .venv/bin/python (POSIX)
    for rel in [".venv/Scripts/python.exe", ".venv/bin/python"]:
        p = STUDIO_ROOT / rel
        if p.exists():
            return [str(p)]

    # 3) 当前 sys.executable
    return [sys.executable]


PY_ARGS = _pick_python()

# ═══════════════════════════════════════════════════════════════════════
# 拼接命令 → 直接调 chroma_server.py
# ═══════════════════════════════════════════════════════════════════════
entry = STUDIO_ROOT / "scripts" / "chroma_server.py"
cmd = [*PY_ARGS, str(entry)]

# 参数
cmd += ["--host", _args.host]
cmd += ["--port", str(_args.port)]
cmd += ["--log-level", _args.log_level]
if _args.open_browser:
    cmd += ["--open-browser"]
if _args.no_viewer:
    cmd += ["--no-viewer"]
if _args.persist_path:
    cmd += ["--persist-path", str(Path(_args.persist_path).expanduser().resolve())]

print("› ", " ".join(cmd))
print()
print("按 Ctrl+C 退出。")
print()

try:
    if _args.wait:
        # 用户希望"结束后等待用户确认"，那么用 Popen 不替换父进程
        proc = subprocess.Popen(cmd)
        try:
            proc.wait()
        except KeyboardInterrupt:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        input("\n[done] 按回车关闭窗口…")
    else:
        # 前台执行 → 用户 Ctrl+C 这里退出
        subprocess.run(cmd, check=False)
except KeyboardInterrupt:
    print("\n[stop] Ctrl+C caught, bye.")
