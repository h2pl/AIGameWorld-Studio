"""
AIGameWorld-Studio · 预置默认主题知识库（genshin / wow_worldview）
===================================================================

做两件事（都是幂等的，可反复执行）：
  1. SQLite knowledge_topic 表写默认主题记录（不存在则插，存在则更新描述/标签）
  2. Qdrant（或 Chroma）向量库建对应空 collection（用户打开 Dashboard 直接能看到）
  3. 同时创建 knowledge-bases/{topic_id}/knowledge 目录骨架（lore/documents/images/videos）

用法（Studio 根目录执行）：
  uv run python scripts/bootstrap_default_topics.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    print("=" * 72)
    print("  AIGameWorld-Studio · 预置默认主题")
    print("=" * 72)
    print(f"  Project: {PROJECT_ROOT}")
    print()

    from src.services.knowledge.manager import KnowledgeManager

    with KnowledgeManager(
        project_root=PROJECT_ROOT,
        created_by="bootstrap",
        bootstrap_default_topics=False,  # 先关，我们要手动调 + 打印过程
    ) as mgr:
        print(f"[1/3] 向量库：type={mgr._factory.config.store_type}  health={mgr._factory.health()}")
        print()

        print("[2/3] 注册默认主题 + 建向量 collection + 知识目录骨架")
        results = mgr.bootstrap_default_topics()
        for r in results:
            t = mgr.get_topic(r["topic_id"])
            t_name = (t or {}).get("name") or r["topic_id"]
            t_chunks = mgr._factory.count(r["topic_id"])
            print(
                f"  - [{r['topic_id']:<14}] {t_name:<12}  "
                f"registered={'Y' if r['registered'] else 'N'}  "
                f"vector={'OK' if r['vector_store_created'] else 'ERR'}  "
                f"chunks_now={t_chunks}"
            )

        print()
        print("[3/3] 当前主题列表（含向量 collection 计数）：")
        for t in mgr.list_topics(include_archived=True):
            print(
                json.dumps(
                    {
                        "topic_id": t["topic_id"],
                        "name": t["name"],
                        "status": t["status"],
                        "chunks": int(t.get("chunks_in_collection") or 0),
                        "tags": t.get("tags") or [],
                    },
                    ensure_ascii=False,
                )
            )

        print()
        cols = mgr._factory.list_collections()
        print(f"向量库中 kb_ 前缀的 collection 共 {len(cols)} 个：")
        for tid, cnt in cols:
            print(f"  - kb_{tid:<14}  chunks={cnt}")

    print()
    print("✅ 预置完成。打开 Qdrant Dashboard 查看 collection：")
    print("   http://127.0.0.1:6333/dashboard")
    print("   或打开 Studio UI 管理主题：")
    print("   http://127.0.0.1:5173/kb")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
