r"""一键索引魔兽世界编年史到 kb_wow_worldview collection（BGE-M3 embedding + Qdrant）.

做的事情：
  1. 实例化 KnowledgeManager（自动跑 migrations、初始化 Qdrant Factory）
  2. 调用 manager.index("wow_worldview")，索引 knowledge-bases/wow_worldview/knowledge 下所有 Markdown
     - 解析文件 → 切 chunk（1024 字/块，200 字 overlap）
     - 通过 sentence-transformers 加载 BAAI/bge-m3 计算 embedding
     - 写入 Qdrant 的 kb_wow_worldview collection
     - SQLite 写入 kb_document / kb_chunk / kb_index_job 元数据（SHA256 增量去重）
  3. 打印 chunks 统计，最后用 5 个典型问题做语义检索验证（可选）

运行：
  cd E:\Projects\AIGameWorld-Studio
  uv run python scripts/ingest_wow_chronicles.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ------- 必须在任何网络导入前设置 no_proxy，避免 qdrant-client 走代理 -------
for _nk in ("NO_PROXY", "no_proxy"):
    _v = os.environ.get(_nk, "")
    os.environ[_nk] = (_v + ",127.0.0.1,localhost,::1") if _v else "127.0.0.1,localhost,::1"
for _nk in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_nk, None)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.knowledge.manager import KnowledgeManager


def main() -> int:
    topic = "wow_worldview"
    print("=" * 72)
    print(f"  AIGameWorld-Studio · 索引 {topic} 主题知识库")
    print("=" * 72)

    # 检查 MD 文件
    kb_dir = PROJECT_ROOT / "knowledge-bases" / topic / "knowledge" / "documents"
    print(f"[0/3] 目标目录: {kb_dir}")
    if kb_dir.exists():
        mds = sorted(kb_dir.glob("*.md"))
        print(f"      找到 {len(mds)} 个 Markdown 文件:")
        for m in mds:
            print(f"        - {m.name}  ({m.stat().st_size // 1024} KB)")
    else:
        print(f"      ❌ 目录不存在: {kb_dir}")
        return 2

    print()
    print("[1/3] 初始化 KnowledgeManager（启动 embedding 加载，可能需要 1~5 分钟首次下载）")
    mgr = KnowledgeManager(project_root=PROJECT_ROOT, created_by="ingest_script")
    print(f"      向量库 type = {mgr._factory.config.store_type}")
    print(f"      向量库 health = {mgr._factory.health()}")

    print()
    print(f"[2/3] 开始索引 topic={topic} mode=incremental（SHA256 去重）")
    result = mgr.index(topic)
    print("      index() 返回:")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    print()
    print(f"[3/3] 索引后统计: topic={topic}")
    stats = mgr.stats(topic)
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))

    # ------- 检索验证（5 个问题） -------
    print()
    print("=" * 72)
    print("  检索验证（top-5 语义相似度）")
    print("=" * 72)
    queries = [
        "阿尔萨斯为什么会堕落？他拔出了什么剑？",
        "黑暗之门是哪一年开启的？是谁和谁联手打开的？",
        "艾泽拉斯星球的上古之神有哪几个？克苏恩、尤格萨隆被关在哪里？",
        "联盟和部落分别由哪些种族组成？历代大酋长是谁？",
        "巫妖王一共有几任？分别是谁？为什么必须有巫妖王存在？",
    ]
    for i, q in enumerate(queries, 1):
        print(f"\n[{i}/{len(queries)}] Q: {q}")
        hits = mgr.search_with_meta(topic, q, top_k=5)
        if not hits:
            print("      （0 条结果）")
            continue
        for rank, h in enumerate(hits, 1):
            score = h.get("score_cosine_sim") or 0.0
            meta = h.get("metadata") or {}
            doc_id = meta.get("doc_id") or "?"
            chunk_idx = meta.get("chunk_index")
            text = (h.get("text") or "").replace("\n", " ").strip()
            short_text = text[:120] + ("…" if len(text) > 120 else "")
            print(
                f"      #{rank}  score={score:.4f}  doc={doc_id}"
                + (f" chunk={chunk_idx}" if chunk_idx is not None else "")
                + f"\n        {short_text}"
            )

    print("\n✅ 索引完成！")
    print("   UI 管理: http://127.0.0.1:5173/kb")
    print("   Qdrant Dashboard: http://127.0.0.1:6333/dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
