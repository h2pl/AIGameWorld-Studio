"""Crawler job/item 持久化层 —— 复用 SQLiteStore，不引入新依赖。

所有方法返回 dict（而非 sqlite3.Row），方便上层直接 JSON 序列化。
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterable

from ...utils.sqlite_store import SQLiteStore


# sqlite3.Row 转普通 dict 工具函数
# 参数 row：sqlite3.Row 对象或 None
# 返回 dict[str, Any]：列名→值的字典；row 为 None 时返回空字典
def _row_to_dict(row) -> dict[str, Any]:
    """sqlite3.Row → dict。"""
    # 行为空（查询无结果）
    if row is None:
        # 返回空字典
        return {}
    # 遍历行的所有列名，构造 {列名: 值}
    return {k: row[k] for k in row.keys()}


# 获取当前 Unix 时间戳（毫秒级整数）
# 返回 int：1970-01-01 以来的毫秒数
def _now_ms() -> int:
    # 秒转毫秒，截断小数
    return int(time.time() * 1000)


# ------------------------------------------------------------------
# crawler_job
# ------------------------------------------------------------------

# 创建 crawler_job 记录
# 参数 store：SQLiteStore 实例
# 参数 job_id：job 主键 ID
# 参数 topic_id：所属主题 ID
# 参数 mode：抓取模式（urls/search）
# 参数 query：搜索关键词（search 模式），urls 模式为 None
# 参数 source_type：promote 时目标子目录名
# 参数 staging_dir：暂存目录相对路径
# 参数 total_items：总条目数
# 参数 created_by：创建者标识（审计用）
# 返回 dict[str, Any]：刚创建的 job 记录字典；创建失败返回空 dict
def create_job(
    store: SQLiteStore,
    *,
    job_id: str,
    topic_id: str,
    mode: str,
    query: str | None,
    source_type: str,
    staging_dir: str,
    total_items: int,
    created_by: str = "ui",
) -> dict[str, Any]:
    # 执行 INSERT 语句创建 job 记录
    store.execute(
        """
        INSERT INTO crawler_job
            (id, topic_id, query, mode, status, source_type,
             total_items, done_items, staging_dir, created_by, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?, 0, ?, ?, ?)
        """,
        # status 默认 pending，done_items 默认 0
        (job_id, topic_id, query, mode, source_type, total_items, staging_dir, created_by, _now_ms()),
    )
    # 查回刚插入的记录返回，失败则空 dict
    return get_job(store, job_id) or {}


# 按主键查询单个 crawler_job 记录
# 参数 store：SQLiteStore 实例
# 参数 job_id：job 主键 ID
# 返回 dict | None：job 字典；不存在返回 None
def get_job(store: SQLiteStore, job_id: str) -> dict[str, Any] | None:
    # 参数化查询防注入
    row = store.fetch_one("SELECT * FROM crawler_job WHERE id = ?", (job_id,))
    # 有行就转 dict，否则 None
    return _row_to_dict(row) if row else None


# 分页列出 crawler_job 记录，可按 topic 过滤
# 参数 store：SQLiteStore 实例
# 参数 topic_id：可选的主题过滤条件
# 参数 limit：最大返回条数
# 参数 offset：分页偏移量
# 返回 list[dict]：按创建时间倒序的 job 字典列表
def list_jobs(
    store: SQLiteStore,
    topic_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    # 指定了 topic 过滤条件
    if topic_id:
        rows = store.fetch_all(
            # 按创建时间倒序
            "SELECT * FROM crawler_job WHERE topic_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            # 绑定三个参数
            (topic_id, limit, offset),
        )
    else:
        # 未指定 topic：列出全部 job
        rows = store.fetch_all(
            "SELECT * FROM crawler_job ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    # 每行转 dict 组成列表返回
    return [_row_to_dict(r) for r in rows]


# 更新 crawler_job 状态及相关字段（动态拼装 UPDATE SQL）
# 参数 store：SQLiteStore 实例
# 参数 job_id：job 主键 ID
# 参数 status：新的状态值
# 参数 done_items：可选的已完成条目数
# 参数 error_msg：可选的错误消息
# 参数 started：是否标记 started_at 时间戳
# 参数 finished：是否标记 finished_at 时间戳
# 返回 None
def update_job_status(
    store: SQLiteStore,
    job_id: str,
    status: str,
    *,
    done_items: int | None = None,
    error_msg: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    # status 是必更新字段
    set_fields: list[str] = ["status = ?"]
    # 对应参数列表，顺序与 set_fields 一一对应
    params: list[Any] = [status]
    # 调用方显式传了 done_items
    if done_items is not None:
        # 加入 SET 子句
        set_fields.append("done_items = ?")
        # 参数入栈
        params.append(done_items)
    # 显式传了错误消息
    if error_msg is not None:
        set_fields.append("error_msg = ?")
        params.append(error_msg)
    # 统一取一次时间戳，保证 started_at/finished_at 相同
    now = _now_ms()
    # 标记任务开始
    if started:
        set_fields.append("started_at = ?")
        params.append(now)
    # 标记任务结束（成功/失败/废弃）
    if finished:
        set_fields.append("finished_at = ?")
        params.append(now)
    # WHERE 条件的 id 参数放最后
    params.append(job_id)
    # 执行动态 SQL
    store.execute(f"UPDATE crawler_job SET {', '.join(set_fields)} WHERE id = ?", tuple(params))


# ------------------------------------------------------------------
# crawler_item
# ------------------------------------------------------------------

# 创建单条 crawler_item 记录（URL 级别），初始状态 pending
# 参数 store：SQLiteStore 实例
# 参数 item_id：item 主键 ID
# 参数 job_id：所属 job ID
# 参数 url：目标 URL
# 参数 title：可选的初始标题
# 返回 dict：创建的 item 字典（不回查 DB，直接构造）
def create_item(
    store: SQLiteStore,
    *,
    item_id: str,
    job_id: str,
    url: str,
    title: str = "",
) -> dict[str, Any]:
    # 执行插入 SQL
    store.execute(
        """
        INSERT INTO crawler_item (id, job_id, url, title, status)
        VALUES (?, ?, ?, ?, 'pending')
        """,
        # status 默认 pending 等待抓取
        (item_id, job_id, url, title),
    )
    # 直接返回构造的字典，不查回
    return {"id": item_id, "job_id": job_id, "url": url, "title": title, "status": "pending"}


# 抓取完成后回填 crawler_item 的元数据和状态
# 参数 store：SQLiteStore 实例
# 参数 item_id：item 主键 ID
# 参数 content_type：内容类型标签（markdown/pdf/failed 等）
# 参数 file_path：落盘后的文件绝对路径；失败时为 None
# 参数 file_size：文件字节数；失败时为 0
# 参数 sha256：文件内容 SHA256 哈希；失败时为 None
# 参数 title：可选的标题更新；非 None 才会 UPDATE
# 参数 error_msg：可选的错误消息；失败分支使用
# 参数 status：新状态，默认 fetched
# 返回 None
def update_item_fetched(
    store: SQLiteStore,
    item_id: str,
    *,
    content_type: str,
    file_path: str | None,
    file_size: int,
    sha256: str | None,
    title: str | None = None,
    error_msg: str | None = None,
    status: str = "fetched",
) -> None:
    # 必更字段列表
    set_fields: list[str] = ["content_type = ?", "file_path = ?", "file_size = ?", "sha256 = ?", "status = ?", "fetched_at = ?"]
    # 必更字段的参数
    params: list[Any] = [content_type, file_path, file_size, sha256, status, _now_ms()]
    # 传了新标题（非 None 才更新，空串也可能是有效值）
    if title is not None:
        # 追加 title 更新
        set_fields.append("title = ?")
        # title 参数入栈
        params.append(title)
    # 传了错误消息（失败分支）
    if error_msg is not None:
        # 追加 error_msg 更新
        set_fields.append("error_msg = ?")
        # 参数入栈
        params.append(error_msg)
    # WHERE 条件 id 参数放最后
    params.append(item_id)
    # 执行动态拼装的 UPDATE
    store.execute(f"UPDATE crawler_item SET {', '.join(set_fields)} WHERE id = ?", tuple(params))


# 列出某个 job 的全部 crawler_item，按 id 排序（与创建顺序一致）
# 参数 store：SQLiteStore 实例
# 参数 job_id：所属 job ID
# 返回 list[dict]：item 字典列表，按 id 升序
def list_items(store: SQLiteStore, job_id: str) -> list[dict[str, Any]]:
    # 参数化查询，按 id 排序（与创建顺序一致）
    rows = store.fetch_all(
        "SELECT * FROM crawler_item WHERE job_id = ? ORDER BY id",
        (job_id,),
    )
    # 每行转 dict 后返回列表
    return [_row_to_dict(r) for r in rows]


# 按主键查询单个 crawler_item 记录
# 参数 store：SQLiteStore 实例
# 参数 item_id：item 主键 ID
# 返回 dict | None：item 字典；不存在返回 None
def get_item(store: SQLiteStore, item_id: str) -> dict[str, Any] | None:
    # 参数化查询
    row = store.fetch_one("SELECT * FROM crawler_item WHERE id = ?", (item_id,))
    # 空返回 None
    return _row_to_dict(row) if row else None


# promote 后更新 crawler_item 状态为 promoted，并可选更新文件路径
# 参数 store：SQLiteStore 实例
# 参数 item_id：item 主键 ID
# 参数 new_path：可选的 promote 后的新路径（相对路径）
# 返回 None
def mark_item_promoted(store: SQLiteStore, item_id: str, new_path: str | None = None) -> None:
    # 固定更状态+ promote 时间
    set_fields: list[str] = ["status = 'promoted'", "promoted_at = ?"]
    # promoted_at 参数
    params: list[Any] = [_now_ms()]
    # 传了 promote 后的新路径
    if new_path is not None:
        # 更 file_path 为 promote 后目标路径
        set_fields.append("file_path = ?")
        # 参数入栈
        params.append(new_path)
    # WHERE 条件 id
    params.append(item_id)
    # 执行 UPDATE
    store.execute(f"UPDATE crawler_item SET {', '.join(set_fields)} WHERE id = ?", tuple(params))


# discard job 时把该 job 下已成功抓取的 item 置 discarded 状态
# 参数 store：SQLiteStore 实例
# 参数 job_id：job ID
# 返回 None
def mark_job_items_discarded(store: SQLiteStore, job_id: str) -> None:
    # 仅更状态为 fetched 的 item（pending/failed 的维持原状，避免无谓更新）
    store.execute(
        "UPDATE crawler_item SET status = 'discarded' WHERE job_id = ? AND status = 'fetched'",
        (job_id,),
    )
