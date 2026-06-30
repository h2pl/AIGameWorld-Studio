# LLM 管线工具函数 / LLM pipeline utility functions

import re
from pathlib import Path


def write_yaml_entities(target_dir: Path, content: str, prefix: str) -> int:
    """LLM 输出 YAML → 覆盖骨架文件 / LLM output → overwrite skeleton files.

    支持两种 LLM 输出格式:
      1. 有注释分隔: # <prefix>_1 ... # <prefix>_2   (优先)
      2. 无注释分隔: 按 YAML 文档边界拆分 (fallback)

    Returns: 写入的文件数 / Number of files written.
    """
    if not content.strip():
        return 0

    # 优先按注释分割 / Try comment-based split first
    items = _split_by_comments(content, prefix)

    # fallback: 按 YAML 文档边界 / Try YAML doc boundary split
    if len(items) <= 1:
        items = _split_by_docs(content)

    written = 0
    for i, item in enumerate(items, 1):
        if not item.strip():
            continue
        filepath = target_dir / f"{prefix}_{i}.yaml"
        filepath.write_text(item, encoding="utf-8")
        written += 1
    return written


def _split_by_comments(content: str, prefix: str) -> list[str]:
    """按 # <prefix>_N 注释分割 / Split by # <prefix>_N comments."""
    pattern = rf"^#\s*{prefix}_\d+"
    lines = content.split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if re.match(pattern, line.strip()):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)

    return ["\n".join(b).strip() for b in blocks if b]


def _split_by_docs(content: str) -> list[str]:
    """按 YAML 文档边界拆分 / Split by YAML document boundaries.

    尝试: \n---\n 分隔 → 顶层 id: 行分隔
    """
    # 先试 --- 分隔符 / Try --- separator
    docs = re.split(r"\n---\n", content)
    if len(docs) > 1:
        return [d.strip() for d in docs if d.strip()]

    # fallback: 按顶层 id: 行分割（仅无缩进）/ Split at top-level id: lines only
    lines = content.split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        # 只有顶格的 id: 才算新实体 / Only non-indented id: starts new entity
        if line.startswith("id:"):
            if current and _has_content(current):
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current and _has_content(current):
        blocks.append(current)

    if len(blocks) > 1:
        return ["\n".join(b).strip() for b in blocks]

    # 都失败就返回整个内容 / Return as single block
    return [content.strip()]


def _has_content(lines: list[str]) -> bool:
    """检查块是否包含有效内容 / Check if block has meaningful content."""
    return any(ln.strip() and not ln.strip().startswith("#") for ln in lines)
