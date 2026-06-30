# CLI 入口 / CLI Entry
# 模板驱动：templates/ 定义结构 × CLI 参数控制数量 → 生成世界
# Template-driven: templates/ define structure × CLI params control counts → generate world

import argparse
import sys
from pathlib import Path

_DEFAULTS = {"pc": 2, "actor": 2, "scene": 2, "item": 5, "scene_object": 2, "lore": 1}

_EXAMPLES = """
示例 / Examples:
  aw-studio generate                              # 全部默认
  aw-studio generate --name my_world              # 指定世界名
  aw-studio generate --pc 4 --actor 6 --scene 3   # 指定数量
  aw-studio generate --pc 4 --actor 6 --scene 3   # 指定数量
  aw-studio validate worlds/custom/my_world       # 校验
  aw-studio load worlds/custom/my_world --db worlds.db     # 加载
"""


def main():
    """主入口 / Main entry point."""
    try:
        return _main()
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


def _main() -> int:
    parser = argparse.ArgumentParser(
        prog="aw-studio",
        description="AIGameWorld Studio — 世界创作工坊 / World Creation Workshop",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # --- generate ---
    gen = sub.add_parser(
        "generate",
        help="生成世界 / Generate world from templates",
        epilog="示例: aw-studio generate --name 遗忘国度 --pc 2 --actor 3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    gen.add_argument("--name", type=str, default="my_world", help="世界名 / World name")
    gen.add_argument("--pc", type=int, default=_DEFAULTS["pc"], help=f"主角数量 (default: {_DEFAULTS['pc']})")
    gen.add_argument("--actor", type=int, default=_DEFAULTS["actor"], help=f"配角数量 (default: {_DEFAULTS['actor']})")
    gen.add_argument("--scene", type=int, default=_DEFAULTS["scene"], help=f"场景数量 (default: {_DEFAULTS['scene']})")
    gen.add_argument("--item", type=int, default=_DEFAULTS["item"], help=f"物品数量 (default: {_DEFAULTS['item']})")
    gen.add_argument(
        "--scene-object",
        type=int,
        default=_DEFAULTS["scene_object"],
        help=f"场景对象数量 (default: {_DEFAULTS['scene_object']})",
    )
    gen.add_argument("--lore", type=int, default=_DEFAULTS["lore"], help=f"设定条数 (default: {_DEFAULTS['lore']})")
    gen.add_argument(
        "-o", "--output", type=Path, default=Path("worlds/custom"), help="输出目录 (default: worlds/custom/)"
    )

    # --- validate ---
    val = sub.add_parser(
        "validate",
        help="校验模板 / Validate template",
        epilog="示例: aw-studio validate worlds/custom/my_world",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    val.add_argument("path", type=Path, help="模板目录 / Template directory")

    # --- load ---
    ld = sub.add_parser(
        "load",
        help="加载到数据库 / Load to database",
        epilog="示例: aw-studio load worlds/custom/my_world --db worlds.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ld.add_argument("path", type=Path, help="模板目录 / Template directory")
    ld.add_argument("--db", type=Path, help="SQLite 路径 (default: <pack>.db)")
    ld.add_argument("--chroma", type=Path, help="ChromaDB 路径 (optional)")

    args = parser.parse_args()
    return {"generate": _generate, "validate": _validate, "load": _load}[args.command](args)


# ---- handlers ----


def _generate(args) -> int:
    """Generate world from templates based on CLI parameters."""
    from src.generator.generate import GenerateParams, generate

    # Build params from CLI args
    params = GenerateParams(
        world_name=args.name,
        num_pcs=args.pc,
        num_actors=args.actor,
        num_scenes=args.scene,
        num_items=args.item,
        num_scene_objects=args.scene_object,
        num_lore=args.lore,
        output_dir=args.output,
    )
    out = generate(params)
    print(f"[OK] {out}")
    print(f"     {args.pc} PC + {args.actor} Actor + {args.scene} Scene")
    print(f"     Next: aw-studio validate {out}")
    return 0


def _validate(args) -> int:
    """Validate YAML template files under the given directory."""
    if not args.path.exists():
        print(f"Error: not found: {args.path}", file=sys.stderr)
        return 1
    # Run validator and format report
    from src.validator.validate import format_report, validate_template

    result = validate_template(args.path)
    print(format_report(result))
    if result.is_valid:
        print("[OK] Validation passed")
    return 0 if result.is_valid else 1


def _load(args) -> int:
    """Load validated template data into SQLite and optionally ChromaDB."""
    if not args.path.exists():
        print(f"Error: not found: {args.path}", file=sys.stderr)
        return 1
    # Import for reading YAML and writing to DB
    from src.loader.db_writer import write_template
    from src.loader.yaml_reader import load_all
    from src.validator.validate import validate_template

    vr = validate_template(args.path)
    if not vr.is_valid:
        print(f"Validation failed ({vr.failed} errors):", file=sys.stderr)
        for e in vr.errors:
            print(f"  {e}", file=sys.stderr)
        return 1

    data = load_all(args.path)
    pack_name = args.path.name
    db_path = args.db or (args.path.parent / f"{pack_name}.db")
    try:
        written = write_template(str(db_path), data, pack_name=pack_name)
        print(f"[OK] SQLite: {written} records → {db_path}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if args.chroma:
        try:
            import chromadb

            from src.loader.chroma_writer import write_lore, write_scenes

            client = chromadb.PersistentClient(path=str(args.chroma))
            write_lore(client, data.get("lore", []), pack_name)
            write_scenes(client, data.get("scenes", []), pack_name)
            print(f"[OK] ChromaDB → {args.chroma}")
        except ImportError:
            print("Warning: chromadb not installed, skipped", file=sys.stderr)
        except Exception as e:
            print(f"Warning: ChromaDB failed ({e}), SQLite OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
