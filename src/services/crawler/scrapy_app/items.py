"""Scrapy Item 定义.

与现有 ``crawler_item`` 表字段一一对应，方便 Pipeline 直接入库。
同时携带抓取时的原始字节 / HTML 文本，由 Pipeline 负责落盘。
"""

from __future__ import annotations

import scrapy


class CrawlItem(scrapy.Item):
    """单个抓取结果 Item.

    必填:
        - ``url`` (str)
        - ``item_id`` (str): 对应 crawler_item.id
        - ``job_id`` (str):  对应 crawler_job.id
        - ``idx`` (int):     序号，用于文件名前缀

    可选（Pipeline 中填充或覆盖）:
        - ``title`` (str)
        - ``content_type`` (str): ``markdown`` / ``pdf`` / ``failed``
        - ``file_bytes`` (bytes): 最终要写入磁盘的字节
        - ``file_ext`` (str): ``.md`` / ``.pdf``
        - ``sha256`` (str)
        - ``file_size`` (int)
        - ``error_msg`` (str | None)
    """

    url = scrapy.Field()
    item_id = scrapy.Field()
    job_id = scrapy.Field()
    idx = scrapy.Field()

    title = scrapy.Field()
    content_type = scrapy.Field()
    file_bytes = scrapy.Field()
    file_ext = scrapy.Field()
    sha256 = scrapy.Field()
    file_size = scrapy.Field()
    error_msg = scrapy.Field()
