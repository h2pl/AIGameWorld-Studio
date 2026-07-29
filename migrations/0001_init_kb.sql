-- =====================================================================
-- AIGameWorld Studio — 知识库系统表 (Knowledge Base Schema v0001 · 主题中心版)
-- 组织维度: **topic_id (主题/IP/游戏作品)**，例如 genshin / wow_worldview / xianjian
--   不再按 world / pack 切库！每个主题一个独立 Chroma collection (kb_{topic_id})，
--   干净隔离，互不影响。world/pack 只是运行时引用关系，通过 world_topic_binding
--   表做绑定（N:1，预留 N:N），GameWorld RAG 侧按 world_id 查对应 topic_id 即可。
-- 双写分离: SQLite 存文档/Chunk/任务/审计等元数据，Chroma 只存 embedding + chunk_id。
-- 兼容性: SQLite 3.45+ (JSON1 + STRICT 模式)
-- =====================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- ---------------------------------------------------------------------
-- schema migration tracker (Alembic-lite): 只记录哪些迁移文件被执行过
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS _schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  INTEGER NOT NULL DEFAULT (unixepoch('subsec') * 1000)
) STRICT;

-- ---------------------------------------------------------------------
-- knowledge_topic: 主题知识库注册表
--   每个「原神世界观」「魔兽世界设定集」「仙剑奇侠传编年史」都占一行，
--   Chroma 里对应 collection 名 = `kb_' || topic_id || '`
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_topic (
    topic_id        TEXT PRIMARY KEY,            -- 短 ID (genshin / wow_worldview / xianjian)，UI 展示
    name            TEXT NOT NULL,               -- 中文/友好名：原神世界观
    description     TEXT,                        -- 简介，UI 展示
    tags_json       TEXT,                        -- 标签 JSON 数组
    status          TEXT NOT NULL DEFAULT 'active', -- active | archived | building
    created_by      TEXT NOT NULL DEFAULT 'system',
    created_at      INTEGER NOT NULL DEFAULT (unixepoch('subsec') * 1000),
    updated_at      INTEGER NOT NULL DEFAULT (unixepoch('subsec') * 1000)
) STRICT;

