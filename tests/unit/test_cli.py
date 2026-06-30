# CLI 单元测试 / Unit tests for CLI

import subprocess
import sys


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
    )


# === validate ===


def test_validate_ok_exits_zero(tmp_path):
    """生成合法 world-pack 后校验通过 / Validate passes on valid world-pack."""

    import yaml

    world = tmp_path / "test_world"
    world.mkdir()
    (world / "meta.yaml").write_text(
        yaml.dump({"id": "test", "name": "Test", "ruleset": "d20", "starting_scene": "s1"}, allow_unicode=True),
        encoding="utf-8",
    )
    (world / "scenes").mkdir()
    (world / "scenes" / "s1.yaml").write_text(
        yaml.dump({"id": "s1", "name": "Tavern", "type": "indoor", "description": "A tavern"}, allow_unicode=True),
        encoding="utf-8",
    )

    r = _run("validate", str(world))
    assert r.returncode == 0
    assert "0 failed" in r.stdout


def test_validate_missing_exits_one():
    """不存在的路径校验失败 / Validate fails on missing path."""
    r = _run("validate", "no_such")
    assert r.returncode == 1


def test_validate_errors_to_stderr():
    """校验错误输出到 stderr / Validation errors go to stderr."""
    r = _run("validate", "no_such")
    assert r.returncode == 1
    assert "Error" in r.stderr


def test_help_works():
    """帮助信息正常 / Help works."""
    r = _run("-h")
    assert r.returncode == 0
    assert "generate" in r.stdout


# === generate --skeleton-only ===


def test_generate_skeleton_only(tmp_path):
    """--skeleton-only 生成骨架并通过校验 / --skeleton-only produces valid skeleton."""
    r = _run("generate", "--name", "test_skel", "--skeleton-only", "-o", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "skeleton" in r.stdout

    # 校验生成的骨架 / Validate generated skeleton
    r2 = _run("validate", str(tmp_path / "test_skel"))
    assert r2.returncode == 0, r2.stderr
    assert "0 failed" in r2.stdout


def test_generate_skeleton_only_with_counts(tmp_path):
    """--skeleton-only 按数量生成 / --skeleton-only respects counts."""
    r = _run("generate", "--name", "counts", "--skeleton-only", "--pc", "3", "--actor", "4", "-o", str(tmp_path))
    assert r.returncode == 0, r.stderr

    world = tmp_path / "counts"
    assert len(list((world / "player_characters").glob("*.yaml"))) == 3
    assert len(list((world / "actors").glob("*.yaml"))) == 4
