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
  aw-studio validate worlds/custom/my_world       # 校验

  导入到 AIGameWorld 引擎:
  aw import worlds/custom/my_world                # YAML → Domain → SQLite + ChromaDB
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

    args = parser.parse_args()
    return {"generate": _generate, "validate": _validate}[args.command](args)


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


if __name__ == "__main__":
    sys.exit(main())
