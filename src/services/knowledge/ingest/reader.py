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

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

import yaml
from llama_index.core import Document, SimpleDirectoryReader

from .enrichers import MetadataEnricher


class KnowledgeReader:
    """多模态知识库文件加载器.

    通用元数据（file_path / sha256 / page_number / content_type 等）由本类负责提取。
    主题特定的业务元数据（如编年史卷数）通过 ``enrichers`` 注入，本类不包含任何 IP 专属逻辑。
    """

    SUPPORTED_TEXT = {".md", ".pdf"}
    SUPPORTED_IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
    SUPPORTED_VIDEO = {".mp4", ".webm"}

    def __init__(self, enrichers: list[MetadataEnricher] | None = None):
        """构造 Reader.

        Parameters
        ----------
        enrichers : list[MetadataEnricher] | None
            主题特定的元数据增强器列表，按顺序对每个文件的 metadata 执行增强。
            None 或空列表表示不做业务元数据增强（纯通用模式）。
        """
        self._enrichers: list[MetadataEnricher] = list(enrichers) if enrichers else []

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
                documents.append(
                    self._make_doc(
                        text=text,
                        fp=img_path,
                        directory=directory,
                        content_type="image",
                    )
                )
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
                documents.append(
                    self._make_doc(
                        text=text,
                        fp=vid_path,
                        directory=directory,
                        content_type="video",
                    )
                )
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
                        ",".join(str(x) for x in sub_v) if isinstance(sub_v, (list, tuple)) else str(sub_v)
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
            import tempfile

            import cv2  # type: ignore

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

    # ------------------------------------------------------------------
    # 显式文件加载（供 CLI / API 直接指定文件路径使用）
    # ------------------------------------------------------------------

    # 支持的文件后缀 → Reader 工厂
    _EXT_TO_READER: dict[str, type] = {}

    @classmethod
    def _get_reader_for_ext(cls, ext: str):
        """延迟导入 reader 类（避免启动时触发不存在的包）."""
        if ext in (".pdf",):
            from llama_index.readers.file import PDFReader

            return PDFReader()
        if ext in (".md", ".markdown"):
            from llama_index.readers.file import MarkdownReader

            return MarkdownReader()
        if ext in (".txt",):
            from llama_index.readers.file import FlatReader

            return FlatReader()
        return None

    @staticmethod
    def infer_chronicle_volume(file_name: str) -> str | None:
        """[已废弃] 从文件名识别编年史卷数.

        .. deprecated::
            此逻辑已迁移到 ``enrichers.ChronicleVolumeEnricher``，
            通过 ``KnowledgeReader(enrichers=[...])`` 注入。
            保留此方法仅为向后兼容，后续将移除。
        """
        from .enrichers import ChronicleVolumeEnricher

        enricher = ChronicleVolumeEnricher()
        meta: dict = {}
        enricher.enrich(Path(file_name), meta)
        return meta.get("chronicle_volume")

    def load_documents(
        self,
        files: list[Path],
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[Document]:
        """加载指定的文件列表（串行读取），按后缀选择 reader，附带标准元数据.

        Parameters
        ----------
        files : list[Path]
            要加载的文件路径列表（支持 pdf / md / txt）。
        on_progress : callable, optional
            每完成一个文件后回调 ``on_progress(done_count, total_count)``，
            供上层实时更新 ``kb_index_job.file_done``。

        Returns
        -------
        list[Document]
            每个文件的每页/每段生成一个 Document，metadata 含
            file_path / file_name / file_size / sha256 / content_type /
            source_type / title / page_number / tags 等通用字段。
            主题特定的业务字段由 enrichers 注入（见 ``__init__`` 参数）。
        """
        import logging

        _log = logging.getLogger(__name__)

        # 过滤不存在的文件
        valid_files = [fp for fp in files if fp.exists()]
        total = len(valid_files)
        if total == 0:
            return []

        out: list[Document] = []
        done = 0

        # 串行读取：文件读取不是性能瓶颈（BGE-M3 embedding 才是，占 ~95% 耗时），
        # 无需并行化。并行只在 pipeline._embed_nodes_parallel 处针对 embedding 瓶颈使用。
        for fp in valid_files:
            try:
                docs = self._load_one_file(fp)
                out.extend(docs)
            # pypdf 可能抛 KeyboardInterrupt（BaseException 而非 Exception），必须捕获，
            # 否则一个文件读取失败会让整个多文件 ingest 中断。
            except (Exception, KeyboardInterrupt) as e:
                _log.error("加载失败 %s: %s: %s", fp.name, type(e).__name__, e)
            done += 1
            if on_progress:
                on_progress(done, total)
        return out

    def _load_pdf_page_by_page(self, fp: Path) -> list[Document]:
        """逐页安全提取 PDF 文本（兜底方案）.

        当 ``PDFReader.load_data`` 整本失败时调用。用 pypdf 逐页提取，
        单个坏页（损坏/异常 font 资源）记录警告并跳过，保证整本不丢失。
        返回 LlamaIndex ``Document`` 列表，每页一个，metadata 含 page_label。
        """
        import logging

        _log = logging.getLogger(__name__)
        try:
            from pypdf import PdfReader
        except Exception as e:  # pragma: no cover
            _log.error("pypdf 不可用，无法逐页兜底 %s: %s", fp.name, e)
            return []

        try:
            reader = PdfReader(str(fp))
        except Exception as e:
            _log.error("pypdf 打开失败 %s: %s: %s", fp.name, type(e).__name__, e)
            return []

        docs: list[Document] = []
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            # 注意：pypdf 对某些 malformed PDF 会在 extract_text 内部误触发
            # KeyboardInterrupt（继承 BaseException 而非 Exception），必须显式捕获，
            # 否则会把单个坏页变成整本提取崩溃。
            except (Exception, KeyboardInterrupt) as e:
                _log.warning("跳过坏页 %s p%d: %s: %s", fp.name, i, type(e).__name__, e)
                continue
            if not text.strip():
                continue
            docs.append(Document(text=text, metadata={"page_label": str(i), "file_path": str(fp)}))
        _log.info(
            "逐页兜底提取完成: %s → %d 页有效（共 %d 页）",
            fp.name,
            len(docs),
            len(reader.pages),
        )
        return docs

    def _load_one_file(self, fp: Path) -> list[Document]:
        """加载单个文件 → 返回 Document 列表.

        提取自 ``load_documents`` 供串行逐文件调用，逻辑独立无共享可变状态。
        """
        import logging

        _log = logging.getLogger(__name__)

        if not fp.exists():
            _log.warning("文件不存在: %s", fp)
            return []

        ext = fp.suffix.lower()
        reader = self._get_reader_for_ext(ext)
        if reader is None:
            _log.warning("暂不支持的文件类型 %s: %s", ext, fp.name)
            return []

        try:
            # 给整本读取加超时保护：pypdf 遇到异常 font 资源时可能卡死不返回，
            # 超时（120s）或抛异常都降级到逐页安全提取兜底（坏页跳过，整本不丢）。
            import concurrent.futures as _cf

            with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(reader.load_data, file=fp)
                try:
                    sub_docs = _fut.result(timeout=120)
                except _cf.TimeoutError:
                    _fut.cancel()
                    _log.warning("reader 整本加载超时 %s，降级逐页提取兜底", fp.name)
                    sub_docs = self._load_pdf_page_by_page(fp)
                except (Exception, KeyboardInterrupt) as e:
                    _log.warning(
                        "reader 整本加载失败 %s: %s: %s，降级逐页提取兜底",
                        fp.name,
                        type(e).__name__,
                        e,
                    )
                    sub_docs = self._load_pdf_page_by_page(fp)
        except (Exception, KeyboardInterrupt) as e:
            _log.warning(
                "reader 加载异常 %s: %s: %s，降级逐页提取兜底",
                fp.name,
                type(e).__name__,
                e,
            )
            sub_docs = self._load_pdf_page_by_page(fp)

        file_size = fp.stat().st_size
        sha256 = hashlib.sha256(fp.read_bytes()).hexdigest()

        out: list[Document] = []
        for idx, d in enumerate(sub_docs):
            meta = dict(d.metadata or {})
            page_num = int(meta.get("page_label") or (idx + 1))
            meta.update(
                {
                    "file_path": str(fp),
                    "file_name": fp.name,
                    "file_size": file_size,
                    "sha256": sha256,
                    "content_type": ext.lstrip("."),
                    "source_type": "documents",
                    "title": f"{fp.stem} p{page_num}" if ext == ".pdf" else fp.stem,
                    "page_number": page_num,
                    "tags": [f"ext_{ext.lstrip('.')}"],
                    "related_packs": [],
                }
            )
            # 主题特定的业务元数据增强（编年史卷数 / 地区分类 / 世代等）
            for enricher in self._enrichers:
                meta = enricher.enrich(fp, meta)
            d.metadata = meta
            d.excluded_llm_metadata_keys = []
            d.excluded_embed_metadata_keys = []
            out.append(d)

        _log.info(
            "加载完成: %s → %d segments  %.2fMB  sha256=%s…",
            fp.name,
            len(sub_docs),
            file_size / 1024 / 1024,
            sha256[:12],
        )
        return out
