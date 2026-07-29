"""Scrapy-based crawler for AIGameWorld-Studio.

Scrapy 项目根。通过 :mod:`runner` 模块在 CrawlerService 内部
以子线程方式启动 CrawlerProcess，共享同一 Python 进程（避免 subprocess
带来的环境/路径/日志隔离问题）。
"""
