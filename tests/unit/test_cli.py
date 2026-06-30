# CLI 单元测试 / Unit tests for CLI (template-driven)

import subprocess
import sys
from pathlib import Path


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
    )


# === generate ===


def test_generate_defaults(tmp_path: Path):
    out = tmp_path / "worlds"
    r = _run("generate", "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert (out / "my_world" / "meta.yaml").exists()


def test_generate_with_name(tmp_path: Path):
    out = tmp_path / "worlds"
    r = _run("generate", "--name", "test_world", "-o", str(out))
    assert r.returncode == 0
    assert (out / "test_world" / "meta.yaml").exists()


def test_generate_with_counts(tmp_path: Path):
    out = tmp_path / "worlds"
    r = _run("generate", "--pc", "3", "--actor", "5", "--scene", "4", "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert len(list((out / "my_world" / "player_characters").glob("*.yaml"))) == 3
    assert len(list((out / "my_world" / "actors").glob("*.yaml"))) == 5


def test_generate_default_name(tmp_path: Path):
    out = tmp_path / "worlds"
    r = _run("generate", "-o", str(out))
    assert r.returncode == 0
    assert (out / "my_world").exists()


# === validate ===


def test_validate_ok_exits_zero(tmp_path: Path):
    from src.generator.generate import GenerateParams, generate

    world = generate(GenerateParams(world_name="test", output_dir=tmp_path))
    r = _run("validate", str(world))
    assert r.returncode == 0
    assert "0 failed" in r.stdout


def test_validate_missing_exits_one():
    r = _run("validate", "no_such")
    assert r.returncode == 1


# === load ===


def test_load_ok_exits_zero(tmp_path: Path):
    from src.generator.generate import GenerateParams, generate

    world = generate(GenerateParams(world_name="test", output_dir=tmp_path))
    db = tmp_path / "test.db"
    r = _run("load", str(world), "--db", str(db))
    assert r.returncode == 0, r.stderr
    assert "records" in r.stdout


def test_load_auto_validates(tmp_path: Path):
    import yaml

    from src.generator.generate import GenerateParams, generate

    world = generate(GenerateParams(world_name="test", output_dir=tmp_path))
    # break meta
    meta = world / "meta.yaml"
    data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
    del data["name"]
    meta.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    r = _run("load", str(world), "--db", str(tmp_path / "t.db"))
    assert r.returncode == 1
    assert "Validation failed" in r.stderr


def test_load_missing_exits_one():
    r = _run("load", "no_such")
    assert r.returncode == 1


# === edge cases ===


def test_validate_errors_to_stderr():
    r = _run("validate", "no_such")
    assert r.returncode == 1
    assert "Error" in r.stderr


def test_help_works():
    r = _run("-h")
    assert r.returncode == 0
    assert "generate" in r.stdout
