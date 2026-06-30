# ChromaDB 写入 / ChromaDB Writer
# lore + scene 描述向量化灌入


def write_lore(client, lore_items: list[dict], world_name: str):
    """灌入设定集到 ChromaDB / Load lore into ChromaDB."""
    if not lore_items:
        return
    collection = client.get_or_create_collection(f"lore_{world_name}")
    collection.delete(where={})
    for lore in lore_items:
        chunks = _chunk_text(lore.get("content", ""))
        for i, chunk in enumerate(chunks):
            collection.add(
                documents=[chunk],
                metadatas=[{"lore_id": lore.get("id", ""), "category": lore.get("category", ""), "chunk": i}],
                ids=[f"{lore.get('id', '')}_{i}"],
            )


def write_scenes(client, scenes: list[dict], world_name: str):
    """灌入场景描述到 ChromaDB / Load scene descriptions into ChromaDB."""
    if not scenes:
        return
    collection = client.get_or_create_collection(f"scene_{world_name}")
    collection.delete(where={})
    for scene in scenes:
        collection.add(
            documents=[scene.get("description", "")],
            metadatas=[{"scene_id": scene.get("id", ""), "name": scene.get("name", ""), "type": scene.get("type", "")}],
            ids=[scene.get("id", "")],
        )


# 工具函数 / Utility functions


def _chunk_text(text: str, chunk_size: int = 256) -> list[str]:
    """按段落切分，小段合并 / Split by paragraphs, merge small ones."""
    paragraphs = text.split("\n\n")
    chunks = []
    current = []
    current_len = 0
    for p in paragraphs:
        words = len(p)
        if current_len + words > chunk_size and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(p)
        current_len += words
    if current:
        chunks.append("\n\n".join(current))
    return chunks
