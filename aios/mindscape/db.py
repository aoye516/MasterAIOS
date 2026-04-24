"""Database helpers for the mindscape sub-agent.

Two new tables (watch_list, learning_plans) + reuse archival_memory for notes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from aios.pg import PgClient


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


# =============================================================================
# Notes (archival_memory with content_type='note')
# =============================================================================

NOTE_CONTENT_TYPE = "note"


async def add_note(
    pg: PgClient,
    *,
    user_id: int | None,
    content: str,
    tags: list[str] | None = None,
    embedding: list[float] | None = None,
) -> int:
    metadata = {"source": "mindscape"}
    if tags:
        metadata["tags"] = tags

    args: list[Any] = [user_id, content, NOTE_CONTENT_TYPE, json.dumps(metadata)]
    embed_clause = "NULL"
    if embedding is not None:
        if len(embedding) != 1024:
            raise ValueError(f"embedding must be 1024-d, got {len(embedding)}")
        args.append(_vector_literal(embedding))
        embed_clause = f"${len(args)}::vector"

    sql = f"""
        INSERT INTO archival_memory (user_id, content, content_type, metadata, embedding)
        VALUES ($1, $2, $3, $4::jsonb, {embed_clause})
        RETURNING id
    """
    async with pg.acquire() as conn:
        return await conn.fetchval(sql, *args)


# =============================================================================
# watch_list
# =============================================================================

WATCH_KINDS = ("book", "movie", "show", "podcast", "article", "other")
WATCH_STATUSES = ("todo", "doing", "done", "dropped")


@dataclass
class WatchInput:
    kind: str
    title: str
    author: str | None = None
    status: str = "todo"
    rating: float | None = None
    external_score: float | None = None
    external_source: str | None = None
    source_url: str | None = None
    summary: str | None = None
    notes: str | None = None
    embedding: list[float] | None = None
    metadata: dict | None = None


async def add_watch_item(
    pg: PgClient, *, user_id: int | None, item: WatchInput
) -> int:
    if item.kind not in WATCH_KINDS:
        raise ValueError(f"kind must be one of {WATCH_KINDS}, got {item.kind!r}")
    if item.status not in WATCH_STATUSES:
        raise ValueError(f"status must be one of {WATCH_STATUSES}, got {item.status!r}")

    args: list[Any] = [
        user_id, item.kind, item.title, item.author, item.status,
        item.rating, item.external_score, item.external_source,
        item.source_url, item.summary, item.notes,
    ]
    embed_clause = "NULL"
    if item.embedding is not None:
        if len(item.embedding) != 1024:
            raise ValueError(f"embedding must be 1024-d, got {len(item.embedding)}")
        args.append(_vector_literal(item.embedding))
        embed_clause = f"${len(args)}::vector"

    args.append(json.dumps(item.metadata) if item.metadata is not None else None)
    metadata_idx = len(args)

    sql = f"""
        INSERT INTO watch_list
            (user_id, kind, title, author, status, rating,
             external_score, external_source, source_url, summary, notes,
             embedding, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, {embed_clause}, ${metadata_idx}::jsonb)
        ON CONFLICT (user_id, kind, title) DO UPDATE
        SET author          = COALESCE(EXCLUDED.author, watch_list.author),
            external_score  = COALESCE(EXCLUDED.external_score, watch_list.external_score),
            external_source = COALESCE(EXCLUDED.external_source, watch_list.external_source),
            source_url      = COALESCE(EXCLUDED.source_url, watch_list.source_url),
            summary         = COALESCE(EXCLUDED.summary, watch_list.summary),
            notes           = COALESCE(EXCLUDED.notes, watch_list.notes),
            embedding       = COALESCE(EXCLUDED.embedding, watch_list.embedding),
            metadata        = COALESCE(EXCLUDED.metadata, watch_list.metadata)
        RETURNING id
    """
    async with pg.acquire() as conn:
        return await conn.fetchval(sql, *args)


async def list_watch(
    pg: PgClient,
    *,
    user_id: int | None = None,
    kind: str | None = None,
    status: str | None = None,
    sort: str = "added",        # added | score
    top: int = 20,
) -> list[dict]:
    sort_clause = {
        "added": "added_at DESC",
        "score": "external_score DESC NULLS LAST, added_at DESC",
        "rating": "rating DESC NULLS LAST, added_at DESC",
    }.get(sort, "added_at DESC")

    args: list[Any] = []
    where: list[str] = []
    if user_id is not None:
        args.append(user_id)
        where.append(f"user_id = ${len(args)}")
    if kind is not None:
        args.append(kind)
        where.append(f"kind = ${len(args)}")
    if status is not None:
        args.append(status)
        where.append(f"status = ${len(args)}")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    args.append(top)
    sql = f"""
        SELECT id, kind, title, author, status, rating, external_score,
               external_source, source_url, summary, added_at, finished_at
        FROM watch_list
        {where_sql}
        ORDER BY {sort_clause}
        LIMIT ${len(args)}
    """
    async with pg.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def finish_watch(
    pg: PgClient, *, item_id: int,
    rating: float | None = None, notes: str | None = None,
) -> bool:
    async with pg.acquire() as conn:
        result = await conn.execute(
            "UPDATE watch_list SET status = 'done', "
            "rating = COALESCE($2, rating), "
            "notes = COALESCE($3, notes), "
            "finished_at = NOW() "
            "WHERE id = $1",
            item_id, rating, notes,
        )
    return result.endswith(" 1")


async def drop_watch(pg: PgClient, *, item_id: int) -> bool:
    async with pg.acquire() as conn:
        result = await conn.execute(
            "UPDATE watch_list SET status = 'dropped' WHERE id = $1",
            item_id,
        )
    return result.endswith(" 1")


async def find_watch_semantic(
    pg: PgClient, *, user_id: int | None, query_embedding: list[float], top: int = 5,
) -> list[dict]:
    """Vector recall over watch_list.embedding (cosine distance)."""
    if len(query_embedding) != 1024:
        raise ValueError(f"query_embedding must be 1024-d, got {len(query_embedding)}")
    vec = _vector_literal(query_embedding)

    args: list[Any] = [vec]
    where = ["embedding IS NOT NULL"]
    if user_id is not None:
        args.append(user_id)
        where.append(f"user_id = ${len(args)}")

    sql = f"""
        SELECT id, kind, title, author, status, external_score, summary,
               embedding <=> $1::vector AS distance
        FROM watch_list
        WHERE {' AND '.join(where)}
        ORDER BY embedding <=> $1::vector
        LIMIT {int(top)}
    """
    async with pg.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


# =============================================================================
# learning_plans
# =============================================================================

PLAN_STATUSES = ("doing", "done", "paused", "dropped")


@dataclass
class PlanInput:
    name: str
    goal: str | None = None
    milestones: list[dict] | None = None
    review_cron: str | None = None
    status: str = "doing"
    notes: str | None = None
    metadata: dict | None = None


async def add_plan(
    pg: PgClient, *, user_id: int | None, plan: PlanInput
) -> int:
    if plan.status not in PLAN_STATUSES:
        raise ValueError(f"status must be one of {PLAN_STATUSES}, got {plan.status!r}")

    async with pg.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO learning_plans
              (user_id, name, goal, milestones, review_cron, status, notes, metadata)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8::jsonb)
            RETURNING id
            """,
            user_id, plan.name, plan.goal,
            json.dumps(plan.milestones) if plan.milestones is not None else None,
            plan.review_cron, plan.status, plan.notes,
            json.dumps(plan.metadata) if plan.metadata is not None else None,
        )


