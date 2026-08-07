"""临时测试：导入魔兽编年史三部曲 PDF，验证多文件 ingest 链路（读取→切块→并发 embedding→双写）.

- 使用独立测试 DB（data/studio_chronicle_test.db），不污染生产 studio.db
- 使用真实 Qdrant（默认 backend）
- 只导入三部曲三本 PDF（01/02/03 第三卷），不动目录下其它 PDF
- 打印：每本加载耗时、embedding 并发批次进度、总 chunk 数、写入验证
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 项目根（脚本在 scripts/ 下）
sys.path.insert(0, str(ROOT))

# 独立测试 DB，避免污染生产
import os

os.environ.setdefault("KB_VECTOR_STORE", "qdrant")  # 显式走真实 Qdrant

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("chronicle_test")

from src.services.knowledge.manager import KnowledgeManager  # noqa: E402
from src.utils.sqlite_store import SQLiteStore  # noqa: E402

# ── 配置 ──
TEST_TOPIC = "wow_chronicle_test"  # 独立测试主题，结束后可清理
DB_PATH = ROOT / "data" / "studio_chronicle_test.db"
PDF_DIR = ROOT / "knowledge-bases" / "world_of_warcraft" / "knowledge" / "documents" / "pdf"

# 只取三部曲
TARGETS = [
    "01_魔兽世界编年史·第一卷（Chronicle Vol.1）.pdf",
    "02_魔兽世界编年史·第二卷（Chronicle Vol.2）.pdf",
    "03_魔兽世界编年史·第三卷（Chronicle Vol.3）.pdf",
]

files = [PDF_DIR / name for name in TARGETS]
missing = [f for f in files if not f.exists()]
if missing:
    _log.error("缺少文件：%s", [str(p) for p in missing])
    sys.exit(1)

# 三部曲全跑（去掉调试用的只跑第一卷限制）
# files = files[:1]

_log.info("目标文件：")
for f in files:
    _log.info("  - %s (%.2f MB)", f.name, f.stat().st_size / 1024 / 1024)

# ── 诊断：monkey-patch _upsert_document_meta 打印单步耗时，定位瓶颈 ──
from src.services.knowledge.ingest.pipeline import KnowledgePipeline  # noqa: E402

_orig_upsert = KnowledgePipeline._upsert_document_meta


def _timed_upsert(self, doc):
    import time as _t

    s = _t.time()
    rid = _orig_upsert(self, doc)
    e = _t.time()
    _upsert_acc["n"] += 1
    _upsert_acc["total"] += e - s
    if _upsert_acc["n"] % 50 == 0:
        _log.info(
            "[诊断] _upsert_document_meta 已执行 %d 次，累计 %.2fs，平均 %.3fs/次",
            _upsert_acc["n"],
            _upsert_acc["total"],
            _upsert_acc["total"] / _upsert_acc["n"],
        )
    return rid


_upsert_acc = {"n": 0, "total": 0.0}
KnowledgePipeline._upsert_document_meta = _timed_upsert

# ── 初始化 Manager（独立测试 DB）──
manager = KnowledgeManager(
    store=SQLiteStore(DB_PATH),
    project_root=ROOT,
    bootstrap_default_topics=False,
    created_by="chronicle_test",
)
manager.ensure_topic(TEST_TOPIC, name="魔兽编年史测试", description="临时测试")

# ── 进度回调 ──
file_total = len(files)
file_done_holder = {"done": 0}


def on_file_progress(done: int, total: int) -> None:
    file_done_holder["done"] = done
    _log.info("[读取进度] %d/%d 文件已加载", done, total)


def on_embed_progress(done_batches: int, total_batches: int) -> None:
    _log.info("[embedding 并发进度] batch %d/%d", done_batches, total_batches)


# ── 计时 ──
t0 = time.time()
_log.info("开始 ingest_files（文件读取串行 + embedding 并发）...")
result = manager.ingest_files(
    TEST_TOPIC,
    files,
    on_progress=on_file_progress,
    on_embed_progress=on_embed_progress,
)
t1 = time.time()

_log.info("=" * 60)
_log.info("完成：耗时 %.2f s", t1 - t0)
_log.info("doc_ids=%s", result.get("doc_ids"))
_log.info("chunks=%s", result.get("chunks"))
_log.info("elapsed(内部)=%.2f s", result.get("elapsed", 0.0))

# ── 验证双写 ──
store = SQLiteStore(DB_PATH)
chunk_rows = store.fetch_all(
    "SELECT document_id, COUNT(*) c FROM kb_chunk WHERE topic_id = ? GROUP BY document_id",
    (TEST_TOPIC,),
)
_log.info("kb_chunk 按 document 分组：%s", [(r["document_id"][:8], r["c"]) for r in chunk_rows])
total_chunks_db = store.fetch_one("SELECT COUNT(*) c FROM kb_chunk WHERE topic_id = ?", (TEST_TOPIC,))["c"]
_log.info("kb_chunk 总数=%s（应等于 chunks=%s）", total_chunks_db, result.get("chunks"))

# ── Qdrant 验证 ──
import json
import urllib.request as u

collection = f"kb_{TEST_TOPIC}"
try:
    raw = u.urlopen(f"http://127.0.0.1:6333/collections/{collection}", timeout=8).read()
    points = json.loads(raw)["result"].get("points_count")
    _log.info("Qdrant collection=%s points_count=%s（应 > 0）", collection, points)
except Exception as e:
    _log.warning("Qdrant 查询失败（可能 collection 未创建）：%s", e)

# ── 检索冒烟测试 ──
hits = manager.search_with_meta(TEST_TOPIC, "燃烧军团第一次入侵艾泽拉斯", top_k=3)
_log.info("检索冒烟测试：命中 %d 条", len(hits))
for i, h in enumerate(hits[:3], 1):
    txt = (h.get("text") or "")[:80]
    _log.info("  %d. score=%.4f %s", i, h.get("score_cosine_sim", 0.0), txt)

_log.info("=" * 60)
_log.info("测试 DB 位于：%s （如需清理：删除此文件 + Qdrant collection '%s'）", DB_PATH, collection)

manager.close()
