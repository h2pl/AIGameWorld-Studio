"""魔兽世界主题爬虫 —— 只通过 CrawlerService API 调用（无独立下载逻辑）。

直接用：
    cd E:\\Projects\\AIGameWorld-Studio
    uv run python scripts\\run_wow_crawler.py

这不是独立下载脚本，只是对爬虫系统的配置化调用：
1. 4 轮 PDF 搜索（编年史/官方小说/设定集/艺术画册）→ 自动 promote 到 documents/
2. 3 轮网页文本搜索（世界观/种族地理/剧情）→ 自动 promote 到 documents/
3. 轮询等待所有 job 结束，打印最终汇总
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows + Python 3.14 兼容 reactor（本脚本不直接用 scrapy，但 CrawlerService 内部可能需要）
import asyncio

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from src.services.crawler.service import CrawlerService
from src.utils.sqlite_store import SQLiteStore


@dataclass
class JobSpec:
    name: str
    query: str
    mode: str  # "pdf_only" / "pdf_prefer" / "all"
    max_results: int
    created_by: str = "run_wow_crawler.py"


TOPIC_ID = "world_of_warcraft"
DB_PATH = ROOT / "data" / "studio_wow.db"

PDF_JOBS: list[JobSpec] = [
    JobSpec("pdf-chronicle", "魔兽世界编年史 第一卷 第二卷 第三卷 中文版 PDF", "pdf_only", 15),
    JobSpec("pdf-novels", "魔兽世界官方小说 全集 PDF 下载", "pdf_only", 15),
    JobSpec("pdf-art", "魔兽世界设定集 艺术画册 原画集 PDF 合集", "pdf_only", 12),
    JobSpec("pdf-warcraft3", "魔兽争霸 世界观 设定资料 PDF 合集", "pdf_only", 10),
]

TEXT_JOBS: list[JobSpec] = [
    JobSpec("txt-worldview", "魔兽世界 世界观 种族 地理 历史剧情 详细介绍", "all", 20),
    JobSpec("txt-kingdoms", "艾泽拉斯 国家势力 地图 编年史剧情 详细介绍", "all", 20),
    JobSpec("txt-lore", "魔兽世界 上古之战 燃烧军团 巫妖王之怒 故事背景", "all", 20),
]


def _sep(title: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78, flush=True)


def _submit_job(svc: CrawlerService, spec: JobSpec) -> str:
    if spec.mode in ("pdf_only", "pdf_prefer"):
        job_id = svc.crawl_search_pdf(
            TOPIC_ID,
            spec.query,
            max_results=spec.max_results,
            source_type="documents",
            created_by=spec.created_by,
            pdf_only=(spec.mode == "pdf_only"),
            auto_promote=True,
        )
    else:
        job_id = svc.crawl_search(
            TOPIC_ID,
            spec.query,
            max_results=spec.max_results,
            source_type="documents",
            created_by=spec.created_by,
            content_type_priority="all",
            auto_promote=True,
        )
    return job_id


def _wait_jobs(svc: CrawlerService, job_ids: dict[str, str], timeout_sec: int = 900) -> None:
    """name->job_id 轮询等待全部 done/failed，最多 timeout_sec 秒."""
    names = list(job_ids.keys())
    t0 = time.time()
    last_status = {n: None for n in names}
    while True:
        now = time.time()
        if now - t0 > timeout_sec:
            print(f"  !! 超时 {timeout_sec}s，仍未结束的 job:")
            for n in names:
                j = svc.get_job(job_ids[n]) or {}
                print(f"     - {n}: status={j.get('status')} done={j.get('done_items')}/{j.get('total_items')}")
            return
        all_done = True
        for n in names:
            j = svc.get_job(job_ids[n]) or {}
            st = j.get("status") or "?"
            done = j.get("done_items") or 0
            total = j.get("total_items") or 0
            if st != last_status[n]:
                elapsed = int(now - t0)
                print(
                    f"  [{elapsed:4d}s] {n:<18s} job={job_ids[n][:12]}... status={st:<10s} items={done}/{total}",
                    flush=True,
                )
                last_status[n] = st
            if st not in ("done", "failed", "discarded"):
                all_done = False
        if all_done:
            elapsed = int(now - t0)
            print(f"  全部结束，耗时 {elapsed}s。", flush=True)
            return
        time.sleep(3.0)


def _print_summary(svc: CrawlerService, name_jobid: dict[str, str]) -> None:
    _sep("最终 job 汇总")
    for n, jid in name_jobid.items():
        j = svc.get_job(jid) or {}
        print(f"\n  --- {n} (job={jid}) ---")
        print(f"    status       : {j.get('status')}")
        print(f"    query        : {j.get('query')}")
        print(f"    done/total   : {j.get('done_items')}/{j.get('total_items')}")
        print(f"    error_msg    : {str(j.get('error_msg') or '')[:200]}")
        items = j.get("items") or []
        promoted = [it for it in items if it.get("status") == "promoted"]
        fetched = [it for it in items if it.get("status") == "fetched"]
        failed = [it for it in items if it.get("status") == "failed"]
        print(f"    promoted     : {len(promoted)}")
        for p in promoted[:5]:
            fp = p.get("file_path") or ""
            # 打印文件名
            fn = Path(fp).name if fp else "(no path)"
            size = p.get("file_size") or 0
            ct = p.get("content_type") or ""
            print(f"      - [{ct:>4s}] {fn:>60s}  {size:>8d} B")
        if len(promoted) > 5:
            print(f"      ... 还有 {len(promoted) - 5} 个")
        print(f"    fetched(未p) : {len(fetched)}")
        if failed:
            print(f"    failed       : {len(failed)}")
            for f in failed[:3]:
                url = f.get("url") or ""
                err = f.get("error_msg") or ""
                print(f"      - {url[:80]:80s} err={err[:60]}")


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "knowledge-bases" / TOPIC_ID / "knowledge" / "documents").mkdir(parents=True, exist_ok=True)

    store = SQLiteStore(DB_PATH)
    svc = CrawlerService(store, ROOT)

    # 1) PDF jobs（并行提交 — CrawlerService 内部是子进程，不阻塞）
    _sep("STEP 1 / 2 : 提交 PDF 搜索 Job 共 4 个")
    name_jobid: dict[str, str] = {}
    for spec in PDF_JOBS:
        jid = _submit_job(svc, spec)
        name_jobid[spec.name] = jid
        print(f"  submitted: {spec.name:<18s} {spec.query[:50]:50s} -> job={jid[:12]}...")

    print("\n  等待所有 PDF job 完成（最多 900s / 15min）...", flush=True)
    _wait_jobs(svc, name_jobid)
    # 手动补一次 promote（auto_promote 是后台线程，可能还没跑；promote 幂等，重复调用安全）
    for n, jid in name_jobid.items():
        try:
            r = svc.promote_job(jid)
            print(f"  [manual promote] {n:<18s} promoted={r.get('promoted_count')} skipped={r.get('skipped_count')}")
        except Exception as _e:
            print(f"  [manual promote] {n:<18s} failed: {_e}")
    _print_summary(svc, name_jobid)

    # 2) Text jobs（并行提交）
    _sep("STEP 2 / 2 : 提交 网页文本 搜索 Job 共 3 个")
    for spec in TEXT_JOBS:
        jid = _submit_job(svc, spec)
        name_jobid[spec.name] = jid
        print(f"  submitted: {spec.name:<18s} {spec.query[:50]:50s} -> job={jid[:12]}...")

    print("\n  等待所有文本 job 完成（最多 900s）...", flush=True)
    _wait_jobs(svc, name_jobid)
    # 再次手动补 promote
    for n, jid in name_jobid.items():
        try:
            r = svc.promote_job(jid)
            print(f"  [manual promote] {n:<18s} promoted={r.get('promoted_count')} skipped={r.get('skipped_count')}")
        except Exception as _e:
            print(f"  [manual promote] {n:<18s} failed: {_e}")

    # 最终全量汇总
    _print_summary(svc, name_jobid)

    # documents/ 目录最终大小 & 数量
    doc_dir = ROOT / "knowledge-bases" / TOPIC_ID / "knowledge" / "documents"
    if doc_dir.exists():
        _sep("documents/ 目录快照")
        pdfs = sorted([p for p in doc_dir.rglob("*.pdf") if p.is_file()])
        mds = sorted([p for p in doc_dir.rglob("*.md") if p.is_file()])
        total_pdf_sz = sum(p.stat().st_size for p in pdfs)
        total_md_sz = sum(p.stat().st_size for p in mds)
        print(f"  PDF 文件 数: {len(pdfs)}，总大小: {total_pdf_sz / 1024 / 1024:.2f} MB")
        print(f"  MD  文件 数: {len(mds)}， 总大小: {total_md_sz / 1024:.2f} KB")
        print(f"\n  存储目录: {doc_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
