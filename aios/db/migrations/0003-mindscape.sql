-- Migration: 0003-mindscape
-- Purpose: watch_list（想看/想读清单）+ learning_plans（学习计划）for the mindscape sub-agent.
--          Notes/memos reuse the existing archival_memory table with content_type='note',
--          so no extra table is needed for that.
-- Idempotent: re-running has no side effects.

CREATE EXTENSION IF NOT EXISTS vector;

-- =========================================================================
-- watch_list — books / movies / shows / podcasts / articles I want to consume
-- =========================================================================

CREATE TABLE IF NOT EXISTS watch_list (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL DEFAULT 'book',
    -- book | movie | show | podcast | article | other
    title           TEXT NOT NULL,
    author          TEXT,                            -- author / director / host
    status          TEXT NOT NULL DEFAULT 'todo',
    -- todo | doing | done | dropped
    rating          REAL,                            -- my own 0-10 score (after finishing)
    external_score  REAL,                            -- douban / goodreads / imdb score
    external_source TEXT,                            -- 'douban' | 'goodreads' | 'imdb' | ...
    source_url      TEXT,
    summary         TEXT,                            -- short blurb (often from web_search)
    notes           TEXT,                            -- my own thoughts after reading
    embedding       VECTOR(1024),                    -- title + summary semantic vector
    metadata        JSONB,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    UNIQUE (user_id, kind, title)
);

CREATE INDEX IF NOT EXISTS idx_watch_list_user_status
    ON watch_list (user_id, status, added_at DESC);
CREATE INDEX IF NOT EXISTS idx_watch_list_kind
    ON watch_list (kind, status);
CREATE INDEX IF NOT EXISTS idx_watch_list_external_score
    ON watch_list (external_score DESC NULLS LAST)
    WHERE status = 'todo';
CREATE INDEX IF NOT EXISTS idx_watch_list_embedding
    ON watch_list
    USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

COMMENT ON TABLE watch_list IS
    'Mindscape sub-agent: things I want to / am / have consumed (books / movies / shows ...).';
COMMENT ON COLUMN watch_list.external_score IS
    'Aggregate score from a public source (e.g. douban 8.7), filled by the sub-agent at add time via web_search.';
COMMENT ON COLUMN watch_list.rating IS
    'My personal 0-10 rating, recorded when status=done.';

-- =========================================================================
-- learning_plans — long-running goals with milestones + review cadence
-- =========================================================================

CREATE TABLE IF NOT EXISTS learning_plans (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    goal            TEXT,                            -- "在 6 个月内能裸听 NPR"
    milestones      JSONB,                           -- [{title, done, due_date, notes}]
    review_cron     TEXT,                            -- 'weekly' | 'biweekly' | 'monthly' | cron expr
    status          TEXT NOT NULL DEFAULT 'doing',
    -- doing | done | paused | dropped
    notes           TEXT,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learning_plans_user_status
    ON learning_plans (user_id, status, created_at DESC);

COMMENT ON TABLE learning_plans IS
    'Mindscape sub-agent: long-running learning goals with optional review cadence.';
COMMENT ON COLUMN learning_plans.milestones IS
    'JSON array of milestone objects, e.g. [{"title": "读完 Pragmatic Programmer", "done": true, "due": "2026-05-30"}].';
