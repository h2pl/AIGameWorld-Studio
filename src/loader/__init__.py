# 加载层 / Loader Layer
# YAML → SQLite + ChromaDB

from src.loader.chroma_writer import write_lore, write_scenes  # noqa: F401  # Phase 2
from src.loader.db_writer import write_template
from src.loader.yaml_reader import load_all

__all__ = ["load_all", "write_template", "write_lore", "write_scenes", "load_template"]


def load_template(template_dir, db_path):
    """加载模板到 AIGameWorld 存储."""
    data = load_all(template_dir)
    records = write_template(str(db_path), data)
    return records
