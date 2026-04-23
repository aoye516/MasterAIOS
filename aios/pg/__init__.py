"""PostgreSQL bridge for AIOS workspace skills.

Wraps the legacy AIOS schema (see scripts/init_db.sql) and exposes a small
async API used by `aios.cli` and `workspace/skills/pg_archive_search/`.
"""

from aios.pg.client import PgClient, get_dsn
from aios.pg.archival import ArchivalRow, search_archival

__all__ = ["PgClient", "get_dsn", "ArchivalRow", "search_archival"]
