# CLI 入口 / CLI Entry
# 生成管线: CLI params → LangGraph → LLM fill → validate → world-pack

# 标准库导入
import argparse
import asyncio
import os
import sys
from pathlib import Path

# 默认生成参数配置（各实体数量 + LLM 重试次数）
_DEFAULTS = {"pc": 2, "actor": 2, "scene": 2, "item": 5, "scene_object": 2, "lore": 1, "max_retries": 3}

# CLI 帮助示例文本（generate / kb / import 用法）
_EXAMPLES = """
示例 / Examples:
  aw-studio generate -i                                         # 交互式输入所有参数
  aw-studio generate --name 仙剑世界 --theme 仙侠修真           # 指定主题
  aw-studio generate --pc 4 --actor 6 --scene 3                 # 指定数量
  aw-studio generate --skeleton-only                            # 仅骨架（不调 LLM）
  aw-studio validate world-packs/custom/my_world                # 校验 world-pack

  知识库 / Knowledge base:
  aw-studio kb index my_world                                   # 增量索引 world-pack 的 knowledge/ 目录
  aw-studio kb index my_world --force                           # 全量重建（先清空再入库）
  aw-studio kb search my_world "索伦的弱点" -k 5                 # 语义检索，返回前 5 条
  aw-studio kb search my_world "索伦的弱点" --meta              # 连同 metadata + relevance 一起输出
  aw-studio kb clear my_world                                   # 清空 my_world 的知识库 collection
  aw-studio kb stats my_world                                   # 查看统计（chunk 数 / 目录）

  导入到 AIGameWorld 引擎:
  aw import world-packs/custom/my_world                         # world-pack → Domain → SQLite + ChromaDB
"""

# 同步主入口（包装 asyncio.run，捕获 Ctrl+C）


def main():
    """主入口 / Main entry point."""
    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


