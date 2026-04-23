"""Thin asyncpg wrapper used by AIOS skills."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg


def get_dsn() -> str:
    """Resolve the PostgreSQL DSN.

    Reads `DATABASE_URL`. If it uses the SQLAlchemy `postgresql+asyncpg://` form
    (legacy AIOS convention), strip the `+asyncpg` driver suffix so asyncpg
    accepts it directly.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Source .env or export it before "
            "running AIOS skills (e.g. DATABASE_URL=postgresql://user@localhost/aios)."
        )
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url[len("postgresql+asyncpg://") :]
    return url


class PgClient:
    """Lazy connection pool wrapper.

    Designed for short-lived CLI invocations (one process per skill call).
    Use `async with PgClient() as pg: ...`.
    """

    def __init__(self, dsn: str | None = None, min_size: int = 1, max_size: int = 4):
        self._dsn = dsn or get_dsn()
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def __aenter__(self) -> "PgClient":
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            command_timeout=30,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        if self._pool is None:
            raise RuntimeError("PgClient not entered; use `async with PgClient()`.")
        async with self._pool.acquire() as conn:
            yield conn
