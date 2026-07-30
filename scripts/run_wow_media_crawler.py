"""魔兽世界主题爬虫：直接用 Bing 搜游戏媒体 URL → submit_crawl 批量爬 GenericCrawlSpider。

解决：搜索引擎里的百度百科/灰机/wiki/NGA/Fandom 全被 403/Cloudflare 挡住，
改用国内游戏媒体站（17173、3DMGAME、游民星空 GAMERSKY、游侠 ALI213、
178、叶子猪、52PK、九游 等）直接爬，这些站反爬弱，且魔兽世界剧情/编年史/
背景/资料类文章非常多。

步骤：
  1) Bing 搜多组关键词 + site:xx 限定（或不限定拿更多源）
  2) 过滤只保留游戏媒体域名，去重
  3) CrawlerService.submit_crawl(seed_urls=list, follow_links=1)
  4) 轮询 job 状态 → promote → 汇总
"""

from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.services.crawler.service import CrawlerService

TOPIC_ID = "world_of_warcraft"
KB_ROOT = ROOT / "knowledge-bases" / TOPIC_ID
DB_PATH = ROOT / "data" / "studio_wow.db"

# ---- 搜索关键词组合 ----
QUERIES: list[str] = [
    # 编年史
    "魔兽世界编年史 剧情故事 17173",
    "魔兽世界编年史 第一卷 第二卷 第三卷 剧情详解 site:17173.com",
    "魔兽世界 编年史 设定集 背景故事大全 site:gamersky.com",
    "魔兽世界编年史 全本阅读 剧情解读 site:3dmgame.com",
    "魔兽世界 编年史 完整故事 世界观设定 site:ali213.net",
    # 世界观/种族
    "魔兽世界 世界观 种族介绍 国家势力 艾泽拉斯 17173",
    "魔兽世界 艾泽拉斯 国家 地图 势力分布 详细介绍 site:178.com",
    "魔兽世界 联盟 部落 主城 种族 详细介绍 大全",
    # 历史剧情/重大事件
    "魔兽世界 上古之战 燃烧军团 巫妖王之怒 故事背景",
    "魔兽世界 历史时间线 大事件 剧情梳理",
    "魔兽世界 魔兽争霸 1 2 3 剧情 世界观设定 发展史",
    # 人物/组织
    "魔兽世界 重要人物 介绍 萨尔 吉安娜 阿尔萨斯 伊利丹",
    "魔兽世界 燃烧军团 上古之神 泰坦 巨龙 组织设定",
    "魔兽世界 萨格拉斯 基尔加丹 阿克蒙德 故事介绍",
]

# ---- 允许的游戏媒体域名白名单 ----
ALLOW_DOMAINS: tuple[str, ...] = (
    "17173.com",
    "wow.17173.com",
    "newgame.17173.com",
    "3dmgame.com",
    "www.3dmgame.com",
    "gl.3dmgame.com",
    "dl.3dmgame.com",
    "gamersky.com",
    "www.gamersky.com",
    "shouyou.gamersky.com",
    "ali213.net",
    "www.ali213.net",
    "gl.ali213.net",
    "wiki.ali213.net",
    "52pk.com",
    "wow.52pk.com",
    "178.com",
    "wow.178.com",
    "nga.178.com",
    "db.178.com",
    "yzz.cn",
    "wow.yzz.cn",
    "9game.cn",
    "www.9game.cn",
    "18183.com",
    "www.18183.com",
    "shouyou.18183.com",
    "4399.com",
    "news.4399.com",
    # 非游戏媒体但经常有魔兽剧情介绍（可留）
    "3loumao.org",
    "douban.com",
    "www.douban.com",
    "zhihu.com",
    "zhuanlan.zhihu.com",
    "www.zhihu.com",
    "bilibili.com",
    "www.bilibili.com",
    "read.bilibili.com",
    "game.sina.com.cn",
    "games.sina.com.cn",
    "game.163.com",
    "qq.com",
    "game.qq.com",
    "sohu.com",
    "game.sohu.com",
)

