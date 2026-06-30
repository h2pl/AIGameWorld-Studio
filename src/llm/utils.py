# LLM 管线工具函数 / LLM pipeline utility functions

import re
from pathlib import Path


def write_yaml_entities(target_dir: Path, content: str, prefix: str) -> None:
    """LLM 输出 YAML 序列 → 覆盖骨架文件 / LLM output → overwrite skeleton files.

    LLM 输出是多实体 YAML 序列，每项以 "# <prefix>_N" 注释开头.
    """
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

    # 过滤空项 / Filter empty items
    items = ["\n".join(b).strip() for b in blocks if b]
    items = [item for item in items if item.strip()]
    for i, item in enumerate(items, 1):
        filepath = target_dir / f"{prefix}_{i}.yaml"
        filepath.write_text(item, encoding="utf-8")
