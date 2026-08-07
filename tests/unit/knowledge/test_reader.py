"""KnowledgeReader 单元测试."""

from pathlib import Path
from unittest import mock

from llama_index.core import Document

from src.services.knowledge.ingest.reader import KnowledgeReader


class TestKnowledgeReaderText:
    """Markdown + PDF 文本文件加载."""

    def test_load_markdown_files(self, tmp_path: Path):
        """加载 Markdown 文件."""
        lore_dir = tmp_path / "lore"
        lore_dir.mkdir()
        (lore_dir / "history.md").write_text("# 世界历史\n\n远古时代，龙族统治大陆。", encoding="utf-8")
        (lore_dir / "geography.md").write_text("# 地理\n\n大陆分为东西两部分。", encoding="utf-8")

        reader = KnowledgeReader()
        docs = reader.load(tmp_path)

        assert len(docs) == 2
        assert all(d.metadata["content_type"] == "markdown" for d in docs)
        texts = [d.text for d in docs]
        assert any("龙族" in t for t in texts)
        assert any("东西两部分" in t for t in texts)

    def test_load_empty_directory(self, tmp_path: Path):
        """空目录返回空列表."""
        reader = KnowledgeReader()
        docs = reader.load(tmp_path)
        assert docs == []

    def test_load_nonexistent_directory(self):
        """目录不存在返回空列表."""
        reader = KnowledgeReader()
        docs = reader.load(Path("/nonexistent/path/12345"))
        assert docs == []

    def test_markdown_preserves_metadata(self, tmp_path: Path):
        """Markdown 文件保留 file_path 元数据."""
        (tmp_path / "test.md").write_text("# Test", encoding="utf-8")
        reader = KnowledgeReader()
        docs = reader.load(tmp_path)
        assert len(docs) == 1
        assert "file_path" in docs[0].metadata
        assert docs[0].metadata["content_type"] == "markdown"

    def test_multiple_directories_recursive(self, tmp_path: Path):
        """递归加载子目录."""
        sub = tmp_path / "lore" / "deep"
        sub.mkdir(parents=True)
        (tmp_path / "root.md").write_text("root", encoding="utf-8")
        (sub / "deep.md").write_text("deep", encoding="utf-8")

        reader = KnowledgeReader()
        docs = reader.load(tmp_path)
        assert len(docs) >= 2


class TestKnowledgeReaderImage:
    """图片 OCR 加载（降级测试）."""

    def test_image_without_paddleocr_skips(self, tmp_path: Path):
        """PaddleOCR 未安装时跳过图片."""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        reader = KnowledgeReader()
        docs = reader.load(tmp_path)
        # PaddleOCR 未安装，应该跳过
        assert all(d.metadata["content_type"] != "image" for d in docs)


class TestKnowledgeReaderVideo:
    """视频 OCR 加载（降级测试）."""

    def test_video_without_opencv_skips(self, tmp_path: Path):
        """opencv 未安装时跳过视频."""
        vid = tmp_path / "test.mp4"
        vid.write_text("fake mp4", encoding="utf-8")

        reader = KnowledgeReader()
        docs = reader.load(tmp_path)
        assert all(d.metadata["content_type"] != "video" for d in docs)


class TestKnowledgeReaderMixed:
    """混合格式测试."""

    def test_mixed_markdown_and_image(self, tmp_path: Path):
        """Markdown + 图片混合目录."""
        (tmp_path / "lore.md").write_text("# Lore", encoding="utf-8")
        img = tmp_path / "img.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        reader = KnowledgeReader()
        docs = reader.load(tmp_path)
        # 至少加载了 Markdown
        assert len(docs) >= 1
        md_docs = [d for d in docs if d.metadata["content_type"] == "markdown"]
        assert len(md_docs) == 1

    def test_subdirectory_excluded_files(self, tmp_path: Path):
        """非支持格式文件被忽略."""
        (tmp_path / "readme.txt").write_text("not supported", encoding="utf-8")
        (tmp_path / "data.json").write_text('{"key": "value"}', encoding="utf-8")

        reader = KnowledgeReader()
        docs = reader.load(tmp_path)
        assert len(docs) == 0


class TestKnowledgeReaderPDF:
    """PDF 加载（Unstructured 优先 + pypdf 兜底）."""

    def _fake_reader(self, docs: list[Document]):
        """构造一个带 load_data 的假 reader."""
        r = mock.MagicMock()
        r.load_data.return_value = docs
        return r

    def test_unstructured_style_page_number_passthrough(self, tmp_path: Path):
        """Unstructured 输出的 page_number 元数据被正确透传并装配通用字段."""
        pdf = tmp_path / "chronicle.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        sub = [
            Document(text="第一卷内容", metadata={"page_number": 1, "category": "NarrativeText"}),
            Document(text="第二卷内容", metadata={"page_number": 2, "category": "Title"}),
        ]
        reader = KnowledgeReader()
        with mock.patch.object(reader, "_get_reader_for_ext", return_value=self._fake_reader(sub)):
            docs = reader._load_one_file(pdf)

        assert len(docs) == 2
        assert all(d.metadata["content_type"] == "pdf" for d in docs)
        assert all(d.metadata["source_type"] == "documents" for d in docs)
        assert docs[0].metadata["page_number"] == 1
        assert docs[1].metadata["page_number"] == 2
        assert "file_path" in docs[0].metadata

    def test_pypdf_style_page_label_compat(self, tmp_path: Path):
        """pypdf / PDFReader 输出的 page_label 元数据被兼容转为 page_number."""
        pdf = tmp_path / "lore.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        sub = [
            Document(text="p3 text", metadata={"page_label": "3"}),
        ]
        reader = KnowledgeReader()
        with mock.patch.object(reader, "_get_reader_for_ext", return_value=self._fake_reader(sub)):
            docs = reader._load_one_file(pdf)

        assert len(docs) == 1
        assert docs[0].metadata["page_number"] == 3

    def test_pdf_unsupported_reader_returns_empty(self, tmp_path: Path):
        """扩展名无对应 reader（_get_reader_for_ext 返回 None）时不崩溃，返回空列表."""
        pdf = tmp_path / "real.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        reader = KnowledgeReader()
        with mock.patch.object(reader, "_get_reader_for_ext", return_value=None):
            docs = reader._load_one_file(pdf)
        assert docs == []

    def test_unstructured_preferred_over_pdfreader(self, tmp_path: Path):
        """PDF 扩展名优先返回 Unstructured 解析器（若可用），否则降级 PDFReader."""
        reader = KnowledgeReader()
        r = reader._get_reader_for_ext(".pdf")
        assert r is not None
        # 可用 unstructured 时返回其包装类；不可用（缺 unstructured_inference）时降级 PDFReader
        assert type(r).__name__ in ("_UnstructuredPDFReader", "PDFReader")
