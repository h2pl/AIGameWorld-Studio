"""KnowledgePipeline 单元测试."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.services.knowledge.pipeline import KnowledgePipeline


@pytest.fixture
def mock_chroma():
    """Mock ChromaClient."""
    client = MagicMock()
    collection = MagicMock()
    client.get_collection.return_value = collection
    client.delete_collection.return_value = None
    return client


@pytest.fixture
def pipeline(mock_chroma):
    """创建测试用 pipeline."""
    return KnowledgePipeline("test_world", mock_chroma)


class TestPipelineIngest:
    """流水线执行测试."""

    def test_ingest_empty_returns_zero(self, pipeline):
        """空文档列表返回 0."""
        assert pipeline.ingest([]) == 0

    def test_clear_deletes_collection(self, pipeline, mock_chroma):
        """清空删除并重建 collection."""
        pipeline.clear()
        mock_chroma.delete_collection.assert_called_once_with("knowledge_test_world")
        mock_chroma.get_collection.assert_called_with("knowledge_test_world")


class TestPipelineIndexDirectory:
    """目录索引测试."""

    @pytest.mark.asyncio
    async def test_empty_directory(self, pipeline, tmp_path):
        """空目录返回零文件."""
        result = await pipeline.index_directory(tmp_path)
        assert result == {"files": 0, "chunks": 0}

    @pytest.mark.asyncio
    async def test_nonexistent_directory(self, pipeline):
        """不存在目录返回零文件."""
        result = await pipeline.index_directory(Path("/nonexistent/12345"))
        assert result == {"files": 0, "chunks": 0}
