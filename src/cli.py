# CLI 入口 / CLI Entry
# 生成管线: CLI params → LangGraph → LLM fill → validate → world-pack

import argparse
import asyncio
import sys
from pathlib import Path

_DEFAULTS = {"pc": 2, "actor": 2, "scene": 2, "item": 5, "scene_object": 2, "lore": 1, "max_retries": 3}

_EXAMPLES = """
示例 / Examples:
  aw-studio generate -i                                         # 交互式输入所有参数
  aw-studio generate --name 仙剑世界 --theme 仙侠修真           # 指定主题
  aw-studio generate --pc 4 --actor 6 --scene 3                 # 指定数量
  aw-studio generate --skeleton-only                            # 仅骨架（不调 LLM）
  aw-studio validate world-packs/custom/my_world                # 校验 world-pack

  导入到 AIGameWorld 引擎:
  aw import world-packs/custom/my_world                         # world-pack → Domain → SQLite + ChromaDB
"""


def main():
    """主入口 / Main entry point."""
    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


async def _main() -> int:
    parser = argparse.ArgumentParser(
        prog="aw-studio",
        description="AIGameWorld Studio — world-pack 创作工坊 / World Pack Creation Workshop",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # --- generate world-pack ---
    gen = sub.add_parser(
        "generate",
        help="LLM 生成 world-pack / Generate world-pack with LLM",
        epilog="示例: aw-studio generate -i",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    gen.add_argument("-i", "--interactive", action="store_true", help="交互式输入 / Interactive input mode")
    gen.add_argument("--name", type=str, default="my_world", help="世界显示名称（可为中文）/ Display name")
    gen.add_argument("--pack-id", type=str, default="", help="pack 目录名 + YAML id（ASCII，默认从 --name 自动生成）")
    gen.add_argument("--theme", type=str, default="", help="世界主题 / World theme")
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
        "--max-retries",
        type=int,
        default=_DEFAULTS["max_retries"],
        help=f"LLM 最大重试次数 (default: {_DEFAULTS['max_retries']})",
    )
    gen.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("world-packs/custom"),
        help="输出目录 (default: world-packs/custom/)",
    )
    gen.add_argument(
        "--skeleton-only",
        action="store_true",
        help="仅生成骨架（不调 LLM，用于测试）",
    )

    # --- validate world-pack ---
    val = sub.add_parser(
        "validate",
        help="校验 world-pack / Validate world pack",
        epilog="示例: aw-studio validate world-packs/custom/my_world",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    val.add_argument("path", type=Path, help="world-pack 目录 / World pack directory")

    # --- serve world-pack viewer ---
    srv = sub.add_parser(
        "serve",
        help="Web 查看器 / Launch web viewer",
        epilog="示例: aw-studio serve  |  aw-studio serve world-packs/custom --port 9999",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    srv.add_argument(
        "path", type=str, nargs="?", default="world-packs/custom", help="pack 目录 (default: world-packs/custom/)"
    )
    srv.add_argument("--port", type=int, default=8888, help="端口 (default: 8888)")

    args = parser.parse_args()

    if args.command == "generate" and args.interactive:
        args = _interactive_prompt(gen, args)
        if args is None:
            return 0  # 用户取消 / User cancelled

    return await {"generate": _generate, "validate": _validate, "serve": _serve}[args.command](args)


# ═══════════════════════════════════════════════════════════════
# Interactive prompt
# ═══════════════════════════════════════════════════════════════

_INTERACTIVE_FIELDS = [
    ("name", "世界显示名 / Display name", "my_world", str),
    ("pack_id", "目录名 & id（ASCII）/ Pack ID", "自动生成", str),
    ("theme", "世界主题 / World theme", "无", str),
    ("pc", "主角数量", 2, int),
    ("actor", "配角数量", 4, int),
    ("scene", "场景数量", 3, int),
    ("item", "物品数量", 6, int),
    ("scene_object", "场景对象数量", 2, int),
    ("lore", "设定条数", 2, int),
    ("max_retries", "LLM 最大重试次数", 3, int),
    ("output", "输出目录", "world-packs/custom", str),
    ("skeleton_only", None, False, None),  # 交互模式不设 skeleton_only
]


def _prompt(label: str, default, cast=str) -> str | int | None:
    """单步输入提示 / Single step input prompt."""
    if isinstance(default, bool):
        return default
    try:
        raw = input(f"  {label} [{default}]: ").strip()
    except EOFError, KeyboardInterrupt:
        return None
    if raw == "":
        return default
    if cast is int:
        try:
            return int(raw)
        except ValueError:
            print("    请输入数字 / Please enter a number", file=sys.stderr)
            return _prompt(label, default, cast)
    return raw


def _interactive_prompt(parser, args):
    """交互式模式 / Interactive mode — 逐步输入所有参数."""
    print("AIGameWorld Studio — 交互式生成 / Interactive Generation")
    print("=" * 50)
    print("按 Enter 使用默认值，Ctrl+C 退出 / Press Enter for default, Ctrl+C to quit\n")

    # 用 argparse Namespace 构建结果 / Build result namespace
    import argparse as _ap

    result = _ap.Namespace(
        command="generate",
        interactive=False,
        skeleton_only=False,
    )

    for attr, label, default, cast in _INTERACTIVE_FIELDS:
        if label is None:  # skeleton_only 不提示
            setattr(result, attr, default)
            continue
        val = _prompt(label, default, cast)
        if val is None:
            print("\n已取消 / Cancelled")
            return None
        setattr(result, attr, val)

    # 校验 / Validation
    if result.pc < 0 or result.actor < 0 or result.scene < 0:
        print("数量不能为负 / Counts cannot be negative", file=sys.stderr)
        return _interactive_prompt(parser, args)
    if not result.name.strip():
        print("名称不能为空 / Name cannot be empty", file=sys.stderr)
        return _interactive_prompt(parser, args)
    if not result.theme.strip() or result.theme == "无":
        result.theme = ""

    # 转换 output 为 Path
    result.output = Path(result.output)

    print()
    return result


# ═══════════════════════════════════════════════════════════════
# Handlers
# ═══════════════════════════════════════════════════════════════


async def _generate(args) -> int:
    """LLM 管线生成 world-pack / LLM pipeline generate world-pack."""
    from src.graph.graph import generate_world_pack
    from src.pipeline.world_pack.params import GenerateParams
    from src.pipeline.world_pack.skeleton import generate_skeleton

    params = GenerateParams(
        world_name=args.name,
        pack_id=args.pack_id,
        theme=args.theme,
        num_pcs=args.pc,
        num_actors=args.actor,
        num_scenes=args.scene,
        num_items=args.item,
        num_scene_objects=args.scene_object,
        num_lore=args.lore,
        output_dir=args.output,
        max_retries=args.max_retries,
    )
    out_dir = Path(params.output_dir) / params.pack_id

    if args.skeleton_only:
        generate_skeleton(out_dir, params)
        print(f"[OK] skeleton: {out_dir}")
        print(f"     Next: aw-studio validate {out_dir}")
        return 0

    try:
        info = f"theme: {params.theme}" if params.theme else "默认"
        print(f"生成中... / Generating... ({info})")
        result = await generate_world_pack(params)
        print(f"[OK] world-pack: {result}")
        print(f"     {args.pc} PC + {args.actor} Actor + {args.scene} Scene")
        print(f"     Next: aw-studio validate {result}")
    except Exception as e:
        if out_dir.exists():
            print(f"[WARN] LLM 生成失败，骨架已保存: {out_dir}", file=sys.stderr)
            print(f"       {e}", file=sys.stderr)
            print(f"       Next: aw-studio validate {out_dir}")
            return 0
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


async def _serve(args) -> int:
    """启动 Web 查看器 / Launch web viewer."""
    from src.serve import run

    run(args.path, port=args.port)
    return 0


async def _validate(args) -> int:
    """校验 world-pack 结构合法性 / Validate world pack."""
    if not args.path.exists():
        print(f"Error: world-pack not found: {args.path}", file=sys.stderr)
        return 1

    from src.validator.validate import format_report, validate_template

    result = validate_template(args.path)
    print(format_report(result))
    if result.is_valid:
        print("[OK] world-pack validation passed")
    return 0 if result.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