async def list_plans(
    pg: PgClient, *, user_id: int | None = None, status: str | None = None,
    limit: int = 20,
) -> list[dict]:
    args: list[Any] = []
    where: list[str] = []
    if user_id is not None:
        args.append(user_id)
        where.append(f"user_id = ${len(args)}")
    if status is not None:
        args.append(status)
        where.append(f"status = ${len(args)}")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    args.append(limit)
    sql = f"""
        SELECT id, name, goal, milestones, review_cron, status,
               notes, created_at, updated_at
        FROM learning_plans
        {where_sql}
        ORDER BY status, updated_at DESC
        LIMIT ${len(args)}
    """
    async with pg.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def update_plan(
    pg: PgClient, *, plan_id: int,
    status: str | None = None,
    milestones: list[dict] | None = None,
    notes: str | None = None,
    review_cron: str | None = None,
) -> bool:
    sets: list[str] = ["updated_at = NOW()"]
    args: list[Any] = []
    if status is not None:
        if status not in PLAN_STATUSES:
            raise ValueError(f"status must be one of {PLAN_STATUSES}, got {status!r}")
        args.append(status)
        sets.append(f"status = ${len(args) + 1}")
    if milestones is not None:
        args.append(json.dumps(milestones))
        sets.append(f"milestones = ${len(args) + 1}::jsonb")
    if notes is not None:
        args.append(notes)
        sets.append(f"notes = ${len(args) + 1}")
    if review_cron is not None:
        args.append(review_cron)
        sets.append(f"review_cron = ${len(args) + 1}")

    sql = f"UPDATE learning_plans SET {', '.join(sets)} WHERE id = $1"
    async with pg.acquire() as conn:
        result = await conn.execute(sql, plan_id, *args)
    return result.endswith(" 1")
