"""One-shot backfill: v0.x `conversations` → v1.0 `archival_memory`.

Reads every row in `public.conversations`, formats it as a single chat turn
chunk, embeds it with SiliconFlow's BAAI/bge-large-zh-v1.5 (1024-d), and
inserts it into `archival_memory` so the new fractal Master can recall the
v0.x history via `aios archive-search`.

Idempotent: skips conversations whose id already appears in
`archival_memory.metadata->>'source_conv_id'`.

Run on the server:

    cd /claude/aios && source .venv/bin/activate
    set -a && . .env && set +a
    python scripts/backfill_legacy_conversations.py

Required env: DATABASE_URL, SILICONFLOW_API_KEY (or SILICONFLOW_BASE_URL).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import aiohttp

# Re-use the shared asyncpg pool wrapper.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aios.pg import PgClient  # noqa: E402

SF_BASE = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
SF_KEY = os.environ.get("SILICONFLOW_API_KEY")
EMBED_MODEL = os.environ.get("LLM_MODEL_EMBEDDING", "BAAI/bge-large-zh-v1.5")
EMBED_DIM = 1024
BATCH = 16
CONTENT_TYPE = "legacy_conversation"


def render_chunk(row: dict) -> str:
    ts = row["created_at"].strftime("%Y-%m-%d %H:%M")
    user = (row["user_message"] or "").strip()
    bot = (row["assistant_message"] or "").strip()
    return f"[{ts}] 敖烨: {user}\n小丙: {bot}"


async def embed_batch(session: aiohttp.ClientSession, texts: list[str]) -> list[list[float]]:
    if not SF_KEY:
        raise RuntimeError("SILICONFLOW_API_KEY not set")
    payload = {"model": EMBED_MODEL, "input": texts}
    async with session.post(
        f"{SF_BASE}/embeddings",
        json=payload,
        headers={"Authorization": f"Bearer {SF_KEY}"},
        timeout=aiohttp.ClientTimeout(total=60),
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"embeddings failed {resp.status}: {body[:200]}")
        data = await resp.json()
    out = [item["embedding"] for item in data["data"]]
    for v in out:
        if len(v) != EMBED_DIM:
            raise RuntimeError(f"unexpected embed dim {len(v)} != {EMBED_DIM}")
    return out


def vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


async def main() -> int:
    async with PgClient() as pg:
        async with pg.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, user_id, user_message, assistant_message, created_at "
                "FROM conversations ORDER BY created_at ASC"
            )
            done_rows = await conn.fetch(
                "SELECT (metadata->>'source_conv_id')::int AS sid "
                "FROM archival_memory "
                "WHERE content_type=$1 AND metadata ? 'source_conv_id'",
                CONTENT_TYPE,
            )
            done = {r["sid"] for r in done_rows if r["sid"] is not None}

            todo = [r for r in rows if r["id"] not in done]
            print(
                f"conversations total={len(rows)}  already_backfilled={len(done)}  "
                f"to_backfill={len(todo)}"
            )
            if not todo:
                return 0

            async with aiohttp.ClientSession() as session:
                for i in range(0, len(todo), BATCH):
                    batch = todo[i : i + BATCH]
                    chunks = [render_chunk(dict(r)) for r in batch]
                    embeds = await embed_batch(session, chunks)
                    async with conn.transaction():
                        for row, content, vec in zip(batch, chunks, embeds):
                            meta: dict[str, Any] = {
                                "source": "v0.x conversations",
                                "source_conv_id": row["id"],
                                "source_created_at": row["created_at"].isoformat(),
                            }
                            await conn.execute(
                                "INSERT INTO archival_memory "
                                "(user_id, content, content_type, metadata, embedding, created_at) "
                                "VALUES ($1,$2,$3,$4::jsonb,$5::vector,$6)",
                                row["user_id"],
                                content,
                                CONTENT_TYPE,
                                json.dumps(meta, ensure_ascii=False),
                                vec_literal(vec),
                                row["created_at"],
                            )
                    print(f"  inserted {i + len(batch)}/{len(todo)}")

            after = await conn.fetchval(
                "SELECT count(*) FROM archival_memory WHERE content_type=$1",
                CONTENT_TYPE,
            )
            print(f"done. archival_memory({CONTENT_TYPE}) total = {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