# ---- 搜索结果过滤关键词（标题/snippet必须命中，否则是广告/下载/私服/SEO垃圾）----
REQUIRE_THEME_KW: tuple[str, ...] = (
    "魔兽",
    "world of warcraft",
    "warcraft",
    "wow",
    "艾泽拉斯",
    "编年史",
    "世界观",
    "种族",
    "剧情",
    "背景",
    "历史",
    "燃烧军团",
    "巫妖王",
    "上古之战",
    "萨格拉斯",
    "泰坦",
    "上古之神",
    "联盟",
    "部落",
    "暴风城",
    "奥格瑞玛",
    "伊利丹",
    "阿尔萨斯",
    "萨尔",
    "吉安娜",
    "基尔加丹",
    "阿克蒙德",
)


def step1_search_urls() -> list[str]:
    """Phase 1: Bing 搜所有关键词组合，过滤出白名单域名的 URL 列表去重返回。"""

    from src.services.crawler.search import web_search

    seen: set[str] = set()
    results: list[str] = []
    for q in QUERIES:
        print(f"  [search] {q[:80]}")
        try:
            raws = web_search(q, max_results=15)
        except Exception as e:
            print(f"    FAIL: {type(e).__name__}: {e}")
            continue
        hit = 0
        for r in raws:
            u = (r.url or "").strip()
            t = (r.title or "").lower()
            s = (r.snippet or "").lower()
            u_lower = u.lower()
            # 1) 必须 http(s)
            if not u or not u_lower.startswith(("http://", "https://")):
                continue
            # 2) 必须是白名单域名（host 里含有 allow 任一项）
            try:
                host = u.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0].lower()
            except Exception:
                continue
            if not any(d in host for d in ALLOW_DOMAINS):
                continue
            # 3) 主题相关：标题/摘要里至少命中一个魔兽世界核心词
            text = t + " " + s + " " + u_lower
            if not any(k.lower() in text for k in REQUIRE_THEME_KW):
                continue
            # 4) 去重
            if u in seen:
                continue
            seen.add(u)
            results.append(u)
            hit += 1
        print(f"    -> {hit} new URLs (cumulative {len(results)})")
    print(f"\n  Total collected: {len(results)} unique URLs")
    for i, u in enumerate(results[:20]):
        print(f"    [{i + 1:2d}] {u[:120]}")
    if len(results) > 20:
        print(f"    ... 省略 {len(results) - 20} 条")
    return results


