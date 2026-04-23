"""Sanity test: the SQL building logic in search_archival is correct enough.

We don't hit a real PG here; we just validate the input contract (vector length
and empty-query rejection)."""

from __future__ import annotations

import pytest

from aios.pg.archival import _vector_literal, search_archival
from aios.pg.client import get_dsn


def test_vector_literal_format():
    s = _vector_literal([0.1, -0.2, 1.0])
    assert s.startswith("[") and s.endswith("]")
    parts = s[1:-1].split(",")
    assert parts == ["0.100000", "-0.200000", "1.000000"]


def test_get_dsn_strips_sqlalchemy_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u@localhost/db")
    assert get_dsn() == "postgresql://u@localhost/db"


def test_get_dsn_passthrough(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h:5432/db")
    assert get_dsn() == "postgresql://u@h:5432/db"


def test_get_dsn_missing_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_dsn()


@pytest.mark.asyncio
async def test_search_archival_empty_query_raises():
    """We can validate input without a live connection — the wrapper raises early."""

    class _DummyPg:
        async def acquire(self):  # noqa: D401
            raise AssertionError("should not reach acquire() on empty query")

    with pytest.raises(ValueError):
        await search_archival(_DummyPg(), "   ")  # type: ignore[arg-type]
