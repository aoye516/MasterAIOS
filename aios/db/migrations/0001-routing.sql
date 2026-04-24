-- Migration: 0001-routing
-- Purpose: routing_traces 表 —— Tier 2 自演化路由记忆的底座
-- Idempotent: 多次运行无副作用

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS routing_traces (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    -- 用户原话（多 intent 拆分时为该 intent 的子句）
    query           TEXT NOT NULL,
    query_embedding VECTOR(1024),

    -- Master 决定的目标子代理（kebab-case agent name，如 'steward'）
    routed_to       TEXT NOT NULL,
    -- nanobot 的 spawn label / task_id，便于和日志对照
    spawn_label     TEXT,
    spawn_task_id   TEXT,

    -- 多 intent 拆分时此 trace 在用户消息中的次序，0-based
    intent_index    INTEGER NOT NULL DEFAULT 0,
    -- Master 自评的路由信心 0..1
    confidence      REAL,

    -- 'pending' / 'success' / 'reroute' / 'failed'
    -- pending: 子代理尚未完成；success: 子代理给出有效回应；
    -- reroute: 路由错了，Master 又 spawn 了别的；failed: 子代理报错
    outcome         TEXT NOT NULL DEFAULT 'pending',
    -- 'thumbs_up' / 'thumbs_down' / null —— 飞书 reaction 异步回灌
    user_feedback   TEXT,

    error           TEXT,
    duration_ms     INTEGER,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finalized_at    TIMESTAMPTZ
);

-- 主用途索引：aios route examples <agent> 拉每个 agent 的近期 success traces
CREATE INDEX IF NOT EXISTS idx_routing_traces_agent_recent
    ON routing_traces (routed_to, outcome, created_at DESC)
    WHERE outcome = 'success';

-- 评估用：按时间扫描整体路由情况
CREATE INDEX IF NOT EXISTS idx_routing_traces_created_at
    ON routing_traces (created_at DESC);

-- 反馈回灌用：按 spawn_task_id 找回原 trace
CREATE INDEX IF NOT EXISTS idx_routing_traces_task_id
    ON routing_traces (spawn_task_id)
    WHERE spawn_task_id IS NOT NULL;

-- 向量召回（用于 Tier 3 embedding pre-router，未来用）
CREATE INDEX IF NOT EXISTS idx_routing_traces_embedding
    ON routing_traces
    USING hnsw (query_embedding vector_cosine_ops)
    WHERE query_embedding IS NOT NULL;

COMMENT ON TABLE routing_traces IS
    'AIOS Tier 2 self-evolving routing memory. Append-only. See docs/agent-contract.md §3.2';

COMMENT ON COLUMN routing_traces.outcome IS
    'pending → success / reroute / failed. Updated when subagent finishes (LLM-as-judge by Master).';

COMMENT ON COLUMN routing_traces.user_feedback IS
    'Async backfill from feishu reaction emoji (👍 / 👎). See aios/feedback module.';
