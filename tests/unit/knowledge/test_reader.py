"""KnowledgeReader 单元测试."""

from pathlib import Path

from src.services.knowledge.reader import KnowledgeReader


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
