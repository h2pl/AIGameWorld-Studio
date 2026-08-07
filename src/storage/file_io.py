"""FileIO — 知识库磁盘 IO 边界（纯文件读写，零业务）.

对标主项目分层契约：``storage`` 层只负责与外部存储系统的底层通信，
不包含任何业务概念（文档 / 主题 / chunk 等）。

本模块是 manager 之下的**纯文件系统抽象**，只负责：
- 知识库目录骨架创建（lore/documents/images/videos）
- 文件名 Windows 安全化
- 上传字节流 / 粘贴文本的落盘（返回路径 + sha256 + size）
- 文件级 ingest 的去重（按 content hash 判定哪些文件内容已变）

**不触碰任何 DB / repository / 检索 / 索引逻辑**，保持职责单一。
DB 登记（kb_document 元数据写入）由各业务层（service/manager）协调完成。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# 默认知识库文件根目录（相对于 project_root）
_KB_DATA_DIR = "knowledge-bases"


class FileIO:
    """知识库纯文件 IO — 只做文件落盘与目录管理，不含任何业务登记."""

    def __init__(self, *, project_root: Path):
        self._project_root = Path(project_root).resolve()

    # ------------------------------------------------------------------
    # 目录管理
    # ------------------------------------------------------------------

    def knowledge_dir(self, topic_slug: str) -> Path:
        """返回知识库文件目录: project_root/knowledge-bases/{topic_slug}/knowledge."""
        return self._project_root / _KB_DATA_DIR / topic_slug / "knowledge"

    def ensure_knowledge_dir(self, topic_slug: str) -> Path:
        """确保知识库目录骨架存在 (lore/documents/images/videos)."""
        kdir = self.knowledge_dir(topic_slug)
        for sub in ("lore", "documents", "images", "videos"):
            (kdir / sub).mkdir(parents=True, exist_ok=True)
        return kdir

    def topic_dir(self, topic_slug: str) -> Path:
        """返回主题根目录 project_root/knowledge-bases/{topic_slug}."""
        return self._project_root / _KB_DATA_DIR / topic_slug

    # ------------------------------------------------------------------
    # 文件名安全化
    # ------------------------------------------------------------------

    @staticmethod
    def sanitize_filename(name: str) -> str:
        """Windows 文件名安全化：转义特殊字符."""
        for ch in r'<>:"/\|?*':
            name = name.replace(ch, "_")
        return name

    # ------------------------------------------------------------------
    # 上传落盘（纯 IO，不写 DB）
    # ------------------------------------------------------------------

    def save_bytes(
        self,
        topic_slug: str,
        data: bytes,
        source_type: str,
        filename: str,
        *,
        prefix: str = "",
    ) -> dict[str, Any]:
        """保存字节流到 knowledge 目录，返回 (file_path, file_name, sha256, file_size, content_type).

        注意：本方法**不**写入 kb_document 元数据，登记业务由调用方（manager）完成。
        """
        kdir = self.ensure_knowledge_dir(topic_slug)
        dest_dir = kdir / source_type
        if prefix:
            dest_dir = dest_dir / prefix
        dest_dir.mkdir(parents=True, exist_ok=True)

        safe_name = self.sanitize_filename(filename)
        target = dest_dir / safe_name

        # 同名文件追加序号
        if target.exists():
            stem, suffix = target.stem, target.suffix
            idx = 1
            while target.exists():
                target = dest_dir / f"{stem}-{idx}{suffix}"
                idx += 1

        target.write_bytes(data)

        sha256 = hashlib.sha256(data).hexdigest()
        file_size = len(data)
        content_type = Path(filename).suffix.lstrip(".") or "binary"

        return {
            "file_path": str(target),
            "file_name": safe_name,
            "sha256": sha256,
            "file_size": file_size,
            "content_type": content_type,
        }

    def save_text(
        self,
        topic_slug: str,
        text: str,
        source_type: str,
        *,
        file_name: str = "pasted.md",
        prefix: str = "",
    ) -> dict[str, Any]:
        """保存粘贴的文本到 knowledge 目录（纯 IO，不写 DB）."""
        data = text.encode("utf-8")
        return self.save_bytes(topic_slug, data, source_type, file_name, prefix=prefix)

    # ------------------------------------------------------------------
    # 文件级 ingest 的去重（纯 IO，按 content hash 判定）
    # ------------------------------------------------------------------

    def collect_new_files(
        self, topic_slug: str, files: list[Path], known_sha256: set[str] | None = None
    ) -> tuple[list[Path], list[Path]]:
        """按内容 hash 去重：返回 (new_files, skipped_files).

        - 文件不存在 → 跳过（不计入两侧）
        - 若提供 ``known_sha256``（调用方从 kb_document 查出的已 done 内容指纹），
          命中则视为内容未变 → skipped（不重算 embedding）
        - 未提供 ``known_sha256`` 时，仅过滤读不到的文件，全部视为 new

        纯 IO 判定，不查询任何 DB。
        """
        new_files: list[Path] = []
        skipped_files: list[Path] = []
        for fp in files:
            if not fp.exists():
                continue
            try:
                file_bytes = fp.read_bytes()
            except Exception:
                continue
            sha256 = hashlib.sha256(file_bytes).hexdigest()
            if known_sha256 is not None and sha256 in known_sha256:
                skipped_files.append(fp)  # 内容没变 → 跳过
                continue
            new_files.append(fp)
        return new_files, skipped_files
