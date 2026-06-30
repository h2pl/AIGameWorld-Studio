# 全链路集成测试 / Full pipeline integration test
# generate → validate → load → verify SQLite

import sqlite3
import subprocess
import sys
from pathlib import Path


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
    )


def test_full_flow(tmp_path: Path):
    """generate → validate → load 全链路."""
    worlds = tmp_path / "worlds"
    db = tmp_path / "world.db"

    # generate
    r = _run("generate", "--name", "test_world", "--pc", "2", "--actor", "3", "--scene", "2", "-o", str(worlds))
    assert r.returncode == 0, r.stderr
    world = worlds / "test_world"
    assert world.exists()

    # validate
    r = _run("validate", str(world))
    assert r.returncode == 0, r.stderr

    # load
    r = _run("load", str(world), "--db", str(db))
    assert r.returncode == 0, r.stderr
    assert "records" in r.stdout

    # verify SQLite
    conn = sqlite3.connect(str(db))
    tables = {"scenes": 2, "items": 5, "player_characters": 2, "actors": 3}
    for table, expected in tables.items():
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == expected, f"{table}: expected {expected}, got {count}"
    conn.close()
