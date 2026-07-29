"""URL fetcher — httpx + BeautifulSoup + html2text.

职责
----
- 抓取单个 URL，自动识别 HTML / PDF / 其它二进制
- HTML 页面：剔除非正文标签后转 Markdown，加 frontmatter 记录来源
- PDF：原样保存字节（解析交给 KB pipeline 的 PDFReader）
- 尊重 robots.txt（按域名缓存）
- 同域名限流（默认 1 秒间隔）

**不写数据库、不碰向量库** —— 纯抓取 + 落盘，返回 ``FetchedFile`` 给上层记录。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

# 用真实浏览器 UA：很多站点（Fandom / 百度百科 / 知乎等）会拦截非浏览器 UA 返回 403。
# 这是爬虫的通行做法，robots.txt 仍被尊重（见 _can_fetch）。
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# 全局超时：总30秒，连接超时10秒
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_HEADERS = {
    # 浏览器 UA，避免被拦截
    "User-Agent": UA,
    # 接受的内容类型
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.9,*/*;q=0.8",
    # 优先中文
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # 不显式设 Accept-Encoding —— httpx 会按已装的解码器（gzip/deflate/可选 brotli）自动协商，
    # 避免声明 br 却没装 brotli 导致响应体无法解码。
    # 禁用缓存，确保抓最新内容
    "Cache-Control": "no-cache",
}


# 单次抓取的结果数据类
@dataclass
class FetchedFile:
    """单次抓取的结果。"""

    # 原始抓取的 URL
    url: str
    # 页面标题（HTML 时提取，PDF/其它为空）
    title: str
    # 'markdown' | 'pdf' | 'failed' 内容类型标记
    content_type: str
    # 落盘后的绝对路径；抓取失败时为 None
    file_path: str | None
    # 文件字节数
    file_size: int
    # 文件内容的 SHA256 哈希，用于去重
    sha256: str | None
    # 失败原因，成功时为 None
    error: str | None = None


# ------------------------------------------------------------------
# robots.txt 缓存（按域名）
# ------------------------------------------------------------------
# robots.txt 解析结果缓存，key 为域名
_robots_cache: dict[str, RobotFileParser | None] = {}
# 同域名限流：domain -> 上次请求时间戳
_last_request_at: dict[str, float] = {}


# 检查 robots.txt 是否允许抓取该 URL
# 参数 url：待检查的目标 URL
# 参数 client：httpx.Client 实例，用于请求 robots.txt
# 返回 bool：True 表示允许抓取，False 表示禁止；失败时默认允许（fail-open）
def _can_fetch(url: str, client: httpx.Client) -> bool:
    """检查 robots.txt 是否允许抓取该 URL。失败时默认允许（fail-open）。"""
    # 解析 URL 获取协议和域名
    parsed = urlparse(url)
    # 构造域名基准 URL
    host = f"{parsed.scheme}://{parsed.netloc}"
    # 缓存未命中，首次访问该域名
    if host not in _robots_cache:
        # 创建 robots.txt 解析器
        rp = RobotFileParser()
        # 拼接 robots.txt 地址
        robots_url = f"{host}/robots.txt"
        # 请求 robots.txt 并处理异常
        try:
            # 请求 robots.txt
            resp = client.get(robots_url, timeout=10.0)
            # 成功获取
            if resp.status_code == 200:
                # 解析规则
                rp.parse(resp.text.splitlines())
                # 存入缓存
                _robots_cache[host] = rp
            else:
                # 非 200 响应，视为无 robots.txt
                # 无 robots.txt，允许
                _robots_cache[host] = None
        except Exception:
            # 请求异常（超时、网络错误等），fail-open
            _robots_cache[host] = None
    # 从缓存取解析器
    rp = _robots_cache.get(host)
    # 无 robots 规则，默认允许
    if rp is None:
        return True
    # 检查当前 UA 是否可抓取该 URL，异常时 fail-open
    try:
        return rp.can_fetch(UA, url)
    except Exception:
        # 解析异常时 fail-open
        return True


# 同域名请求间隔限流函数
# 参数 url：目标请求 URL，用于提取域名
# 参数 min_interval：同域名两次请求的最小间隔秒数，默认 1.0 秒
# 返回 None：通过 sleep 实现阻塞等待，无返回值
def _rate_limit(url: str, min_interval: float = 1.0) -> None:
    """同域名请求间隔至少 min_interval 秒，避免压垮目标站。"""
    # 提取域名部分
    domain = urlparse(url).netloc
    # 当前时间戳
    now = time.time()
    # 该域名上次请求时间，默认 0 表示从未请求
    last = _last_request_at.get(domain, 0.0)
    # 计算需等待的时长
    wait = min_interval - (now - last)
    # 间隔不足，需要 sleep
    if wait > 0:
        # 等待达到最小间隔
        time.sleep(wait)
    # 更新该域名的最后请求时间
    _last_request_at[domain] = time.time()


# HTML 转 Markdown 函数，提取标题和正文
# 参数 html：HTML 源文本字符串
# 参数 url：页面来源 URL，用于错误提示或相对路径处理
# 返回 tuple[str, str]：(提取的页面标题, 转换后的 Markdown 正文字符串)
def _html_to_markdown(html: str, url: str) -> tuple[str, str]:
    """HTML → Markdown，返回 (title, markdown_body)。

    用 BeautifulSoup 剔除 nav/footer/script/aside 等非正文区块，
    再用 html2text 转成 Markdown。
    """
    from bs4 import BeautifulSoup
    import html2text  # type: ignore

    # 用 lxml 解析器构建 DOM
    soup = BeautifulSoup(html, "lxml")

    # 提取标题
    title = ""
    # title 标签存在且有文本
    if soup.title and soup.title.string:
        # 去除首尾空白
        title = soup.title.string.strip()

    # 剔除非正文标签（导航、页脚、脚本等噪声）
    for tag in soup.find_all(["nav", "footer", "header", "aside", "script", "style", "noscript", "iframe"]):
        # 从 DOM 中彻底移除该节点
        tag.decompose()

    # 优先取 <main> / <article> 正文容器，没有就退回到 body 甚至整个文档
    main = soup.find("main") or soup.find("article") or soup.body or soup
    # 移除 class 含 nav/menu/sidebar/ad/advertisement 等关键词的元素（广告、侧边栏等）
    # 注意：部分节点（Comment / NavigableString）的 .attrs 是 None，必须防御
    # 遍历所有带 class 属性的元素
    for el in main.find_all(attrs={"class": True}):
        # 安全获取 attrs 字典
        attrs = getattr(el, "attrs", None) or {}
        # 取出 class 值（可能是 list 或 str）
        raw_class = attrs.get("class")
        # class 为空则跳过
        if not raw_class:
            continue
        # 统一拼成空格分隔的字符串
        classes = " ".join(raw_class) if isinstance(raw_class, list) else str(raw_class)
        # 命中广告/导航关键词，移除该元素
        if any(kw in classes.lower() for kw in ("nav", "menu", "sidebar", "advertisement", "ad-slot", "cookie")):
            el.decompose()

    # 创建 html2text 转换器
    h = html2text.HTML2Text()
    # 保留链接（保留来源信息）
    h.ignore_links = False
    # 忽略图片标签，仅保留文本
    h.ignore_images = True
    # 不自动换行，避免破坏 Markdown 结构
    h.body_width = 0
    # 保护链接不被折行破坏
    h.protect_links = True
    # 执行 HTML → Markdown 转换
    md = h.handle(str(main))
    # 去除首尾空白行
    md = md.strip()
    # 返回提取的标题和 Markdown 正文
    return title, md


# 拼装带 YAML frontmatter 的 Markdown 文档
# 参数 title：文档标题，空则回退到 URL
# 参数 url：来源 URL，写入 frontmatter 的 source_url 字段
# 参数 body：Markdown 正文内容
# 参数 crawled_at：抓取时间字符串（ISO 格式）
# 返回 str：完整的带 frontmatter + H1 标题的 Markdown 文档
def _build_markdown_with_frontmatter(title: str, url: str, body: str, crawled_at: str) -> str:
    """拼装带 frontmatter 的 Markdown 文档。"""
    # 标题为空时回退到 URL
    safe_title = title or url
    # 构造 YAML frontmatter 段
    fm = (
        # frontmatter 开始标记
        "---\n"
        # 记录来源 URL
        f"source_url: {url}\n"
        # 记录抓取时间
        f"crawled_at: {crawled_at}\n"
        # 记录文档标题
        f"title: {safe_title}\n"
        # 标记为网页抓取来源
        "source_type: crawled_web\n"
        # frontmatter 结束标记
        "---\n\n"
    )
    # 正文前加一个 H1 标题，方便阅读和 chunk 切分
    # 拼接 frontmatter、H1 标题和正文
    return f"{fm}# {safe_title}\n\n{body}\n"


# 从标题和 URL 生成安全文件名
# 参数 title：页面标题，优先用作文件名基础
# 参数 url：来源 URL，标题为空时回退到 URL 路径
# 参数 idx：序号前缀，用于保证文件名排序稳定且不冲突
# 返回 str：安全文件名（不含扩展名），格式为 {idx:03d}__{sanitized_base}
def _safe_filename(title: str, url: str, idx: int) -> str:
    """从标题生成安全文件名（去掉特殊字符）。"""
    import re

    # 优先用标题，否则用 URL 路径
    base = title or urlparse(url).path.strip("/").replace("/", "_") or "page"
    # 替换 Windows/Linux 非法文件名为下划线
    base = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", base)
    # 去掉首尾特殊字符并限制长度 60
    base = base.strip("._- ")[:60]
    # 拼接序号前缀，保证排序稳定
    return f"{idx:03d}__{base or 'page'}"


# 抓取单个 URL 的公开入口函数
# 参数 url：目标抓取 URL
# 参数 staging_dir：暂存目录 Path 对象，抓取结果文件落盘位置
# 参数 idx：序号，用于文件名前缀和去重
# 参数 client：可选的 httpx.Client 实例；None 时内部创建新实例并在 finally 关闭
# 返回 FetchedFile：抓取结果对象，包含 URL、标题、内容类型、文件路径、大小、哈希和错误信息
def fetch_url(
    url: str,
    staging_dir: Path,
    idx: int,
    *,
    client: httpx.Client | None = None,
) -> FetchedFile:
    """抓取单个 URL，保存到 ``staging_dir``，返回 ``FetchedFile``.

    - HTML → ``{idx}__title.md``
    - PDF  → ``{idx}__title.pdf``
    - 其它 → 尝试按扩展名保存；识别失败标记为 failed
    """
    # 标记是否由本函数创建 client（用于 finally 关闭）
    owns_client = client is None
    # 外部未传入 client，创建新的
    if client is None:
        # 启用重定向跟随
        client = httpx.Client(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)

    # 主抓取流程：robots 检查 → 限流 → 请求 → 类型识别 → 落盘
    try:
        # robots.txt 检查
        # 被 robots.txt 禁止
        if not _can_fetch(url, client):
            # 返回失败结果，记录 robots 禁止原因
            return FetchedFile(
                url=url, title="", content_type="failed",
                file_path=None, file_size=0, sha256=None,
                # 记录失败原因
                error="disallowed by robots.txt",
            )

        # 同域名限流
        _rate_limit(url)
        # 发起 HTTP GET 请求
        resp = client.get(url)
        # 4xx/5xx 状态码抛异常
        resp.raise_for_status()
        # 取响应头 content-type 并转小写
        content_type = resp.headers.get("content-type", "").lower()
        # 取原始响应字节
        body = resp.content

        # 判断是否 PDF：content-type / URL 后缀 / 文件头魔法数字 任一命中即可
        is_pdf = "application/pdf" in content_type or url.lower().endswith(".pdf") or body[:5] == b"%PDF-"
        # 判断是否 HTML：content-type 含 text/html 或 xhtml
        is_html = "text/html" in content_type or "application/xhtml" in content_type

        # 格式化当前抓取时间
        crawled_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

        # PDF 分支：原样保存字节
        if is_pdf:
            # 文件扩展名
            ext = ".pdf"
            # 内容类型标记
            content_type_label = "pdf"
            # PDF 不在此处解析标题
            title = ""
            # 直接用原始字节
            file_bytes = body
        # HTML 分支：转 Markdown
        elif is_html:
            # Markdown 扩展名
            ext = ".md"
            # 标记为 markdown
            content_type_label = "markdown"
            # 解码 HTML 字节为文本，处理编码异常
            try:
                # 优先 UTF-8 解码
                html_text = body.decode("utf-8", errors="replace")
            except Exception:
                # 解码失败退回到 latin-1（永不失败）
                html_text = body.decode("latin-1", errors="replace")
            # HTML → Markdown + 提取标题
            title, md_body = _html_to_markdown(html_text, url)
            # 正文提取为空（可能是纯空白页）
            if not md_body.strip():
                # 返回失败结果，标记正文为空
                return FetchedFile(
                    url=url, title=title, content_type="failed",
                    file_path=None, file_size=0, sha256=None,
                    error="empty body after extraction",
                )
            # 拼装带 frontmatter 的文档
            md_full = _build_markdown_with_frontmatter(title, url, md_body, crawled_at)
            # UTF-8 编码为字节
            file_bytes = md_full.encode("utf-8")
        else:
            # 其它二进制：按 URL 扩展名保存，无法识别则标记 failed
            from pathlib import PurePosixPath

            # 从 URL 路径取扩展名
            ext = PurePosixPath(urlparse(url).path).suffix.lower() or ".bin"
            # 白名单校验，仅支持常见文档和媒体格式
            if ext not in {".md", ".txt", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"}:
                # 非白名单扩展名，标记失败
                return FetchedFile(
                    url=url, title="", content_type="failed",
                    file_path=None, file_size=0, sha256=None,
                    # 记录失败原因：不支持的内容类型
                    error=f"unsupported content_type: {content_type}",
                )
            # 去掉扩展名前的点号作为类型标签
            content_type_label = ext.lstrip(".")
            # 二进制文件不提取标题
            title = ""
            # 直接使用原始响应字节
            file_bytes = body

        # 文件名只 sanitize 一次（避免 001__001__ 双前缀）；title 保留原始可读标题
        # 计算内容哈希用于去重
        sha256 = hashlib.sha256(file_bytes).hexdigest()
        # 生成安全文件名
        fname = _safe_filename(title, url, idx) + ext
        # 拼接完整目标路径
        target = staging_dir / fname
        # 写入文件
        target.write_bytes(file_bytes)

        # 返回成功结果
        return FetchedFile(
            url=url,
            title=title,
            content_type=content_type_label,
            file_path=str(target),
            # 文件大小（字节）
            file_size=len(file_bytes),
            sha256=sha256,
        )
    # 捕获所有异常，统一封装为 failed 结果
    except Exception as e:
        # 返回失败结果，包含异常类型和消息
        return FetchedFile(
            url=url, title="", content_type="failed",
            file_path=None, file_size=0, sha256=None,
            # 记录异常类型和消息
            error=f"{type(e).__name__}: {e}",
        )
    # 清理资源：仅关闭本函数内部创建的 client
    finally:
        # 仅当 client 由本函数创建时才关闭，避免关闭外部传入的
        if owns_client:
            # 关闭 httpx 客户端释放连接
            client.close()
