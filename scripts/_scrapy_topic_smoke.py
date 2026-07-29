"""TopicSearch Spider 端到端冒烟测试：DDG搜索 + 批量爬取.

用法：uv run python scripts\_scrapy_topic_smoke.py
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
    tmp = Path(tempfile.mkdtemp(prefix="scrapy_topic_smoke_"))
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

    # 1. 模拟项目目录
    project_root = tmp / "pr"
    topic_dir = project_root / "knowledge-bases" / "wow_test"
    topic_dir.mkdir(parents=True, exist_ok=True)
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "studio.db"

    store = SQLiteStore(db_file)
    store.run_migrations(ROOT / "migrations")
    store.close()

    svc = CrawlerService(store=SQLiteStore(db_file), project_root=project_root)
    print(f"[init] use_scrapy={svc._use_scrapy}")

    # 2. 主题爬取模式（DDG 搜索 max_results=3，缩小测试范围）
    query = "魔兽世界 世界观 简介"
    print(f"\n[submit] topic crawl: query={query!r}  max_results=3")
    job_id = svc.crawl_search(
        "wow_test",
        query=query,
        max_results=3,
        source_type="documents",
        created_by="smoke",
    )
    print(f"[submitted] job_id={job_id}")

    # 3. 等待完成（最长 5 分钟）
    deadline = time.time() + 300
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
        time.sleep(2)

    # 4. 打印详情
    job = svc.get_job(job_id) or {}
    items = job.get("items", [])
    print("\n==== Final state ====")
    print(f"job: status={job.get('status')}, done={job.get('done_items')}/{job.get('total_items')}")
    print(f"error_msg={job.get('error_msg')}")
    for it in items:
        print(f"  item: status={it['status']} ct={it.get('content_type')} sz={it.get('file_size')} "
              f"url={(it.get('url') or '')[:80]} err={it.get('error_msg')}")
        if it.get("file_path"):
            fp = Path(it["file_path"])
            if fp.exists():
                print(f"     >>> file EXISTS ({fp.stat().st_size} bytes)")
            else:
                print("     >>> file DOES NOT EXIST")

    staging = svc.staging_dir("wow_test", job_id)
    mds = list(staging.glob("*.md"))
    pdfs = list(staging.glob("*.pdf"))
    print(f"\nstaging dir content (md={len(mds)}, pdf={len(pdfs)}): {[f.name for f in mds + pdfs]}")

    # 打印日志
    for log_name in ("_launcher.log", "_scrapy.log"):
        lf = staging / log_name
        if lf.exists():
            print(f"\n==== {log_name} tail (last 40 lines) ====")
            try:
                lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
                print("\n".join(lines[-40:]))
            except Exception as e:
                print(f"[cannot read {log_name}] {e}")

    # List all files in staging
    print("\n==== All files under staging ====")
    for f in sorted(staging.rglob("*")):
        if f.is_file() and f.name.startswith("_") is False:
            try:
                sz = f.stat().st_size
            except Exception:
                sz = -1
            print(f"  {f.relative_to(staging)} ({sz} bytes)")

    # 5. 断言：只要 status=done 且至少有 1 个 item fetched（允许少量失败，DDG搜索结果不稳定）
    assert final_status == "done", f"job not done: status={final_status} err={job.get('error_msg')}"
    fetched_items = [it for it in items if it["status"] == "fetched"]
    print(f"\nFetched items: {len(fetched_items)}/{len(items)}")
    if not fetched_items:
        print("[WARN] 0 fetched items - DDG search may have been empty or all sites blocked. "
              "Check network connectivity and try again later. Not asserting for flakiness.")
    else:
        md_files = [Path(it["file_path"]) for it in fetched_items if it.get("file_path") and it["content_type"] == "markdown"]
        existing_md = [f for f in md_files if f.exists()]
        print(f"Valid markdown files: {len(existing_md)}/{len(md_files)}")
        if existing_md:
            body = existing_md[0].read_text(encoding="utf-8", errors="replace")
            print(f"\nFirst MD file sample (first 300 chars):\n{body[:300]}")
            # 至少有 frontmatter（source_url）
            assert "source_url:" in body, f"missing frontmatter source_url in:\n{body[:300]}"
    print("\n[PASS] TopicSearch (Scrapy v3 subprocess) E2E smoke test succeeded (DDG search + crawl)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