# 异步主入口：构建 argparse 解析器 + 按子命令分发到对应 handler
async def _main() -> int:
    parser = argparse.ArgumentParser(
        prog="aw-studio",
        description="AIGameWorld Studio — world-pack 创作工坊 / World Pack Creation Workshop",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # === generate 子命令：LLM 生成 world-pack ===
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

    # === validate 子命令：校验 world-pack 结构合法性 ===
    # --- validate world-pack ---
    val = sub.add_parser(
        "validate",
        help="校验 world-pack / Validate world pack",
        epilog="示例: aw-studio validate world-packs/custom/my_world",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    val.add_argument("path", type=Path, help="world-pack 目录 / World pack directory")

    # === serve 子命令：启动 YAML Web 查看器 ===
    # --- serve world-pack viewer ---
    srv = sub.add_parser(
        "serve",
        help="YAML Web 查看器 / Launch YAML web viewer",
        epilog="示例: aw-studio serve  |  aw-studio serve world-packs/custom --port 9999",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    srv.add_argument(
        "path", type=str, nargs="?", default="world-packs/custom", help="pack 目录 (default: world-packs/custom/)"
    )
    srv.add_argument("--port", type=int, default=8888, help="端口 (default: 8888)")

    # === mcp 子命令：知识库 MCP server（stdio，供主项目 AIGameWorld backend 连接）===
    mcp_cmd = sub.add_parser(
        "mcp",
        help="知识库 MCP server（stdio）/ Knowledge-base MCP server (stdio)",
        epilog=(
            "示例:\n"
            "  aw-studio mcp            # 以 stdio 启动 MCP server，被主项目 client 拉起\n"
            "  python -m src.mcp_server # 等价"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mcp_cmd.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="SQLite / 数据根目录 (default: data)",
    )
    mcp_cmd.add_argument(
        "--vector-store",
        type=str,
        default=None,
        help="向量库后端 qdrant|chroma|sqlite（默认读 KB_VECTOR_STORE，否则 qdrant）",
    )

    # ── kb: knowledge base ──────────────────────────────────────────
    kb = sub.add_parser(
        "kb",
        help="知识库管理 / Knowledge base (index/search/clear/stats/eval)",
        epilog=(
            "示例:\n"
            "  aw-studio kb index my_world\n"
            "  aw-studio kb index my_world --force\n"
            '  aw-studio kb search my_world "boss 位置" -k 5 --meta\n'
            "  aw-studio kb clear my_world\n"
            "  aw-studio kb stats my_world\n"
            "  aw-studio kb eval wow_chronicle_test --top-k 10 --json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kb_sub = kb.add_subparsers(dest="kb_command", metavar="SUBCOMMAND")
    kb_sub.required = True

    # === kb index 子命令：增量/全量索引 knowledge/ 目录 ===
    kb_idx = kb_sub.add_parser(
        "index",
        help="增量/全量索引 world-pack knowledge/ 目录 / Index knowledge dir",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kb_idx.add_argument("world_id", type=str, help="world id / pack id")
    kb_idx.add_argument(
        "-d",
        "--dir",
        type=Path,
        default=None,
        help="world-pack 根目录（默认 world-packs/custom/<world_id>）",
    )
    kb_idx.add_argument(
        "--force",
        action="store_true",
        help="全量重建：先清空 collection 再重新索引（默认：增量 SHA256 更新）",
    )
    kb_idx.add_argument(
        "--chroma-path",
        type=Path,
        default=Path("data/chroma"),
        help="ChromaDB persist 目录 (default: data/chroma)",
    )

    # === kb search 子命令：语义检索知识库 ===
    kb_s = kb_sub.add_parser(
        "search",
        help="语义检索 / Semantic search in knowledge base",
    )
    kb_s.add_argument("world_id", type=str, help="world id / pack id")
    kb_s.add_argument("query", type=str, help="自然语言查询")
    kb_s.add_argument("-k", "--top-k", type=int, default=5, help="返回前 N 条 (default: 5)")
    kb_s.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="最小 cosine similarity 阈值 0~1 (default: 0)",
    )
    kb_s.add_argument(
        "--meta",
        action="store_true",
        help="输出 metadata + relevance（默认只输出文本）",
    )
    kb_s.add_argument(
        "--chroma-path",
        type=Path,
        default=Path("data/chroma"),
        help="ChromaDB persist 目录 (default: data/chroma)",
    )

    # === kb clear 子命令：清空知识库 collection ===
    kb_clr = kb_sub.add_parser("clear", help="清空知识库 collection / Clear knowledge collection")
    kb_clr.add_argument("world_id", type=str, help="world id / pack id")
    kb_clr.add_argument(
        "--chroma-path",
        type=Path,
        default=Path("data/chroma"),
        help="ChromaDB persist 目录 (default: data/chroma)",
    )

    # === kb stats 子命令：知识库 chunk/文件数统计 ===
    kb_st = kb_sub.add_parser("stats", help="知识库统计 / Knowledge base stats")
    kb_st.add_argument("world_id", type=str, help="world id / pack id")
    kb_st.add_argument(
        "-d",
        "--dir",
        type=Path,
        default=None,
        help="world-pack 根目录（默认 world-packs/custom/<world_id>）",
    )
    kb_st.add_argument(
        "--chroma-path",
        type=Path,
        default=Path("data/chroma"),
        help="ChromaDB persist 目录 (default: data/chroma)",
    )

    # === kb eval 子命令：RAG 离线评估（recall@k / MRR / hit_rate@k）===
    kb_ev = kb_sub.add_parser(
        "eval",
        help="RAG 离线评估 / Offline RAG evaluation (recall@k / MRR / hit_rate@k)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '评估集: knowledge-bases/<topic>/eval/qa.jsonl（每行 {"query":..., '
            '"expected_doc_ids":[...]}，格式见 docs/dev/spec-rag-eval.md）\n'
            "示例:\n"
            "  aw-studio kb eval wow_chronicle_test\n"
            "  aw-studio kb eval wow_chronicle_test --top-k 10\n"
            "  aw-studio kb eval wow_chronicle_test --json"
        ),
    )
    kb_ev.add_argument("world_id", type=str, help="world id / pack id / topic slug")
    kb_ev.add_argument(
        "-k",
        "--top-k",
        type=int,
        default=5,
        help="检索 top_k，指标基于此 (default: 5)",
    )
    kb_ev.add_argument(
        "--eval-file",
        type=Path,
        default=None,
        help="评估集 JSONL 路径（默认 knowledge-bases/<world_id>/eval/qa.jsonl）",
    )
    kb_ev.add_argument(
        "--json",
        action="store_true",
        help="输出 Machine-readable JSON（便于 CI / 前后对比存档）",
    )
    kb_ev.add_argument(
        "--query-rewrite",
        action="store_true",
        help="启用 Query 改写（multi_query，LLM 不可用时自动降级）",
    )
    kb_ev.add_argument(
        "--save",
        type=Path,
        default=None,
        help="把 JSON 评估报告保存到文件（供前后对比存档，建议 .json）",
    )
    kb_ev.add_argument(
        "--compare",
        type=Path,
        default=None,
        help="与历史 JSON 报告对比，输出指标差异（delta）",
    )
    kb_ev.add_argument(
        "--chroma-path",
        type=Path,
        default=Path("data/chroma"),
        help="ChromaDB persist 目录 (default: data/chroma)",
    )

    # === kb gen-eval-from-audit 子命令：从真实查询日志构建评估集 ===
    kb_ge = kb_sub.add_parser(
        "gen-eval-from-audit",
        help="从 kb_audit 真实查询日志构建评估集（弱监督）/ Build eval set from audit logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "从线上检索审计日志提取真实用户 query，用检索 top1 弱标注期望文档，"
            "写入评估集 JSONL。\n"
            "示例:\n"
            "  aw-studio kb gen-eval-from-audit wow_chronicle_test\n"
            "  aw-studio kb gen-eval-from-audit wow_chronicle_test --limit 200 --out /tmp/eval.jsonl"
        ),
    )
    kb_ge.add_argument("world_id", type=str, help="world id / pack id / topic slug")
    kb_ge.add_argument(
        "--limit",
        type=int,
        default=200,
        help="最多扫描的审计日志条数 (default: 200)",
    )
    kb_ge.add_argument(
        "--min-chars",
        type=int,
        default=2,
        help="query 最小长度过滤 (default: 2)",
    )
    kb_ge.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出评估集 JSONL 路径（默认 knowledge-bases/<world_id>/eval/qa.jsonl，覆盖写）",
    )
    kb_ge.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印构建结果，不写文件",
    )
    kb_ge.add_argument(
        "--chroma-path",
        type=Path,
        default=Path("data/chroma"),
        help="ChromaDB persist 目录 (default: data/chroma)",
    )

    # === kb llm-eval 子命令：LLM-as-judge 检索质量评估 ===
    kb_le = kb_sub.add_parser(
        "llm-eval",
        help="LLM-as-judge 检索质量评估 / LLM-as-judge retrieval quality (context_precision/relevancy)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "用 LLM 判分检索返回的 context 质量（不依赖人工 expected，可评估真实查询）。\n"
            "示例:\n"
            "  aw-studio kb llm-eval wow_chronicle_test\n"
            "  aw-studio kb llm-eval wow_chronicle_test --top-k 5 --eval-file /tmp/qa.jsonl --json"
        ),
    )
    kb_le.add_argument("world_id", type=str, help="world id / pack id / topic slug")
    kb_le.add_argument(
        "-k",
        "--top-k",
        type=int,
        default=5,
        help="检索 top_k (default: 5)",
    )
    kb_le.add_argument(
        "--eval-file",
        type=Path,
        default=None,
        help="评估集 JSONL 路径（默认 knowledge-bases/<world_id>/eval/qa.jsonl）",
    )
    kb_le.add_argument(
        "--max-contexts",
        type=int,
        default=5,
        help="每条 query 送 LLM 判分的最大段落数 (default: 5)",
    )
    kb_le.add_argument(
        "--json",
        action="store_true",
        help="输出 Machine-readable JSON",
    )
    kb_le.add_argument(
        "--chroma-path",
        type=Path,
        default=Path("data/chroma"),
        help="ChromaDB persist 目录 (default: data/chroma)",
    )

    # 解析命令行参数
    args = parser.parse_args()

    # generate --interactive 分支：进入交互模式逐字段输入
    if args.command == "generate" and args.interactive:
        args = _interactive_prompt(gen, args)
        if args is None:
            return 0  # 用户取消 / User cancelled

    # mcp 子命令分发：设置环境变量后启动 MCP server（stdio）
    if args.command == "mcp":
        return await _mcp(args)

    # kb 子命令分发：按 kb_command 映射到对应 handler
    if args.command == "kb":
        _kb_handlers = {
            "index": _kb_index,
            "search": _kb_search,
            "clear": _kb_clear,
            "stats": _kb_stats,
            "eval": _kb_eval,
            "gen-eval-from-audit": _kb_gen_eval_from_audit,
            "llm-eval": _kb_llm_eval,
        }
        return await _kb_handlers[args.kb_command](args)

    # 主命令分发：generate / validate / serve
    return await {"generate": _generate, "validate": _validate, "serve": _serve}[args.command](args)


# ═══════════════════════════════════════════════════════════════
# Interactive prompt
# ═══════════════════════════════════════════════════════════════

# 交互模式字段定义：(属性名, 提示标签, 默认值, 类型转换)
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


# 单字段交互输入提示：支持默认值 + 类型转换 + 数字校验重试
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


# 交互式参数输入流程：逐字段提示 → 校验必填 → 组装 Namespace 返回
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


# generate 命令 handler：骨架模式直接输出，完整模式调 LLM 管线
async def _generate(args) -> int:
    """LLM 管线生成 world-pack / LLM pipeline generate world-pack."""
    from src.domain.params import GenerateParams
    from src.services.generator.filler import generate_world_pack
    from src.services.generator.world_pack import generate_skeleton

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


# serve 命令 handler：转发到 src.serve.run 启动 Web UI
async def _serve(args) -> int:
    """启动 YAML Web 查看器 / Launch YAML web viewer."""
    from src.serve import run

    run(args.path, port=args.port)
    return 0


# mcp 命令 handler：设置环境后启动知识库 MCP server（stdio）
async def _mcp(args) -> int:
    """启动知识库 MCP server（stdio transport）.

    被主项目 AIGameWorld backend 作为独立进程拉起；通过环境变量把
    data-dir / vector-store 透传给 src.mcp_server 的构造逻辑。
    """
    if args.data_dir:
        os.environ["STUDIO_DATA_DIR"] = str(Path(args.data_dir).resolve())
    if getattr(args, "vector_store", None):
        os.environ["KB_VECTOR_STORE"] = args.vector_store
    from src.mcp_server import main as mcp_main

    mcp_main()
    return 0


# validate 命令 handler：调用 validator 校验 YAML 结构并输出报告
async def _validate(args) -> int:
    """校验 world-pack 结构合法性 / Validate world pack."""
    if not args.path.exists():
        print(f"Error: world-pack not found: {args.path}", file=sys.stderr)
        return 1

    from src.services.validator import format_report, validate_template

    result = validate_template(args.path)
    print(format_report(result))
    if result.is_valid:
        print("[OK] world-pack validation passed")
    return 0 if result.is_valid else 1


# ═══════════════════════════════════════════════════════════════
# Knowledge base (kb) handlers
# ═══════════════════════════════════════════════════════════════


def _make_kb_manager(args):
    """构造 KnowledgeManager（新版：组合 Pipeline + Factory + Store）."""
    from src.services.knowledge import KnowledgeManager

    topic_id = getattr(args, "topic_id", None) or getattr(args, "world_id", "default")
    return KnowledgeManager(
        project_root=Path.cwd(),
        auto_init_schema=True,
        created_by="cli",
    ), topic_id


# kb index handler：增量/全量索引 knowledge/ 目录并输出统计
async def _kb_index(args) -> int:
    """索引 knowledge/ 目录.

    --force: 清空 collection + 索引表后再全量重建；默认增量 SHA256。
    """
    kb, topic_id = _make_kb_manager(args)
    if args.force:
        print(f"[kb] force reindex topic={topic_id}")
    else:
        print(f"[kb] incremental index topic={topic_id}")

    result = kb.index(topic_id, force=bool(args.force))
    if not result.get("ok"):
        err = result.get("error", "unknown error")
        print(f"[FAIL] {err}", file=sys.stderr)
        return 1

    info = result.get("indexed", {})
    files = info.get("files", 0)
    chunks = info.get("chunks", 0)
    skipped = info.get("skipped_files", 0) > 0 and info.get("new_files", 0) == 0
    total_chunks = result.get("total_chunks", 0)
    if skipped:
        print(f"[OK] 无变更，跳过（所有文件 SHA256 未变）。当前 chunks = {total_chunks}")
    else:
        print(f"[OK] 索引文件 {files} 个 → {chunks} chunks。当前总数 = {total_chunks}")
    kb.close()
    return 0


# kb search handler：按 query 做语义检索，按 --meta 决定输出格式
async def _kb_search(args) -> int:
    """语义检索.

    默认纯文本输出；加 --meta 显示 metadata + relevance score。
    """
    import json as _json

    kb, topic_id = _make_kb_manager(args)
    _SYS_META_KEYS = {
        "_node_content",
        "_node_type",
        "doc_id",
        "document_id",
        "ref_doc_id",
    }

    def _clean_meta(m: dict) -> dict:
        return {k: v for k, v in (m or {}).items() if k not in _SYS_META_KEYS}

    hits = kb.search_with_meta(
        topic_id,
        args.query,
        top_k=args.top_k,
        min_score=args.min_score,
    )
    if not hits:
        print("(no hits)")
        kb.close()
        return 0
    for i, h in enumerate(hits, 1):
        score = f"{h['score_cosine_sim']:.3f}"
        dist = f"{h['distance']:.4f}"
        meta = _clean_meta(h.get("metadata") or {})
        title = meta.get("title") or meta.get("file_name") or meta.get("file_path") or "-"
        source_type = meta.get("source_type", "-")
        print(f"[{i}] score={score} dist={dist} type={source_type}  {title}")
        print(f"    {h['text'][:300]}{'...' if len(h['text']) > 300 else ''}")
        if args.meta and meta:
            print(f"    meta: {_json.dumps(meta, ensure_ascii=False, default=str)}")
        print()
    kb.close()
    return 0


# kb clear handler：清空指定 topic 的向量 collection（不删除源文件）
async def _kb_clear(args) -> int:
    """清空知识库 collection（不删文件）."""
    kb, topic_id = _make_kb_manager(args)
    stats_before = kb.stats(topic_id)
    print(f"[kb] clear collection={stats_before['collection']}")
    result = kb.clear(topic_id)
    print(f"[OK] {result}")
    kb.close()
    return 0


# kb stats handler：输出知识库 chunk 数 / 目录等统计信息 (JSON)
async def _kb_stats(args) -> int:
    """打印知识库统计."""
    import json as _json

    kb, topic_id = _make_kb_manager(args)
    stats = kb.stats(topic_id)
    print(_json.dumps(stats, ensure_ascii=False, indent=2))
    kb.close()
    return 0


# kb eval handler：RAG 离线评估（recall@k / MRR / hit_rate@k）
async def _kb_eval(args) -> int:
    """对指定 topic 的评估集跑 recall@k / MRR / hit_rate@k.

    评估集: knowledge-bases/<world_id>/eval/qa.jsonl（每行一条 golden query）。
    复用 KnowledgeManager.search_with_meta（min_score=0.0，与线上一致），
    只算 doc 级召回。--json 输出 Machine-readable 供 CI / 前后对比。
    """
    kb, topic_id = _make_kb_manager(args)

    from src.services.knowledge.eval import (
        _default_eval_path,
        format_report,
        load_queries,
    )

    eval_file = args.eval_file or _default_eval_path(Path.cwd(), topic_id)
    try:
        queries = load_queries(eval_file)
    except (FileNotFoundError, ValueError) as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        kb.close()
        return 1

    # 确保 topic 已索引；未索引时给出提示
    stats = kb.stats(topic_id)
    chunk_count = stats.get("chunks", 0) or stats.get("total_chunks", 0)
    if chunk_count == 0:
        print(
            f"[WARN] topic={topic_id} 尚无已索引 chunk（{stats.get('collection', '?')}），"
            f"建议先运行: aw-studio kb index {topic_id} --force",
            file=sys.stderr,
        )

    print(f"[kb] eval topic={topic_id} top_k={args.top_k} eval_file={eval_file}")
    import json as _json

    from src.services.knowledge.eval import compare_reports, evaluate

    report = evaluate(
        kb,
        topic_id,
        queries,
        top_k=args.top_k,
        query_rewrite=bool(getattr(args, "query_rewrite", False)),
    )
    print(format_report(report, json_out=bool(getattr(args, "json", False))))

    # --compare：与历史报告对比输出 delta
    if getattr(args, "compare", None):
        cmp_path = args.compare
        if not cmp_path.exists():
            print(f"[FAIL] 对比报告不存在: {cmp_path}", file=sys.stderr)
            kb.close()
            return 1
        try:
            old = _json.loads(cmp_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[FAIL] 无法解析对比报告 {cmp_path}: {e}", file=sys.stderr)
            kb.close()
            return 1
        print()
        print(compare_reports(report, old))

    # --save：保存 JSON 报告供前后对比存档
    if getattr(args, "save", None):
        save_path = args.save
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(
            _json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[saved] 评估报告 -> {save_path}")

    kb.close()
    return 0


# kb gen-eval-from-audit handler：从 kb_audit 真实查询日志构建评估集（弱监督）
async def _kb_gen_eval_from_audit(args) -> int:
    """从线上审计日志提取真实 query，用检索 top1 弱标注期望文档，写入评估集 JSONL.

    企业级评估集第一优先来源是真实查询（比手写更贴近线上分布）。
    注意：弱标注存在"自我印证"偏差（expected 来自检索自身 top1），建议人工抽检后使用。
    """
    import json as _json

    from src.services.knowledge.eval import _default_eval_path, build_queries_from_audit

    kb, topic_id = _make_kb_manager(args)

    out_path = args.out or _default_eval_path(Path.cwd(), topic_id)
    try:
        queries = build_queries_from_audit(
            kb,
            topic_id,
            limit=int(getattr(args, "limit", 200)),
            min_chars=int(getattr(args, "min_chars", 2)),
        )
    except ValueError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        kb.close()
        return 1

    print(f"[kb] 从审计日志构建评估集 topic={topic_id}: {len(queries)} 条")

    if getattr(args, "dry_run", False):
        for q in queries[:20]:
            print(f"  {q['query']!r}  ->  {q['expected']}")
        if len(queries) > 20:
            print(f"  ... 共 {len(queries)} 条")
        kb.close()
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for q in queries:
            rec = {
                "query": q["query"],
                "expected_doc_ids": q["expected"],
                "note": q.get("note", "from_audit"),
            }
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[saved] 评估集 -> {out_path}（{len(queries)} 条，weak-label）")
    print("[hint] 建议人工抽检/精修 expected 后，再运行: aw-studio kb eval <topic>")
    kb.close()
    return 0


# kb llm-eval handler：LLM-as-judge 检索质量评估（context_precision / relevancy）
async def _kb_llm_eval(args) -> int:
    """用 LLM 判分检索返回的 context 质量.

    对评估集每条 query 检索，送 LLM 判分 context_precision（逐段相关）+ context_relevancy
    （整体相关）。LLM 不可用时对应指标为 N/A，不阻塞。
    """
    from src.services.knowledge.eval import (
        _default_eval_path,
        format_llm_report,
        llm_evaluate,
        load_queries,
    )

    kb, topic_id = _make_kb_manager(args)
    eval_file = args.eval_file or _default_eval_path(Path.cwd(), topic_id)
    try:
        queries = load_queries(eval_file)
    except (FileNotFoundError, ValueError) as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        kb.close()
        return 1

    print(f"[kb] llm-eval topic={topic_id} top_k={args.top_k} eval_file={eval_file}")
    report = llm_evaluate(
        kb,
        topic_id,
        queries,
        top_k=args.top_k,
        max_contexts=int(getattr(args, "max_contexts", 5)),
    )
    print(format_llm_report(report, json_out=bool(getattr(args, "json", False))))
    kb.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
