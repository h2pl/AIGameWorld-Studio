"""KnowledgeReader 集成测试 — 真实文件场景."""

from pathlib import Path

import pytest

from src.services.knowledge.reader import KnowledgeReader


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    """创建模拟知识库目录."""
    kd = tmp_path / "knowledge"
    lore = kd / "lore"
    lore.mkdir(parents=True)

    # 世界观 Markdown 文件
    (lore / "history.md").write_text(
        "# 世界历史\n\n"
        "远古时代，龙族统治大陆，人类只是卑微的仆从。\n"
        "千年战争后，人类崛起，龙族退隐深山。\n",
        encoding="utf-8",
    )
    (lore / "factions.md").write_text(
        "# 势力\n\n"
        "王国联盟：由三大王国组成的人类联合体。\n"
        "暗影议会：地下组织，操控政治。\n",
        encoding="utf-8",
    )
    (lore / "geography.md").write_text(
        "# 地理\n\n"
        "大陆分为东西两部分，中央山脉横贯南北。\n"
        "东部平原适合农耕，西部荒漠矿产丰富。\n",
        encoding="utf-8",
    )

    return kd


class TestReaderIntegration:
    """端到端加载测试."""

    def test_load_complete_knowledge_dir(self, knowledge_dir: Path):
        """加载完整知识库目录."""
        reader = KnowledgeReader()
        docs = reader.load(knowledge_dir)

        assert len(docs) == 3
        assert all(d.metadata["content_type"] == "markdown" for d in docs)

        texts = {d.text for d in docs}
        assert any("龙族" in t for t in texts)
        assert any("王国联盟" in t for t in texts)
        assert any("中央山脉" in t for t in texts)

    def test_all_docs_have_required_metadata(self, knowledge_dir: Path):
        """所有文档都有必要元数据."""
        reader = KnowledgeReader()
        docs = reader.load(knowledge_dir)

        for d in docs:
            assert "file_path" in d.metadata
            assert "content_type" in d.metadata
            assert d.metadata["content_type"] in ("markdown", "pdf", "image", "video")
            assert d.text.strip()

    def test_documents_are_valid_llama_index_documents(self, knowledge_dir: Path):
        """Document 是合法的 LlamaIndex Document."""
        from llama_index.core import Document

        reader = KnowledgeReader()
        docs = reader.load(knowledge_dir)

        for d in docs:
            assert isinstance(d, Document)
            assert isinstance(d.text, str)
            assert isinstance(d.metadata, dict)

    def test_load_twice_returns_same_count(self, knowledge_dir: Path):
        """两次加载返回相同数量（幂等）."""
        reader = KnowledgeReader()
        docs1 = reader.load(knowledge_dir)
        docs2 = reader.load(knowledge_dir)
        assert len(docs1) == len(docs2)

    def test_content_not_truncated(self, knowledge_dir: Path):
        """文档内容完整，未被截断."""
        reader = KnowledgeReader()
        docs = reader.load(knowledge_dir)

        history = [d for d in docs if "历史" in d.text]
        assert len(history) == 1
        assert "千年战争" in history[0].text
        assert "龙族退隐深山" in history[0].text
