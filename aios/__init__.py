"""AIOS — Native AI OS, AIOS-specific layer on top of nanobot-ai.

This package contains the AIOS-specific glue:
- `aios.pg`     — asyncpg + pgvector wrapper for archival memory bridge
- `aios.acp`    — subprocess client for delegating to `claude` CLI (stream-json)
- `aios.cli`    — `python -m aios <subcommand>` entry point used by workspace skills
"""

__version__ = "0.2.0"
