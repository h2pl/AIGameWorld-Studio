"""Scrapy + CrawlerService 端到端冒烟测试：抓 example.com → 验证 DB + 文件.

Runner v3：子进程架构，无需在本脚本模拟 reactor install 了。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="scrapy_smoke_"))
    cleanup = True
    try:
        return _run(tmp)
    except AssertionError:
        cleanup = False
        print("\n[FAIL] leaving temp dir:", tmp)
        return 1
    finally:
        if cleanup:
            for _ in range(5):
                try:
                    shutil.rmtree(tmp, ignore_errors=True)
                    break
                except Exception:
                    time.sleep(0.5)


def _run(tmp: Path) -> int:
    from src.utils.sqlite_store import SQLiteStore
    from src.services.crawler.service import CrawlerService

    # 1. 模拟项目根目录：tmp/project_root/knowledge-bases/test_topic/
    project_root = tmp / "pr"
    topic_dir = project_root / "knowledge-bases" / "test_topic"
    topic_dir.mkdir(parents=True, exist_ok=True)
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "studio.db"

    store = SQLiteStore(db_file)
    store.run_migrations(ROOT / "migrations")
    store.close()

    svc = CrawlerService(store=SQLiteStore(db_file), project_root=project_root)
    print(f"[init] use_scrapy={svc._use_scrapy}")

    # 2. 贴 URL 模式（example.com 只有一页，抓它）
    job_id = svc.crawl_urls(
        "test_topic",
        urls=["https://example.com/"],
        source_type="documents",
        created_by="smoke",
    )
    print(f"[submitted] job_id={job_id}")

    # 3. 等待完成（最长 90 秒：子进程启动 + DNS + 下载都要时间）
    deadline = time.time() + 90
    final_status = None
    while time.time() < deadline:
        job = svc.get_job(job_id) or {}
        final_status = str(job.get("status", "???"))
        done = int(job.get("done_items") or 0)
        total = int(job.get("total_items") or 0)
        err = job.get("error_msg")
        print(f"  [poll] status={final_status} done={done}/{total} err={err}")
        if final_status in {"done", "failed", "discarded"}:
            break
        time.sleep(1.5)

    # 4. 打印详情
    job = svc.get_job(job_id) or {}
    items = job.get("items", [])
    print("\n==== Final state ====")
    print(f"job: status={job.get('status')}, done={job.get('done_items')}/{job.get('total_items')}")
    print(f"error_msg={job.get('error_msg')}")
    for it in items:
        print(f"  item: status={it['status']} ct={it.get('content_type')} sz={it.get('file_size')} "
              f"fp={it.get('file_path')} err={it.get('error_msg')}")
        if it.get("file_path"):
            fp = Path(it["file_path"])
            if fp.exists():
                print("     >>> file EXISTS, first 300 bytes:")
                try:
                    print(fp.read_text(encoding="utf-8", errors="replace")[:300])
                except Exception as e:
                    print(f"    [cannot read] {e}")
            else:
                print("     >>> file DOES NOT EXIST (path is not None)")

    staging = svc.staging_dir("test_topic", job_id)
    mds = list(staging.glob("*.md"))
    pdfs = list(staging.glob("*.pdf"))
    print(f"\nstaging dir content (md={len(mds)}, pdf={len(pdfs)}): {[f.name for f in mds + pdfs]}")

    # Print logs if exist
    for log_name in ("_launcher.log", "_scrapy.log"):
        lf = staging / log_name
        if lf.exists():
            print(f"\n==== {log_name} tail (last 50 lines) ====")
            try:
                lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
                print("\n".join(lines[-50:]))
            except Exception as e:
                print(f"[cannot read {log_name}] {e}")

    # List all files in staging
    print("\n==== All files under staging ====")
    for f in sorted(staging.rglob("*")):
        if f.is_file():
            try:
                sz = f.stat().st_size
            except Exception:
                sz = -1
            print(f"  {f.relative_to(staging)} ({sz} bytes)")

    # 5. 断言
    assert final_status == "done", f"job not done: status={final_status}"
    assert len(items) >= 1, "no items in job"
    fetched_items = [it for it in items if it["status"] == "fetched"]
    assert fetched_items, f"no fetched items: {items}"
    md = next((Path(it["file_path"]) for it in fetched_items if it.get("file_path") and it["content_type"] == "markdown"), None)
    assert md and md.exists(), f"no markdown file written (expected path from db={md})"
    body = md.read_text(encoding="utf-8", errors="replace")
    assert "example.com" in body.lower() or "source_url:" in body, f"markdown body missing expected content, got:\n{body[:300]}"
    print("\n[PASS] Scrapy (v3 subprocess) + CrawlerService E2E smoke test succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