def step2_submit_and_wait(urls: list[str]) -> str | None:
    """Phase 2: 构造 CrawlerService（正确 API），crawl_urls 提交，等 done，手动 promote，返回 job_id。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    (KB_ROOT / "knowledge" / "documents").mkdir(parents=True, exist_ok=True)

    from src.utils.sqlite_store import SQLiteStore

    store = SQLiteStore(DB_PATH)
    svc = CrawlerService(store, ROOT)

    if not urls:
        print("  [SKIP] 0 URLs, nothing to submit")
        return None

    job_id = svc.crawl_urls(
        TOPIC_ID,
        urls,
        source_type="documents",
        created_by="script:wow_media",
    )
    print(f"\n  [submit_crawl] submitted job={job_id}")

    # 轮询等完成
    t0 = time.time()
    total = 0
    last_st = None
    while total < 1800:  # 最多 30min
        try:
            j = svc.get_job(job_id) or {}
        except Exception as e:
            print(f"  [poll ERR] {type(e).__name__}: {e}")
            time.sleep(15)
            total += 15
            continue
        st = j.get("status") or "?"
        done = j.get("done_items") or 0
        tot = j.get("total_items") or 0
        if st != last_st:
            elapsed = int(time.time() - t0)
            print(f"  [{elapsed:4d}s] status={st:10s}  fetched/total={done}/{tot}")
            last_st = st
        if st in ("done", "failed", "discarded", "stopped"):
            break
        time.sleep(15)
        total += 15

    # 结束后再等 2 秒，auto_promote 线程（如果有）可能刚启动
    time.sleep(2)
    # 手动 promote 补一次（双重保险，promote 幂等）
    try:
        r = svc.promote_job(job_id)
        print(f"  [manual promote] promoted={r.get('promoted_count')} skipped={r.get('skipped_count')}")
    except Exception as e:
        print(f"  [manual promote FAIL] {type(e).__name__}: {e}")

    # 打印 job 详情
    j = svc.get_job(job_id) or {}
    items = j.get("items") or []
    promoted = [i for i in items if i.get("status") == "promoted"]
    fetched = [i for i in items if i.get("status") == "fetched"]
    failed = [i for i in items if i.get("status") == "failed"]
    print("\n  Job summary:")
    print(f"    status       : {j.get('status')}")
    print(f"    promoted     : {len(promoted)}")
    for p in promoted[:10]:
        fp = p.get("file_path") or ""
        sz = p.get("file_size") or 0
        print(f"      [{p.get('content_type', '?'):4s}] {sz:8d} B  {Path(fp).name if fp else '-':50s}")
    print(f"    fetched(未p) : {len(fetched)}")
    print(f"    failed       : {len(failed)}")
    for f in failed[:10]:
        u = (f.get("url") or "")[:100]
        e = (f.get("error_msg") or "")[:80]
        print(f"      {u}  err={e}")
    store.close()
    return job_id


def step3_summary(job_id: str | None) -> None:
    """Phase 3: 打印 documents 目录汇总。"""
    print("\n" + "=" * 78)
    print("  资料汇总（documents/ 目录）")
    print("=" * 78)
    doc_dir = KB_ROOT / "knowledge" / "documents"
    pdfs = []
    mds = []
    for p in sorted(doc_dir.rglob("*")):
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        size = p.stat().st_size
        rel = str(p.relative_to(doc_dir))
        if suf == ".pdf":
            pdfs.append((rel, size))
        elif suf in (".md", ".txt"):
            mds.append((rel, size))
    pdf_tot = sum(s for _, s in pdfs)
    md_tot = sum(s for _, s in mds)
    print(f"\n  PDF 文件 : {len(pdfs):3d} 个，总大小 {pdf_tot / 1024 / 1024:.2f} MB")
    for n, s in pdfs:
        print(f"    {s / 1024 / 1024:7.2f} MB  {n}")
    print(f"\n  MD/TXT : {len(mds):3d} 个，总大小 {md_tot / 1024:.2f} KB")
    for n, s in sorted(mds, key=lambda x: -x[1])[:30]:
        # 预览 MD 首行标题
        tit = ""
        try:
            text = (doc_dir / n).read_text(encoding="utf-8", errors="ignore")[:300]
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("#") or line.startswith("title"):
                    tit = line[:90]
                    break
            if not tit:
                tit = text[:80].replace("\n", " ")
        except Exception:
            pass
        print(f"    {s / 1024:7.2f} KB  {n}")
        if tit:
            print(f"               title: {tit}")
    print(f"\n  存储目录: {doc_dir}")
    print(f"  文件总数: {len(pdfs) + len(mds)}")


def main() -> None:
    print("=" * 78)
    print("  WoW 主题定向爬虫：Bing 搜游戏媒体站 → GenericCrawlSpider 批量抓取")
    print("=" * 78)
    print(f"  Topic    : {TOPIC_ID}")
    print(f"  KB root  : {KB_ROOT}")
    print(f"  DB       : {DB_PATH}")

    urls = step1_search_urls()
    job_id = step2_submit_and_wait(urls)
    step3_summary(job_id)
    print("\n  Done.")


if __name__ == "__main__":
    main()
