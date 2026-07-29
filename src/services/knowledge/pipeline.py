"""KnowledgePipeline — 文档处理流水线 / Document ingestion pipeline."""

import hashlib
from pathlib import Path

from llama_index.core import Document
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

from .reader import KnowledgeReader


class KnowledgePipeline:
    """知识库文档处理流水线.

    Reader → SentenceSplitter → BGE-M3 Embedding → ChromaDB.
    """

    def __init__(self, world_id: str, chroma_client, sqlite=None):
        self.world_id = world_id
        self.collection_name = f"knowledge_{world_id}"
        self._chroma = chroma_client
        self._sqlite = sqlite
        self._reader = KnowledgeReader()

        self._collection = chroma_client.get_or_create_collection(self.collection_name)

        # BGE-M3 本地路径（ModelScope 下载）
        import os
        _bge_path = os.path.expanduser(
            "~/.cache/huggingface/hub/models/BAAI--bge-m3/snapshots/master"
        )

        self._pipeline = IngestionPipeline(
            transformations=[
                SentenceSplitter(chunk_size=500, chunk_overlap=50),
                HuggingFaceEmbedding(model_name=_bge_path, trust_remote_code=True),
            ],
            vector_store=ChromaVectorStore(chroma_collection=self._collection),
        )

    def ingest(self, documents: list[Document]) -> int:
        """执行流水线，返回 chunk 数."""
        if not documents:
            return 0
        nodes = self._pipeline.run(documents=documents)
        return len(nodes)

    def clear(self):
        """清空知识库索引."""
        self._chroma.delete_collection(self.collection_name)
        self._collection = self._chroma.get_or_create_collection(self.collection_name)

    async def index_directory(self, knowledge_dir: Path) -> dict:
        """索引整个目录，返回统计信息."""
        documents = self._reader.load(knowledge_dir)
        if not documents:
            return {"files": 0, "chunks": 0}

        new_docs = documents
        if self._sqlite:
            new_docs = await self._filter_indexed(documents)

        if not new_docs:
            return {"files": len(documents), "chunks": 0, "skipped": True}

        chunk_count = self.ingest(new_docs)

        if self._sqlite:
            for doc in new_docs:
                fp = doc.metadata.get("file_path", "")
                if fp:
                    await self._mark_indexed(fp)

        return {"files": len(new_docs), "chunks": chunk_count, "total_files": len(documents)}

    async def _filter_indexed(self, documents: list[Document]) -> list[Document]:
        """过滤已索引文件（SHA256 对比）."""
        new_docs = []
        for doc in documents:
            fp = doc.metadata.get("file_path", "")
            if not fp:
                new_docs.append(doc)
                continue
            fh = hashlib.sha256(Path(fp).read_bytes()).hexdigest()
            rows = await self._sqlite.fetch_all(
                "SELECT file_hash FROM knowledge_index WHERE id = ?",
                (f"kb_{self.world_id}_{fp}",),
            )
            if not rows or rows[0].get("file_hash") != fh:
                new_docs.append(doc)
        return new_docs

    async def _mark_indexed(self, file_path: str):
        """标记文件已索引."""
        fh = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
        await self._sqlite.execute(
            """INSERT OR REPLACE INTO knowledge_index
               (id, world_id, file_path, file_hash, content_type, status)
               VALUES (?, ?, ?, ?, ?, 'done')""",
            (
                f"kb_{self.world_id}_{file_path}",
                self.world_id,
                file_path,
                fh,
                Path(file_path).suffix.lstrip("."),
            ),
        )
        await self._sqlite.commit()
