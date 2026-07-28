"""KnowledgeReader — 多模态文件加载器 / Multi-modal file loader."""

from pathlib import Path

from llama_index.core import Document, SimpleDirectoryReader


class KnowledgeReader:
    """多模态知识库文件加载器.

    将 knowledge/ 目录下的文件转换为 LlamaIndex Document 对象。
    支持 Markdown、PDF（LlamaIndex 原生）、图片（PaddleOCR）、视频（关键帧 OCR）。
    OCR 依赖未安装时降级为纯文本模式。
    """

    SUPPORTED_TEXT = {".md", ".pdf"}
    SUPPORTED_IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
    SUPPORTED_VIDEO = {".mp4", ".webm"}

    def load(self, knowledge_dir: Path) -> list[Document]:
        """加载目录下所有文件."""
        if not knowledge_dir.exists() or not knowledge_dir.is_dir():
            return []

        documents: list[Document] = []

        # Markdown + PDF — LlamaIndex 原生支持
        docs = self._load_text_files(knowledge_dir)
        documents.extend(docs)

        # 图片 — OCR 提取文字
        docs = self._load_images(knowledge_dir)
        documents.extend(docs)

        # 视频 — 关键帧 OCR
        docs = self._load_videos(knowledge_dir)
        documents.extend(docs)

        return documents

    def _load_text_files(self, directory: Path) -> list[Document]:
        """加载 Markdown + PDF."""
        try:
            reader = SimpleDirectoryReader(
                input_dir=str(directory),
                recursive=True,
                required_exts=list(self.SUPPORTED_TEXT),
            )
            docs = reader.load_data()
            for d in docs:
                fp = d.metadata.get("file_path", "")
                ext = Path(fp).suffix.lower()
                d.metadata["content_type"] = "markdown" if ext == ".md" else "pdf"
            return docs
        except Exception:
            return []

    def _load_images(self, directory: Path) -> list[Document]:
        """加载图片，OCR 提取文字."""
        documents: list[Document] = []
        for ext in self.SUPPORTED_IMAGE:
            for img_path in directory.rglob(f"*{ext}"):
                text = self._ocr_image(img_path)
                if text:
                    documents.append(
                        Document(
                            text=text,
                            metadata={
                                "file_path": str(img_path),
                                "content_type": "image",
                            },
                        )
                    )
        return documents

    def _load_videos(self, directory: Path) -> list[Document]:
        """加载视频，抽关键帧 OCR."""
        documents: list[Document] = []
        for ext in self.SUPPORTED_VIDEO:
            for vid_path in directory.rglob(f"*{ext}"):
                text = self._ocr_video(vid_path)
                if text:
                    documents.append(
                        Document(
                            text=text,
                            metadata={
                                "file_path": str(vid_path),
                                "content_type": "video",
                            },
                        )
                    )
        return documents

    # ── OCR 实现 ──

    def _ocr_image(self, image_path: Path) -> str | None:
        """图片 OCR."""
        try:
            from paddleocr import PaddleOCR

            ocr = PaddleOCR(lang="ch")
            result = ocr.ocr(str(image_path))
            if result and result[0]:
                return " ".join(line[1][0] for line in result[0])
        except ImportError:
            pass
        except Exception:
            pass
        return None

    def _ocr_video(self, video_path: Path) -> str | None:
        """视频关键帧 OCR."""
        try:
            import cv2
            import tempfile

            cap = cv2.VideoCapture(str(video_path))
            fps = cap.get(cv2.CAP_PROP_FPS)
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
                        text = self._ocr_image(Path(f.name))
                        if text:
                            texts.append(text)
                        Path(f.name).unlink(missing_ok=True)
                idx += 1
            cap.release()
            return " ".join(texts) if texts else None
        except ImportError:
            pass
        except Exception:
            pass
        return None
