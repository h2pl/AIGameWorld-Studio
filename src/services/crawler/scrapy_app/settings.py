"""Scrapy 全局 settings.

注意：只保留 **大写常量**；运行时注入的 job_id / staging_dir / db_path 由
runner._build_settings_dict 里再覆盖写。
"""

# === 项目基础 ===
# Scrapy 查找模块的基包名
BOT_NAME = "ags_crawler"
# Spider 模块搜索路径（scrapy_app/spiders 包）
SPIDER_MODULES = ["src.services.crawler.scrapy_app.spiders"]
# runspider 命令创建的新 Spider 会放在这个模块
NEWSPIDER_MODULE = "src.services.crawler.scrapy_app.spiders"
# robots.txt 是否遵守（Spider 自己还会走 fetcher._can_fetch 二次防御，这里也开着更合规）
ROBOTSTXT_OBEY = True

# === 并发限速 ===
# 全局最大并发请求数（runner 可按 job 覆盖，默认 8）
CONCURRENT_REQUESTS = 8
# 同一域名最大并发请求数（保护目标站）
CONCURRENT_REQUESTS_PER_DOMAIN = 2
# 同一 IP 最大并发（0 = 不按 IP 限制，只按域名）
CONCURRENT_REQUESTS_PER_IP = 0
# 对同一个域名两次请求之间的最小延迟秒数（配合 AutoThrottle）
DOWNLOAD_DELAY = 1.0
# 禁用 cookies middleware（不需要登录，减少被 fingerprint 概率）
COOKIES_ENABLED = False
# Telnet console 默认关掉（避免端口占用，且子进程里根本不需要）
TELNETCONSOLE_ENABLED = False

# === 重试 ===
# 允许对 5xx / 超时 / 连接错误 自动重试
RETRY_ENABLED = True
# 最多重试 2 次（初始请求 + 2 次重试 = 3 次尝试）
RETRY_TIMES = 2
# 触发重试的 HTTP 状态码（5xx 为主，408/429 也算典型可重试）
RETRY_HTTP_CODES = [408, 429, 500, 502, 503, 504, 522, 524]

# === 中间件（Downloader / Spider） ===
# Downloader middleware：Scrapy 默认顺序基础上注入 AutoThrottle + 自定义 UA
DOWNLOADER_MIDDLEWARES = {
    # Scrapy 自带的自动限速（根据响应延迟动态调）
    "scrapy.downloadermiddlewares.autothrottle.AutoThrottleMiddleware": 800,
}
# AutoThrottle 起始并发（每个 domain）
AUTOTHROTTLE_START_DELAY = 0.5
# AutoThrottle 最大并发延迟（遇到 429/5xx 会升到这里）
AUTOTHROTTLE_MAX_DELAY = 10.0
# AutoThrottle 目标并发（0 = 让 Scrapy 按 DOWNLOAD_DELAY 自适应）
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# AutoThrottle 调试日志（开 DEBUG 才会看到，不影响性能）
AUTOTHROTTLE_DEBUG = False
# 默认 User-Agent（浏览器 UA 伪装，避免被当成爬虫默认 UA 直接 ban）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
# 请求头默认值（Accept 等，更像正常浏览器）
DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3",
}

# === Pipeline ===
# Pipeline 顺序：先写 staging 文件，再回写 SQLite（数值越大越靠后）
ITEM_PIPELINES = {
    # 写文件：_metadata.jsonl + {item_id}.md
    "src.services.crawler.scrapy_app.pipelines.StagingWritePipeline": 300,
    # 写 DB：回写 crawler_item 状态 + 标 job 完成
    "src.services.crawler.scrapy_app.pipelines.SQLiteWritePipeline": 600,
}
# 每个 item 在 pipeline 最长处理秒数（我们都是本地 IO，留 30s 足够防卡住）
DOWNLOAD_TIMEOUT = 30

# === 扩展（Extensions） ===
# 扩展：默认保留 memusage（监控内存占用）；关掉其它不需要的
EXTENSIONS = {
    # 记录 RSS 内存占用峰值（写 stats，不显著增加开销）
    "scrapy.extensions.memusage.MemoryUsage": 0,
}
# Windows 上没有 resource.getrusage，memusage 扩展会被自动关闭；这里给个上限兜底
# 单个子进程内存上限（MB），超过会尝试软回收（Scrapy 默认实现会发 signal，Windows 没用到）
MEMUSAGE_LIMIT_MB = 4096
# 达到上限的 80% 时打 warning
MEMUSAGE_WARNING_MB = 2048
# MEMDEBUG 关掉（只在排内存泄漏时开）
MEMDEBUG_ENABLED = False

# === 日志 ===
# Scrapy 日志根级别（runner.py 会按 job 注入具体值，这里给全局默认 INFO）
LOG_LEVEL = "INFO"
# 子进程里默认不写 LOG_FILE（runner 会把 LOG_FILE 覆盖为 staging/_scrapy.log）
LOG_FILE = None
# 禁止把 print 重定向到 Scrapy 日志（子进程 stdout/stderr 由 runner 重定向）
LOG_STDOUT = False
# 日志格式：带时间戳 + 级别 + logger 名，方便排查
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
# 日期格式
LOG_DATEFORMAT = "%Y-%m-%d %H:%M:%S"
# 每个 HTTP 请求都打 DEBUG 日志太吵，默认关
LOG_ENABLED = True

# === HTTP 缓存（可选，避免重跑重复抓同一个 URL） ===
# 开启 HTTPCACHE（临时缓存，断点续跑 / 同 URL 重复爬时很有用）
HTTPCACHE_ENABLED = True
# 缓存过期秒数（默认 7 天 = 604800 秒）
HTTPCACHE_EXPIRATION_SECS = 604800
# 缓存目录（runner 会覆盖成 staging/_httpcache，这里给一个默认值仅作占位）
HTTPCACHE_DIR = ".scrapy/httpcache"
# 缓存策略：所有状态码都缓存（2xx/3xx/4xx，不包括 5xx）
HTTPCACHE_POLICY = "scrapy.extensions.httpcache.RFC2616Policy"
# 缓存存储：用 DbmCacheStorage（轻量，单文件，够用）
HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.DbmCacheStorage"

# === 调度器 / 深度优先（默认是 DFO，适合我们抓单页为主的场景） ===
# 调度器：默认 Scrapy Scheduler（内存队列 + 可配合 JobDir 持久化）
SCHEDULER = "scrapy.core.scheduler.Scheduler"
# 是否按域名做优先级（默认即可，false 表示由 start_urls 顺序决定）
SCHEDULER_PRIORITY_QUEUE = "scrapy.pqueues.ScrapyPriorityQueue"
# 队列内存占用过大时开始磁盘换出（默认 0 表示不启用磁盘队列，够用）
SCHEDULER_DISK_QUEUE = "scrapy.squeues.PickleLifoDiskQueue"
SCHEDULER_MEMORY_QUEUE = "scrapy.squeues.LifoMemoryQueue"
# 不限制 Spider 深度（offsite middleware + Spider 自己的 follow_depth 控制更靠谱）
DEPTH_LIMIT = 0
# 请求深度统计，开着但不限制
DEPTH_STATS_VERBOSE = False

# === 自定义注入（由 runner.py 覆盖，仅做占位） ===
# 当前 job_id（runner 会注入）
JOB_ID = ""
# 暂存目录（runner 会注入）
STAGING_DIR = ""
# 全局 SQLite DB 路径（runner 会注入）
DB_PATH = ""
