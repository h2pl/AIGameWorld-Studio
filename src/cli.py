# CLI 入口 / CLI Entry
# argparse 命令路由，零业务逻辑

import argparse
import sys
from pathlib import Path


def main():
    """主入口 / Main entry point."""
    parser = argparse.ArgumentParser(
        prog="aw-studio",
        description="AIGameWorld Studio - 世界创作工坊 / World Creation Workshop",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate 子命令 / generate subcommand
    gen = subparsers.add_parser("generate", help="生成世界模板 / Generate world template")
    mode = gen.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preset", type=str, help="内置模板名 / Preset template name")
    mode.add_argument("--world", type=str, help="自然语言描述 / Natural language description (Phase 3)")
    gen.add_argument("--pc", type=int, help="主角数量 / Number of PCs")
    gen.add_argument("--actor", type=int, help="配角数量 / Number of actors")
    gen.add_argument("--scene", type=int, help="场景数量 / Number of scenes")
    gen.add_argument("--lore", type=str, help="设定类别 / Lore categories")
    gen.add_argument("-o", "--output", type=Path, default=Path("output"))
    gen.add_argument("--dry-run", action="store_true", help="只生成不加载 / Generate only, skip load")

    # validate 子命令 / validate subcommand
    val = subparsers.add_parser("validate", help="校验模板 / Validate template")
    val.add_argument("template_path", type=Path, help="模板目录路径 / Template directory path")

    # load 子命令 / load subcommand
    ld = subparsers.add_parser("load", help="加载到 AIGameWorld / Load to AIGameWorld")
    ld.add_argument("template_path", type=Path, help="模板目录路径 / Template directory path")
    ld.add_argument("--db", type=Path, help="SQLite 数据库路径 / SQLite DB path")
    ld.add_argument("--chroma", type=Path, help="ChromaDB 实例路径 / ChromaDB instance path")

    args = parser.parse_args()

    if args.command == "generate":
        return _handle_generate(args)
    elif args.command == "validate":
        return _handle_validate(args)
    elif args.command == "load":
        return _handle_load(args)

    return 0


def _handle_generate(args) -> int:
    """处理 generate 命令 / Handle generate command."""
    # TODO: 实现 generator 调用 / Implement generator call
    print("generate: not implemented yet / 尚未实现")
    return 0


def _handle_validate(args) -> int:
    """处理 validate 命令 / Handle validate command."""
    # TODO: 实现 validator 调用 / Implement validator call
    print("validate: not implemented yet / 尚未实现")
    return 0


def _handle_load(args) -> int:
    """处理 load 命令 / Handle load command."""
    # TODO: 实现 loader 调用 / Implement loader call
    print("load: not implemented yet / 尚未实现")
    return 0


if __name__ == "__main__":
    sys.exit(main())
