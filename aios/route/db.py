"""Database helpers for routing_traces (Tier 2 self-evolving routing memory)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from aios.pg import PgClient


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


async def record_trace(
    pg: PgClient,
    *,
    query: str,
    routed_to: str,
    user_id: int | None = None,
    spawn_label: str | None = None,
    spawn_task_id: str | None = None,
    intent_index: int = 0,
    confidence: float | None = None,
    embedding: list[float] | None = None,
) -> int:
    """Insert a new routing trace; returns the new id. outcome defaults to 'pending'."""
    if embedding is not None and len(embedding) != 1024:
        raise ValueError(f"embedding must be 1024-d, got {len(embedding)}")

    args: list[Any] = [
        user_id, query, routed_to, spawn_label, spawn_task_id,
        intent_index, confidence,
    ]
    embed_clause = "NULL"
    if embedding is not None:
        args.append(_vector_literal(embedding))
        embed_clause = f"${len(args)}::vector"

    sql = f"""
        INSERT INTO routing_traces (
            user_id, query, routed_to, spawn_label, spawn_task_id,
            intent_index, confidence, query_embedding
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, {embed_clause})
        RETURNING id
    """
    async with pg.acquire() as conn:
        return await conn.fetchval(sql, *args)


async def finalize_trace(
    pg: PgClient,
    *,
    trace_id: int,
    outcome: str,
    duration_ms: int | None = None,
    error: str | None = None,
) -> bool:
    """Update an existing trace's outcome ('success' / 'reroute' / 'failed')."""
    if outcome not in ("success", "reroute", "failed"):
        raise ValueError(f"outcome must be success/reroute/failed, got {outcome!r}")
    async with pg.acquire() as conn:
        result = await conn.execute(
            "UPDATE routing_traces "
            "SET outcome = $2, duration_ms = $3, error = $4, finalized_at = NOW() "
            "WHERE id = $1",
            trace_id, outcome, duration_ms, error,
        )
    # asyncpg execute returns "UPDATE N"
    return result.endswith(" 1")


async def feedback_by_task(
    pg: PgClient, *, spawn_task_id: str, feedback: str
) -> int:
    """Backfill user_feedback for all traces tied to a spawn_task_id; returns affected rows."""
    if feedback not in ("thumbs_up", "thumbs_down"):
        raise ValueError(f"feedback must be thumbs_up/thumbs_down, got {feedback!r}")
    async with pg.acquire() as conn:
        result = await conn.execute(
            "UPDATE routing_traces "
            "SET user_feedback = $2 "
            "WHERE spawn_task_id = $1",
            spawn_task_id, feedback,
        )
    # "UPDATE N" → N
    try:
        return int(result.rsplit(" ", 1)[1])
    except (IndexError, ValueError):
        return 0


async def fetch_examples(
    pg: PgClient,
    *,
    agent_name: str,
    top: int = 8,
    recent_days: int = 30,
    min_confidence: float = 0.5,
    require_positive_feedback: bool = False,
) -> list[dict]:
    """Fetch top-N high-quality recent queries routed to <agent_name>.

    Used by Master at startup to populate the {{ROUTING_EXAMPLES}} placeholder
    in each subagent's SKILL.md description.

    Returns list of dicts: {query, confidence, user_feedback, created_at}.
    """
    feedback_filter = "AND user_feedback = 'thumbs_up'" if require_positive_feedback else ""

    sql = f"""
        SELECT DISTINCT ON (query)
               query, confidence, user_feedback, created_at
        FROM routing_traces
        WHERE routed_to = $1
          AND outcome = 'success'
          AND ($2::real IS NULL OR confidence IS NULL OR confidence >= $2)
          AND created_at >= NOW() - ($3 || ' days')::interval
          {feedback_filter}
        ORDER BY query, created_at DESC
    """
    async with pg.acquire() as conn:
        rows = await conn.fetch(sql, agent_name, min_confidence, str(recent_days))

    # Re-rank: boost user_feedback=thumbs_up, then by confidence desc, then recency desc
    def _score(r: Any) -> tuple[int, float, datetime]:
        fb_boost = 1 if r["user_feedback"] == "thumbs_up" else 0
        return (fb_boost, r["confidence"] or 0.0, r["created_at"])

    rows = sorted(rows, key=_score, reverse=True)[:top]
    return [
        {
            "query": r["query"],
            "confidence": r["confidence"],
            "user_feedback": r["user_feedback"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


async def count_traces(pg: PgClient, *, agent_name: str) -> int:
    """How many traces (any outcome) routed to this agent. Used to decide cold-start fallback."""
    async with pg.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM routing_traces WHERE routed_to = $1",
            agent_name,
        )


def load_seed_examples(agent_name: str, workspace_root: str | None = None) -> list[dict]:
    """Load workspace/agents/<agent_name>/seed_examples.jsonl as fallback."""
    from pathlib import Path

    if workspace_root is None:
        # Default: AIOS_HOME/workspace, else <repo_root>/workspace
        root = Path(__file__).resolve().parents[2] / "workspace"
    else:
        root = Path(workspace_root)

    seed_path = root / "agents" / agent_name / "seed_examples.jsonl"
    if not seed_path.exists():
        return []

    out: list[dict] = []
    with seed_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
