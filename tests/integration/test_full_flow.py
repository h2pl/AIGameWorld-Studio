# 全链路集成测试 / Full pipeline integration test
# generate → validate

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
    """generate → validate 全链路."""
    worlds = tmp_path / "worlds"

    # generate
    r = _run("generate", "--name", "test_world", "--pc", "2", "--actor", "3", "--scene", "2", "-o", str(worlds))
    assert r.returncode == 0, r.stderr
    world = worlds / "test_world"
    assert world.exists()

    # validate
    r = _run("validate", str(world))
    assert r.returncode == 0, r.stderr
    assert "0 failed" in r.stdout
