"""Studio 知识库：端到端验证（模拟 UI 操作，用 TestClient）

1. 新建独立的测试 world-pack
2. 通过 /api/packs GET 拿到列表 → 含 test_world
3. 通过 POST /api/kb/{id}/upload 上传 2 份 MD（lore + documents）
4. POST /api/kb/{id}/index 索引
5. GET /api/kb/{id}/documents → total>=2
6. POST /api/kb/{id}/search → 命中青云城
7. DELETE /api/kb/{id}/documents/{doc_id} → 软删 + 从搜索结果消失
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 独立测试数据库 & 向量库：保证干净，不污染用户生产的 studio.db
DB_PATH = ROOT / "data" / "studio_e2e_test.db"
CHROMA_PATH = ROOT / "data" / "chroma_e2e"
WORLD_ID = "e2e_test_kb"
CUSTOM_DIR = ROOT / "world-packs" / "custom" / WORLD_ID

for p in [DB_PATH, CHROMA_PATH]:
    import shutil

    if p.exists():
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
if CUSTOM_DIR.exists():
    shutil.rmtree(CUSTOM_DIR)
(CUSTOM_DIR / "knowledge").mkdir(parents=True, exist_ok=True)

meta = """name: e2e 测试世界
description: 端到端测试知识库 UI/API 全流程
version: 0.1.0
author: aw-test
"""
(CUSTOM_DIR / "meta.yaml").write_text(meta, encoding="utf-8")

from src.serve import create_app  # noqa: E402

app = create_app(
    pack_dir=ROOT / "world-packs" / "custom",
    chroma_path=CHROMA_PATH,
    data_dir=DB_PATH.parent,
)

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)

failures = []


def check(label, cond, detail=""):
    status = "✅" if cond else "❌"
    print(f"{status} {label}", end="")
    if detail:
        print(f"\n     → {detail[:160]}")
    else:
        print()
    if not cond:
        failures.append((label, detail))


# 1. health + packs
r = client.get("/health")
check("health 200", r.status_code == 200, f"status={r.status_code}")

r = client.get("/api/packs")
check(
    f"/api/packs 包含 {WORLD_ID}",
    r.status_code == 200 and any(p["id"] == WORLD_ID for p in r.json()),
    f"status={r.status_code} ids={[p.get('id') for p in r.json() if isinstance(p, dict)] if r.status_code == 200 else r.text[:120]}",
)

# 2. upload documents
md1 = "## 新手入门 10 分钟指南\n\n1. 创建角色\n2. 去青云城中央接第一个任务「寻仙问道」\n3. 打 5 只野外怪物，就能 10 级转职"
md2 = "## 青云城概览\n\n青云城是仙侠世界中最大的中立城市，位于天脉之眼中央。\n城内有三大势力：天机阁（卖情报）、百宝阁（卖丹药）、御剑堂（学功法）。\n推荐先去百宝阁买个疗伤药再出城。"

r = client.post(
    f"/api/kb/{WORLD_ID}/upload?source_type=documents&prefix=2026-07-29_",
    files={"file": ("intro.md", io.BytesIO(md1.encode("utf-8")), "text/markdown")},
)
d1 = r.json()
check(
    f"upload intro.md 200, saved_to={d1.get('saved_to', '')}",
    r.status_code == 200 and d1.get("file_size") and d1.get("source_type") == "documents",
    f"status={r.status_code} text={r.text[:200]}",
)

r = client.post(
    f"/api/kb/{WORLD_ID}/upload-text?source_type=lore&file_name=city.md",
    content=md2,
    headers={"Content-Type": "text/markdown"},
)
d2 = r.json()
check(
    f"upload-text city.md 200, file_name={d2.get('file_name', '')}",
    r.status_code == 200 and d2.get("file_name") == "city.md" and d2.get("source_type") == "lore",
    f"status={r.status_code} text={r.text[:200]}",
)

# 3. 索引
import time

t0 = time.time()
r = client.post(f"/api/kb/{WORLD_ID}/index", json={"force": True})
idx = r.json()
t1 = time.time()
check(
    f"force 索引 2 files → files={idx.get('files')}, chunks={idx.get('chunks')}",
    r.status_code == 200 and idx.get("ok") and idx.get("files") == 2 and idx.get("chunks") >= 2,
    f"status={r.status_code} took={t1 - t0:.1f}s resp={r.text[:400]}",
)

# 4. 文档列表
r = client.get(f"/api/kb/{WORLD_ID}/documents?limit=50&include_deleted=false")
doc_r = r.json()
check(
    f"documents 列表 total={doc_r.get('total')}",
    r.status_code == 200 and doc_r.get("total", 0) >= 2,
    f"status={r.status_code} body={r.text[:300]}",
)
docs = doc_r.get("documents") or []
city_doc = next((d for d in docs if d.get("file_name") == "city.md"), None)
intro_doc = next((d for d in docs if "intro" in str(d.get("file_name", ""))), None)
check(
    "文档列表里能找到 city.md + source=lore",
    bool(city_doc and city_doc.get("source_type") == "lore"),
    f"city_doc={city_doc}" if not city_doc else "",
)
check(
    "文档列表里能找到 intro.md + source=documents",
    bool(intro_doc and intro_doc.get("source_type") == "documents"),
    f"intro_doc={intro_doc}" if not intro_doc else "",
)

# 5. 搜索
r = client.post(f"/api/kb/{WORLD_ID}/search", json={"query": "青云城百宝阁在哪", "top_k": 3, "min_score": 0.0})
search_r = r.json()
hits = search_r.get("hits") or []
check(
    f"搜索「青云城百宝阁在哪」命中 {len(hits)} 条，第一名是城市概览",
    r.status_code == 200
    and len(hits) >= 1
    and (
        "青云城" in (hits[0].get("text") or "")
        or str((hits[0].get("metadata") or {}).get("title") or "").startswith("青云城")
    ),
    f"status={r.status_code} top1={hits[0].get('text', '')[:80] if hits else ''}",
)

# 6. jobs + audit
r = client.get(f"/api/kb/{WORLD_ID}/jobs?limit=10")
jr = r.json()
check(
    f"jobs 列表里有 {len(jr.get('jobs', []))} 条，有 done",
    r.status_code == 200 and any(j.get("status") == "done" for j in (jr.get("jobs") or [])),
    f"status={r.status_code} resp={r.text[:200]}",
)

r = client.get(f"/api/kb/{WORLD_ID}/audit?limit=20")
ar = r.json()
ops = [a.get("op") for a in (ar.get("audit") or [])]
check(
    f"审计 log 至少有 index_start + retrieve 这 2 个 op (实际={sorted(set(ops))})",
    r.status_code == 200 and {"index_start", "retrieve"}.issubset(set(ops)),
    f"status={r.status_code} ops={ops[:15]}",
)

# 7. 软删 city.md，然后再搜索「百宝阁丹药 天机阁」——应该不再返回
if city_doc:
    doc_id = city_doc["id"]
    r = client.delete(f"/api/kb/{WORLD_ID}/documents/{doc_id}")
    dr = r.json()
    check(
        f"软删 city.md ok (soft_deleted={dr.get('soft_deleted')}, deleted_chunk_count={dr.get('deleted_chunk_ids_count')})",
        r.status_code == 200
        and dr.get("ok")
        and (dr.get("soft_deleted") or 0) == 1
        and (dr.get("deleted_chunk_ids_count") or 0) >= 1,
        f"status={r.status_code} text={r.text[:200]}",
    )
    r = client.post(
        f"/api/kb/{WORLD_ID}/search", json={"query": "百宝阁丹药 天机阁 御剑堂", "top_k": 10, "min_score": 0.0}
    )
    hits = (r.json()).get("hits") or []
    remaining_city_hits = [h for h in hits if "百宝阁" in (h.get("text") or "") or "天机阁" in (h.get("text") or "")]
    check(
        f"软删后搜索「百宝阁丹药 天机阁」应 0 条命中 (实际={len(remaining_city_hits)})",
        len(remaining_city_hits) == 0,
        f"hits={[(h.get('text', '')[:80]) for h in remaining_city_hits]}",
    )

# 8. UI 4 个页面 200
for page in ["/kb", "/kb/upload", "/kb/docs", "/kb/jobs"]:
    r = client.get(page)
    check(f"UI 页面 {page} 200", r.status_code == 200, f"status={r.status_code} len={len(r.content)}")

# 9. 清理测试数据（先关闭 client 的 TestClient 连接句柄）
import gc

try:
    del client
except Exception:
    pass
gc.collect()


def _rmtree_ignore(p):
    import shutil

    if not p.exists():
        return
    # 如果 Windows 上文件被锁，最多重试 2 次
    for _ in range(3):
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=False)
            else:
                p.unlink()
            return
        except PermissionError:
            import time

            time.sleep(0.3)
        except Exception:
            break
    # 最后一次 ignore_errors
    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    except Exception:
        pass


_rmtree_ignore(CUSTOM_DIR)
_rmtree_ignore(DB_PATH)
_rmtree_ignore(CHROMA_PATH)

print()
print(f"=== 端到端完成：{'全部通过' if not failures else f'{len(failures)} 个失败'} ===")
if failures:
    for lbl, det in failures:
        print(f"  ❌ {lbl}")
        if det:
            print(f"       → {det}")
    sys.exit(1)
