"""Web search backend — DuckDuckGo (zero API key).

封装 ``ddgs`` 库做全网搜索，返回统一的 ``SearchResult`` 列表。
选 DuckDuckGo 而非 SerpAPI/Bing 的理由：零配置、无需 API key、免费、本地可用。
若日后要换 SearXNG / Google CSE，只需替换本文件的实现，上层 service 不变。
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


# 调用 DuckDuckGo 搜索的公开入口函数
# 参数 query：搜索关键词字符串；空或全空白时直接返回空列表
# 参数 max_results：返回的最大结果条数，默认 10
# 返回 list[SearchResult]：标准化后的搜索结果列表；任何异常都返回空列表
def web_search(query: str, max_results: int = 10) -> list[SearchResult]:
    """调用 DuckDuckGo 搜索，返回 ``SearchResult`` 列表.

    防御性兼容 ddgs 9.x 多种调用形式（context manager / 直接实例化 / 顶层函数）。
    任何异常都吞掉返回空列表，由上层决定如何提示用户。
    """
    # 查询为空或全空白
    if not query or not query.strip():
        # 直接返回空列表
        return []

    # 延迟导入 ddgs，避免未装 ddgs 时 import 整个模块就崩
    try:
        from ddgs import DDGS  # type: ignore
    except ImportError:
        # ddgs 库未安装
        # 返回空列表，由上层处理
        return []

    # 存储原始搜索结果字典列表
    raw: list[dict] = []
    # 执行搜索，兼容不同 ddgs 版本，异常兜底返回空列表
    try:
        # 9.x 推荐：直接实例化后调 .text()
        # 实例化 DuckDuckGo 搜索客户端
        ddgs = DDGS()
        try:
            # 执行文本搜索并转为列表
            raw = list(ddgs.text(query, max_results=max_results))
        finally:
            # 部分版本有 close，没有就忽略（兼容不同 ddgs 版本）
            # 安全获取 close 方法
            close = getattr(ddgs, "close", None)
            # 确认是可调用的方法
            if callable(close):
                # 关闭客户端释放资源
                try:
                    close()
                except Exception:
                    # 关闭时出错也忽略
                    pass
    except Exception:
        # 搜索过程中任何异常（网络、API 变更等）
        # 重置为空列表
        raw = []

    # 规范化后的结果列表
    results: list[SearchResult] = []
    # 遍历原始结果逐条标准化
    for r in raw:
        # 非字典条目跳过（防御性）
        if not isinstance(r, dict):
            continue
        # 兼容不同字段名取标题
        title = str(r.get("title") or r.get("name") or "").strip()
        # 兼容不同字段名取 URL
        url = str(r.get("href") or r.get("url") or r.get("link") or "").strip()
        # 兼容不同字段名取摘要
        snippet = str(r.get("body") or r.get("snippet") or r.get("description") or "").strip()
        # 没有 URL 的结果无意义，丢弃
        if not url:
            continue
        # 标题为空时回退用 URL
        results.append(SearchResult(title=title or url, url=url, snippet=snippet))
    # 返回标准化后的搜索结果列表
    return results
