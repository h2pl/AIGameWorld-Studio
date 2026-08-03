-- 爬虫系统表 / Crawler job & item tracking
--
-- 与 kb_document / kb_chunk 完全解耦：
--   爬虫只负责搜/抓/存本地暂存区（.staging/crawler/{job_id}/），
--   人工审核 promote 后才进入 knowledge/documents/ 并由 kb pipeline 索引。
--   本表只记录爬虫任务本身的元数据，不碰向量库 / 不写 kb_document。

CREATE TABLE IF NOT EXISTS crawler_job (
    id           TEXT PRIMARY KEY,            -- UUID
    topic_id     TEXT NOT NULL,               -- 所属主题（暂存区归属于此 topic）
    query        TEXT,                        -- 搜索模式时的关键词（mode='search' 时有值）
    mode         TEXT NOT NULL,               -- 'urls' | 'search'
    status       TEXT NOT NULL,               -- 'pending' | 'running' | 'done' | 'failed' | 'discarded'
    source_type  TEXT NOT NULL DEFAULT 'documents',  -- promote 时落到 knowledge/{source_type}/
    total_items  INTEGER NOT NULL DEFAULT 0,  -- 计划抓取的 URL 数
    done_items   INTEGER NOT NULL DEFAULT 0,  -- 成功抓取数
    staging_dir  TEXT NOT NULL,               -- 暂存目录相对项目根的路径
    created_by   TEXT NOT NULL DEFAULT 'system',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now')),
    started_at   TEXT,
    finished_at  TEXT,
    error_msg    TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS crawler_item (
    id           TEXT PRIMARY KEY,            -- UUID
    job_id       TEXT NOT NULL REFERENCES crawler_job(id) ON DELETE CASCADE,
    url          TEXT NOT NULL,               -- 抓取的源 URL
    title        TEXT,                        -- 搜索结果标题 / HTML <title>
    content_type TEXT,                        -- 'markdown' | 'pdf' | 'failed'
    file_path    TEXT,                        -- 暂存区内相对路径（promoted 后置空）
    file_size    INTEGER,
    sha256       TEXT,                        -- 内容哈希，用于去重
    status       TEXT NOT NULL,               -- 'pending' | 'fetched' | 'failed' | 'promoted' | 'discarded'
    error_msg    TEXT,
    fetched_at   TEXT,                     -- 抓取完成时间
    promoted_at  TEXT                      -- promote 到 knowledge/ 的时间
) STRICT;

CREATE INDEX IF NOT EXISTS idx_crawler_item_job ON crawler_item(job_id);
CREATE INDEX IF NOT EXISTS idx_crawler_job_topic ON crawler_job(topic_id);
CREATE INDEX IF NOT EXISTS idx_crawler_job_status ON crawler_job(status);
