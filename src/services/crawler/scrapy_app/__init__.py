"""Scrapy-based crawler for AIGameWorld-Studio.

Scrapy 项目根。通过 :mod:`runner` 模块在 CrawlerService 内部
以子线程方式启动 CrawlerProcess，共享同一 Python 进程（避免 subprocess
带来的环境/路径/日志隔离问题）。
"""
# Scrapy 应用包 / Scrapy App
# - settings.py: Scrapy 全局配置（延迟、并发、Header、Pipeline 顺序）
# - runner.py:   封装 CrawlerProcess 启动（线程隔离 + AsyncioSelectorReactor）
# - extensions.py: Spider opened/closed 事件 → 更新 crawler_job 状态
# - pipelines.py: StagingWritePipeline（落盘）+ SQLiteWritePipeline（更新 DB）
# - spiders/:    TopicSearchSpider + GenericCrawlSpider