-- ---------------------------------------------------------------------
-- world_topic_binding: 运行时 world → 主题知识库 的绑定关系
--   例: world_id='my_world_based_on_wow' → topic_id='wow_worldview'
--   GameWorld RAG 侧先查这张表拿到 topic_id，再去 kb_{topic_id} 检索
--   一个 world 当前只绑一个 topic (UNIQUE(world_id))，未来若要 N:N 去掉唯一约束即可
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS world_topic_binding (
    world_id        TEXT NOT NULL,
    topic_id        TEXT NOT NULL REFERENCES knowledge_topic(topic_id) ON DELETE CASCADE,
    priority        INTEGER NOT NULL DEFAULT 0, -- 预留 N:N 时的排序
    created_at      INTEGER NOT NULL DEFAULT (unixepoch('subsec') * 1000),
    PRIMARY KEY (world_id, topic_id),
    UNIQUE(world_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_world_topic_binding_topic ON world_topic_binding(topic_id);

-- ---------------------------------------------------------------------
-- kb_document: 文档级元数据
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_document (
    id              TEXT PRIMARY KEY,                -- UUID v4 (应用层生成)
    topic_id         TEXT NOT NULL REFERENCES knowledge_topic(topic_id) ON DELETE CASCADE,
    title           TEXT NOT NULL,                   -- 文档标题
    source_type     TEXT NOT NULL DEFAULT 'documents', -- lore | documents | images | videos
    file_name       TEXT NOT NULL,                   -- 原始文件名
    file_path       TEXT NOT NULL,                   -- 完整文件路径
    file_size       INTEGER NOT NULL DEFAULT 0,      -- 字节数
    sha256          TEXT NOT NULL,                   -- 文件内容 SHA256（增量去重）
    content_type    TEXT NOT NULL DEFAULT 'markdown',
    version         INTEGER NOT NULL DEFAULT 1,      -- 软版本号
    status          TEXT NOT NULL DEFAULT 'parsing', -- parsing | done | failed | deleted
    error_msg       TEXT,
    created_by      TEXT NOT NULL DEFAULT 'system',
    created_at      INTEGER NOT NULL DEFAULT (unixepoch('subsec') * 1000),
    updated_at      INTEGER NOT NULL DEFAULT (unixepoch('subsec') * 1000),
    deleted_at      INTEGER,                         -- 软删除时间戳；NULL=未删除
    -- 运行时引用：这个文档被哪些 pack 用到（仅参考，非主键维度；JSON 数组字符串，默认 '[]'）
    related_packs   TEXT NOT NULL DEFAULT '[]',
    -- 冗余存所有 tag（JSON 数组），UI 展示不用 JOIN
    tags_json       TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_kb_document_topic   ON kb_document(topic_id, status);
CREATE INDEX IF NOT EXISTS idx_kb_document_source  ON kb_document(topic_id, source_type);
CREATE INDEX IF NOT EXISTS idx_kb_document_sha256  ON kb_document(sha256);
CREATE INDEX IF NOT EXISTS idx_kb_document_deleted ON kb_document(topic_id, deleted_at) WHERE deleted_at IS NULL;

-- ---------------------------------------------------------------------
-- kb_chunk: chunk 级元数据
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_chunk (
    id              TEXT PRIMARY KEY,                -- UUID v4 = Chroma documents.id
    document_id     TEXT NOT NULL REFERENCES kb_document(id) ON DELETE CASCADE,
    topic_id         TEXT NOT NULL REFERENCES knowledge_topic(topic_id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,                -- 文档内 chunk 序号(从 0)
    chunk_count     INTEGER NOT NULL DEFAULT 1,
    text_hash       TEXT NOT NULL,                   -- chunk 文本 SHA256
    text_preview    TEXT NOT NULL,                   -- 正文前 200 字
    token_count     INTEGER NOT NULL DEFAULT 0,
    meta_json       TEXT,                            -- 冗余写回 Chroma metadata 的 JSON
    created_at      INTEGER NOT NULL DEFAULT (unixepoch('subsec') * 1000)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_kb_chunk_doc    ON kb_chunk(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_kb_chunk_topic  ON kb_chunk(topic_id);

-- ---------------------------------------------------------------------
-- kb_index_job: 索引任务
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_index_job (
    id              TEXT PRIMARY KEY,
    topic_id         TEXT NOT NULL REFERENCES knowledge_topic(topic_id) ON DELETE CASCADE,
    document_id     TEXT REFERENCES kb_document(id) ON DELETE SET NULL,  -- NULL = 整个 topic 全量重建
    mode            TEXT NOT NULL DEFAULT 'incremental', -- incremental | force | per_file
    status          TEXT NOT NULL DEFAULT 'pending',
    progress        INTEGER NOT NULL DEFAULT 0,
    file_total      INTEGER NOT NULL DEFAULT 0,
    file_done       INTEGER NOT NULL DEFAULT 0,
    chunk_total     INTEGER NOT NULL DEFAULT 0,
    error_msg       TEXT,
    started_at      INTEGER,
    finished_at     INTEGER,
    created_by      TEXT NOT NULL DEFAULT 'system',
    created_at      INTEGER NOT NULL DEFAULT (unixepoch('subsec') * 1000)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_kb_index_job_topic  ON kb_index_job(topic_id, status);
CREATE INDEX IF NOT EXISTS idx_kb_index_job_latest ON kb_index_job(topic_id, created_at DESC);

-- ---------------------------------------------------------------------
-- kb_tag / kb_document_tag: 文档标签（N:N）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_tag (
    id              TEXT PRIMARY KEY,
    topic_id         TEXT NOT NULL REFERENCES knowledge_topic(topic_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    display_name    TEXT,
    color           TEXT,
    created_at      INTEGER NOT NULL DEFAULT (unixepoch('subsec') * 1000),
    UNIQUE(topic_id, name)
) STRICT;

CREATE TABLE IF NOT EXISTS kb_document_tag (
    document_id TEXT NOT NULL REFERENCES kb_document(id) ON DELETE CASCADE,
    tag_id      TEXT NOT NULL REFERENCES kb_tag(id) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL DEFAULT (unixepoch('subsec') * 1000),
    PRIMARY KEY (document_id, tag_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_kb_document_tag_tag ON kb_document_tag(tag_id);

-- ---------------------------------------------------------------------
-- kb_audit: 审计日志
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_audit (
    id              TEXT PRIMARY KEY,
    actor           TEXT NOT NULL DEFAULT 'system',
    op              TEXT NOT NULL,                   -- topic_create / topic_delete / world_bind / doc_upload / doc_delete / index_start / index_done / retrieve / clear_collection
    topic_id         TEXT,
    world_id        TEXT,                            -- op=world_bind 时填
    document_id     TEXT,
    job_id          TEXT,
    query_text      TEXT,
    top_k           INTEGER,
    filters_json    TEXT,
    result_json     TEXT,
    error_msg       TEXT,
    created_at      INTEGER NOT NULL DEFAULT (unixepoch('subsec') * 1000)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_kb_audit_op      ON kb_audit(op, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kb_audit_topic   ON kb_audit(topic_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kb_audit_world   ON kb_audit(world_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kb_audit_actor   ON kb_audit(actor, created_at DESC);

-- =====================================================================
-- 迁移记录（本行必须作为本文件最后一条 SQL）
-- =====================================================================
INSERT OR IGNORE INTO _schema_migrations(name) VALUES ('0001_init_kb');
