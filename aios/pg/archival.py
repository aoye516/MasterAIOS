"""Archival memory bridge.

Reads from `archival_memory` table (vector(1024) + tsvector). Returns rows in
order of relevance using a hybrid strategy:

  1. If `embedding` is provided: cosine distance ranking via pgvector HNSW
  2. Otherwise: full-text rank using `to_tsquery('simple', plainto)` ↔ tsvector

The CLI / skill side decides whether to compute an embedding (requires the
SiliconFlow embedding API key); in degraded mode we always have keyword search.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Sequence

from aios.pg.client import PgClient


@dataclass
class ArchivalRow:
    id: int
    user_id: int | None
    content: str
    content_type: str | None
    metadata: dict | None
    created_at: datetime
    score: float | None = None  # distance (lower = better) for vector, rank for tsvector

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at
        return d


def _vector_literal(vec: Sequence[float]) -> str:
    """asyncpg has no native pgvector codec by default; pass as text literal."""
    inner = ",".join(f"{x:.6f}" for x in vec)
    return f"[{inner}]"


async def search_archival(
    pg: PgClient,
    query: str,
    *,
    user_id: int | None = None,
    embedding: Sequence[float] | None = None,
    limit: int = 5,
) -> list[ArchivalRow]:
    """Hybrid search over archival_memory.

    Behaviour:
      - If `embedding` is provided, ANN search on `embedding` (cosine).
      - Else, tsvector rank against `content_tsvector` using plainto_tsquery.
      - Optional `user_id` filter.
    """
    if not query.strip():
        raise ValueError("query must not be empty")

    if embedding is not None:
        if len(embedding) != 1024:
            raise ValueError(f"embedding must be length 1024, got {len(embedding)}")
        vec_literal = _vector_literal(embedding)
        args: list = [vec_literal]
        where_user = ""
        if user_id is not None:
            args.append(user_id)
            where_user = f"AND user_id = ${len(args)}"
        sql = f"""
            SELECT id, user_id, content, content_type, metadata, created_at,
                   embedding <=> $1::vector AS score
            FROM archival_memory
            WHERE embedding IS NOT NULL {where_user}
            ORDER BY embedding <=> $1::vector
            LIMIT {int(limit)}
        """
    else:
        args = [query]
        where_user = ""
        if user_id is not None:
            args.append(user_id)
            where_user = f"AND user_id = ${len(args)}"
        sql = f"""
            SELECT id, user_id, content, content_type, metadata, created_at,
                   ts_rank(content_tsvector, plainto_tsquery('simple', $1)) AS score
            FROM archival_memory
            WHERE content_tsvector @@ plainto_tsquery('simple', $1) {where_user}
            ORDER BY score DESC
            LIMIT {int(limit)}
        """

    async with pg.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    out: list[ArchivalRow] = []
    for r in rows:
        meta = r["metadata"]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(
            ArchivalRow(
                id=r["id"],
                user_id=r["user_id"],
                content=r["content"],
                content_type=r["content_type"],
                metadata=meta,
                created_at=r["created_at"],
                score=float(r["score"]) if r["score"] is not None else None,
            )
        )
    return out
