-- ============================================================
-- Native AI OS - PostgreSQL 初始化脚本
-- ============================================================
-- 此脚本与生产服务器的 schema 完全一致
-- 包含：pgvector 向量搜索 + tsvector 全文搜索 + HNSW/GIN 索引
--
-- 使用方法：
--   psql aios -f scripts/init_db.sql
--
-- 前置要求：
--   1. PostgreSQL 13+
--   2. pgvector 扩展已安装（brew install pgvector 或源码编译）
-- ============================================================

-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- ------------------------------------------------------------
-- 用户表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    channel VARCHAR(50) NOT NULL,
    channel_user_id VARCHAR(100) NOT NULL,
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel, channel_user_id)
);

-- ------------------------------------------------------------
-- 用户核心记忆表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_memories (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    human_block TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id)
);

-- ------------------------------------------------------------
-- 对话记录表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    user_message TEXT NOT NULL,
    assistant_message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- 日程表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schedules (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    scheduled_at TIMESTAMP NOT NULL,
    remind_before INTEGER DEFAULT 15,
    reminded BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- 提醒表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reminders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    schedule_id INTEGER REFERENCES schedules(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    remind_at TIMESTAMP NOT NULL,
    sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- 健康数据表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS health_records (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    data_type VARCHAR(50) NOT NULL,
    value JSONB NOT NULL,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- 钉选指令表（重要规则始终注入 System Prompt）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pinned_instructions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    category VARCHAR(50),
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- 归档记忆表（向量 + 全文混合检索）
-- ------------------------------------------------------------
-- 注意：embedding 维度 1024 与 BAAI/bge-large-zh-v1.5 模型对齐
CREATE TABLE IF NOT EXISTS archival_memory (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    content_type VARCHAR(50),
    embedding vector(1024),
    metadata JSONB,
    content_tsvector tsvector,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- 索引
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_schedules_user_date ON schedules(user_id, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_health_records_user_type ON health_records(user_id, data_type, recorded_at);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reminders_sent ON reminders(sent, remind_at);
CREATE INDEX IF NOT EXISTS idx_pinned_instructions_user ON pinned_instructions(user_id, priority);
CREATE INDEX IF NOT EXISTS idx_archival_memory_user ON archival_memory(user_id, created_at);

-- 向量索引（HNSW，余弦距离）
CREATE INDEX IF NOT EXISTS archival_memory_embedding_idx
    ON archival_memory USING hnsw (embedding vector_cosine_ops);

-- 全文索引（GIN，tsvector）
CREATE INDEX IF NOT EXISTS archival_memory_content_tsvector_idx
    ON archival_memory USING GIN (content_tsvector);

-- ------------------------------------------------------------
-- 全文搜索触发器：自动维护 content_tsvector
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION archival_memory_tsvector_trigger() RETURNS trigger AS $$
BEGIN
    NEW.content_tsvector := to_tsvector('simple', NEW.content);
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tsvectorupdate ON archival_memory;
CREATE TRIGGER tsvectorupdate
BEFORE INSERT OR UPDATE ON archival_memory
FOR EACH ROW EXECUTE FUNCTION archival_memory_tsvector_trigger();
