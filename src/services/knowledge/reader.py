"""KnowledgeReader — 多模态文件加载器 / Multi-modal file loader.

将 knowledge/ 目录下的文件转换为 LlamaIndex Document 对象。

支持：
- Markdown (.md)：自动解析 ``---`` frontmatter，合并到 metadata
- PDF (.pdf)：LlamaIndex 原生支持
- 图片 (.png/.jpg/.jpeg/.webp)：PaddleOCR 提取文字（缺失则降级跳过）
- 视频 (.mp4/.webm)：5 秒 1 帧，关键帧 OCR（缺失则降级跳过）

所有 Document **默认保留全部自定义 metadata** 到向量库：不会把 ``title``/``source_type``/``tags``
等字段丢到 ``excluded_*_metadata_keys`` 里，保证 Retriever 端能基于 metadata 做 where 过滤。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from llama_index.core import Document, SimpleDirectoryReader


class KnowledgeReader:
    """多模态知识库文件加载器."""

    SUPPORTED_TEXT = {".md", ".pdf"}
    SUPPORTED_IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
    SUPPORTED_VIDEO = {".mp4", ".webm"}

    _FRONTMATTER_RE = re.compile(
        r"^---\s*\n(?P<meta>.*?)\n---\s*\n?(?P<body>.*)$",
        re.DOTALL,
    )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def load(self, knowledge_dir: Path) -> list[Document]:
        """加载目录下所有支持的文件."""
        if not knowledge_dir.exists() or not knowledge_dir.is_dir():
            return []

        documents: list[Document] = []
        documents.extend(self._load_text_files(knowledge_dir))
        documents.extend(self._load_images(knowledge_dir))
        documents.extend(self._load_videos(knowledge_dir))
        return documents

    # ------------------------------------------------------------------
    # Text (md / pdf)
    # ------------------------------------------------------------------

    def _load_text_files(self, directory: Path) -> list[Document]:
        """加载 Markdown + PDF.

        - Markdown：直接 rglob 手动读取（避免依赖 llama-index-readers-file 包），
          解析 ``---`` frontmatter，metadata 合并后 chunk 不丢自定义字段。
        - PDF：优先尝试 llama-index-readers-file 的 PDFReader（有就用），没有则
          读取二进制字节写 placeholder metadata，保证上层不会崩。
        """
        documents: list[Document] = []

        # 1) Markdown — 手动逐文件解析
        for md_path in directory.rglob("*.md"):
            try:
                text = md_path.read_text(encoding="utf-8")
            except Exception:
                continue
            fm, body = self._parse_frontmatter(text)
            extra: dict = {
                "file_path": str(md_path),
                "file_name": md_path.name,
                "file_size": md_path.stat().st_size,
                "content_type": "markdown",
                "source_type": self._infer_source_type(md_path, directory),
            }
            if fm:
                extra.update(self._flatten_metadata(fm))

            doc = Document(text=body or text, metadata=extra)
            doc.excluded_llm_metadata_keys = []
            doc.excluded_embed_metadata_keys = []
            documents.append(doc)

        # 2) PDF — 走 SimpleDirectoryReader（llama-index-readers-file 包可选）
        try:
            pdf_reader = None
            try:
                from llama_index.readers.file import PDFReader  # type: ignore

                pdf_reader = PDFReader()
            except ImportError:
                pdf_reader = None

            reader = SimpleDirectoryReader(
                input_dir=str(directory),
                recursive=True,
                required_exts=[".pdf"],
                **({"file_extractor": {".pdf": pdf_reader}} if pdf_reader else {}),
            )
            pdf_docs = reader.load_data()
        except Exception:
            pdf_docs = []

        for d in pdf_docs:
            fp = d.metadata.get("file_path", "")
            extra = {
                "content_type": "pdf",
                "file_name": Path(fp).name,
                "source_type": self._infer_source_type(Path(fp), directory),
            }
            d.metadata = {**(d.metadata or {}), **extra}
            d.excluded_llm_metadata_keys = []
            d.excluded_embed_metadata_keys = []
            documents.append(d)

        return documents

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------

    def _load_images(self, directory: Path) -> list[Document]:
        """图片 → OCR 提取文字."""
        documents: list[Document] = []
        for ext in sorted(self.SUPPORTED_IMAGE):
            for img_path in directory.rglob(f"*{ext}"):
                text = self._ocr_image(img_path)
                if not text:
                    continue
                documents.append(self._make_doc(
                    text=text,
                    fp=img_path,
                    directory=directory,
                    content_type="image",
                ))
        return documents

    # ------------------------------------------------------------------
    # Videos
    # ------------------------------------------------------------------

    def _load_videos(self, directory: Path) -> list[Document]:
        """视频 → 每 5 秒抽 1 帧 OCR."""
        documents: list[Document] = []
        for ext in sorted(self.SUPPORTED_VIDEO):
            for vid_path in directory.rglob(f"*{ext}"):
                text = self._ocr_video(vid_path)
                if not text:
                    continue
                documents.append(self._make_doc(
                    text=text,
                    fp=vid_path,
                    directory=directory,
                    content_type="video",
                ))
        return documents

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_doc(
        self,
        *,
        text: str,
        fp: Path,
        directory: Path,
        content_type: str,
    ) -> Document:
        doc = Document(
            text=text,
            metadata={
                "file_path": str(fp),
                "file_name": fp.name,
                "content_type": content_type,
                "source_type": self._infer_source_type(fp, directory),
            },
        )
        doc.excluded_llm_metadata_keys = []
        doc.excluded_embed_metadata_keys = []
        return doc

    @staticmethod
    def _infer_source_type(fp: Path, directory: Path) -> str:
        """从子目录名推断 ``source_type``（lore / documents / image / video ...）.

        Rules:
          - ``knowledge/lore/xxx.md`` -> ``lore``
          - ``knowledge/documents/xxx.md`` -> ``documents``
          - ``knowledge/images/x.png`` -> ``image``
          - ``knowledge/videos/x.mp4`` -> ``video``
          - 其它 fallback 到父目录名，再不行用 ``unknown``
        """
        try:
            rel = fp.relative_to(directory)
        except ValueError:
            return "unknown"
        parts = [p for p in rel.parts[:-1] if p]  # 去掉 filename
        if not parts:
            return "unknown"
        top = parts[0].lower()
        mapping = {
            "lore": "lore",
            "documents": "documents",
            "images": "image",
            "videos": "video",
        }
        return mapping.get(top, top)

    @classmethod
    def _parse_frontmatter(cls, text: str) -> tuple[dict, str]:
        """解析 Markdown ``---`` frontmatter，返回 ``(yaml_dict, body_text)``.

        先去掉 UTF-8 BOM（0xFEFF），否则 Windows PowerShell ``Out-File -Encoding utf8``
        写出的带 BOM 文件会导致 ``^---`` 正则永远无法命中。
        """
        if not text:
            return {}, text
        if text[0] == "\ufeff":
            text = text[1:]
        m = cls._FRONTMATTER_RE.match(text)
        if not m:
            return {}, text
        raw_meta = m.group("meta")
        body = m.group("body")
        try:
            data = yaml.safe_load(raw_meta)
            if isinstance(data, dict):
                return data, body
        except Exception:
            pass
        return {}, body

    @staticmethod
    def _flatten_metadata(fm: dict) -> dict:
        """把 frontmatter 嵌套 YAML 平铺为 1 层 str 键便于 ChromaDB where 过滤.

        ``tags: [a, b]``  -> ``tags: "a,b"``
        ``author: {name: xx}``  -> ``author_name: xx``
        """
        flat: dict = {}
        for k, v in fm.items():
            if v is None:
                continue
            if isinstance(v, (list, tuple)):
                flat[str(k)] = ",".join(str(x) for x in v if x is not None)
            elif isinstance(v, dict):
                for sub_k, sub_v in v.items():
                    if sub_v is None:
                        continue
                    flat[f"{k}_{sub_k}"] = (
                        ",".join(str(x) for x in sub_v)
                        if isinstance(sub_v, (list, tuple))
                        else str(sub_v)
                    )
            else:
                flat[str(k)] = str(v)
        return flat

    # ── OCR 实现 ────────────────────────────────────────────────────

    @staticmethod
    def _ocr_image(image_path: Path) -> str | None:
        try:
            from paddleocr import PaddleOCR

            ocr = PaddleOCR(lang="ch", show_log=False)
            result = ocr.ocr(str(image_path))
            if result and result[0]:
                return " ".join(line[1][0] for line in result[0])
        except ImportError:
            pass
        except Exception:
            pass
        return None

    @staticmethod
    def _ocr_video(video_path: Path) -> str | None:
        try:
            import cv2  # type: ignore
            import tempfile

            cap = cv2.VideoCapture(str(video_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            frame_interval = max(1, int(fps * 5))  # 每 5 秒抽 1 帧
            texts: list[str] = []
            idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                if idx % frame_interval == 0:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                        cv2.imwrite(f.name, frame)
                        ocr = KnowledgeReader._ocr_image(Path(f.name))
                        if ocr:
                            texts.append(ocr)
                        Path(f.name).unlink(missing_ok=True)
                idx += 1
            cap.release()
            return " ".join(texts) if texts else None
        except ImportError:
            pass
        except Exception:
            pass
        return None
