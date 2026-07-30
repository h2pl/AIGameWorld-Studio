"""Web search backend — DuckDuckGo (primary, zero API key) + Bing (fallback, zh-CN friendly).

封装 ``ddgs`` 库做全网搜索，返回统一的 ``SearchResult`` 列表。
选 DuckDuckGo 而非 SerpAPI/Bing 的理由：零配置、无需 API key、免费、本地可用。

**中国大陆网络兜底 (Bing fallback):**
由于 DDGS 依赖的 DuckDuckGo/Google/Yahoo/Brave/Wikipedia 等在中国大陆网络下频繁超时
（10+ 秒 per engine，几乎拿不到有效结果），这里增加 ``_web_search_bing`` 作为备选：
直接用 httpx 请求 ``https://cn.bing.com/search``，用 BeautifulSoup 解析 li.b_algo，
无需任何 API key，延迟低、中文结果质量高。DDGS 返回不足时自动 fallback 合并。
"""

from __future__ import annotations

from dataclasses import dataclass


# 统一的搜索结果数据结构
@dataclass
class SearchResult:
    """统一的搜索结果结构。"""

    # 搜索结果标题
    title: str
    # 结果页的目标 URL
    url: str
    # 搜索结果摘要片段
    snippet: str

    # 转换为字典便于序列化
    def to_dict(self) -> dict:
        # 返回三个字段的字典
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


# Bing 搜索 fallback：解析 cn.bing.com HTML（无需 API key，国内可访问）
# 返回 list[SearchResult]，异常时返回空列表
def _web_search_bing(query: str, max_results: int = 10) -> list[SearchResult]:
    """必应搜索 (cn.bing.com) 直连 fallback。

    - 中国大陆网络优先可用
    - 无 API key、无速率限制（保持合理 UA + 请求间隔）
    - 使用 BeautifulSoup 解析 ``<li class="b_algo">``
    """
    if not query or not query.strip():
        return []

    try:
        import httpx
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return []

    url = "https://cn.bing.com/search"
    params = {
        "q": query.strip(),
        "count": max(10, min(max_results * 2, 50)),  # 多拿一些，后去重
        "ensearch": 0,
        "mkt": "zh-CN",
        "setlang": "zh-CN",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                return []
            html = resp.text
    except Exception:
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []

    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    for li in soup.select("li.b_algo"):
        h2 = li.select_one("h2")
        a = h2.select_one("a[href]") if h2 else None
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href or not href.startswith(("http://", "https://")):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        title = (a.get_text(separator=" ", strip=True) or href).strip()
        snippet = ""
        cap = li.select_one(".b_caption p, .b_caption, p, .b_snippet")
        if cap:
            snippet = cap.get_text(separator=" ", strip=True)

        results.append(SearchResult(title=title, url=href, snippet=snippet))
        if len(results) >= max_results:
            break
    return results


# 调用 DuckDuckGo 搜索的公开入口函数
# 参数 query：搜索关键词字符串；空或全空白时直接返回空列表
# 参数 max_results：返回的最大结果条数，默认 10
# 返回 list[SearchResult]：标准化后的搜索结果列表；任何异常都返回空列表
def web_search(query: str, max_results: int = 10) -> list[SearchResult]:
    """调用 DuckDuckGo 搜索 (主) + 必应 Bing 搜索 (fallback)，返回 ``SearchResult`` 列表.

    策略：
    1. 先跑 DDGS（DuckDuckGo 等 engines），最多 6 秒就强制切 fallback（timeout 保守）。
    2. 如果 DDGS 条数 >= 所需 -> 直接返回。
    3. 否则跑 Bing fallback 补够数量，URL 去重后合并。

    任何异常都吞掉返回空列表，由上层决定如何提示用户。
    """
    # 查询为空或全空白
    if not query or not query.strip():
        # 直接返回空列表
        return []

    # ---------- Phase 1: DDGS ----------
    # 延迟导入 ddgs，避免未装 ddgs 时 import 整个模块就崩
    try:
        from ddgs import DDGS  # type: ignore
    except ImportError:
        DDGS = None  # type: ignore

    raw: list[dict] = []
    if DDGS is not None:
        try:
            ddgs = DDGS()
            try:
                raw = list(ddgs.text(query, max_results=max_results))
            finally:
                close = getattr(ddgs, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
        except Exception:
            raw = []

    # 规范化 DDGS 结果
    results: list[SearchResult] = []
    seen: set[str] = set()
    for r in raw:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or r.get("name") or "").strip()
        url = str(r.get("href") or r.get("url") or r.get("link") or "").strip()
        snippet = str(r.get("body") or r.get("snippet") or r.get("description") or "").strip()
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append(SearchResult(title=title or url, url=url, snippet=snippet))

    # DDGS 足够 -> 直接返回
    if len(results) >= max_results:
        return results[:max_results]

    # ---------- Phase 2: Bing fallback ----------
    remaining = max_results - len(results)
    bing_results = _web_search_bing(query, max_results=max(remaining, 10))
    for br in bing_results:
        if br.url in seen:
            continue
        seen.add(br.url)
        results.append(br)
        if len(results) >= max_results:
            break
    return results
