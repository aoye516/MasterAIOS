"""`aios` CLI — single entry-point used by workspace skills.

Usage:
    aios archive-search "<query>" [--user-id N] [--limit K] [--json]
    aios code-helper --task <name> "<description>" [--timeout SEC] [--json]
    aios code-helper --list-tasks
    aios db-ping

The skill files in `workspace/skills/<name>/SKILL.md` teach the LLM to invoke
this CLI through nanobot's built-in `bash` tool.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _load_env() -> None:
    """Load .env from project root if present (no-op when already exported)."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            load_dotenv(candidate, override=False)
            return


async def _cmd_archive_search(args: argparse.Namespace) -> int:
    from aios.pg import PgClient, search_archival

    async with PgClient() as pg:
        rows = await search_archival(
            pg,
            args.query,
            user_id=args.user_id,
            limit=args.limit,
        )
    payload = [r.to_dict() for r in rows]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not payload:
            print("(no archival memory matched)")
            return 0
        for i, row in enumerate(payload, 1):
            score = row.get("score")
            score_str = f"  [score={score:.4f}]" if isinstance(score, (int, float)) else ""
            print(f"#{i} id={row['id']} created={row['created_at']}{score_str}")
            print(f"  {row['content'][:400]}")
            if row.get("metadata"):
                print(f"  metadata: {row['metadata']}")
            print()
    return 0


async def _cmd_code_helper(args: argparse.Namespace) -> int:
    from aios.acp import delegate_to_claude, list_tasks

    if args.list_tasks:
        tasks = list_tasks()
        if args.json:
            print(json.dumps(tasks, ensure_ascii=False))
        else:
            if not tasks:
                print("(no code-helper tasks yet)")
            for t in tasks:
                print(t)
        return 0

    if not args.task or not args.description:
        print("ERROR: --task <name> and a description are required", file=sys.stderr)
        return 2

    result = await delegate_to_claude(
        args.task,
        args.description,
        timeout_s=args.timeout,
    )
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if not result.error else 1

    print(f"task: {result.task}")
    print(f"cwd:  {result.cwd}")
    print(f"resumed: {result.resumed}")
    if result.session_id:
        print(f"session_id: {result.session_id}")
    if result.duration_ms is not None:
        print(f"duration: {result.duration_ms} ms")
    if result.cost_usd is not None:
        print(f"cost: ${result.cost_usd:.4f}")
    if result.tool_calls:
        print("tool calls:")
        for tc in result.tool_calls:
            print(f"  - {tc.get('name')}({list((tc.get('input') or {}).keys())})")
    if result.error:
        print(f"ERROR: {result.error}", file=sys.stderr)
    print()
    print("--- final ---")
    print(result.final_text.strip() or "(empty)")
    return 0 if not result.error else 1


async def _cmd_db_ping(args: argparse.Namespace) -> int:  # noqa: ARG001
    from aios.pg import PgClient

    async with PgClient() as pg:
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT current_database() AS db, version() AS pg, "
                "(SELECT count(*) FROM archival_memory) AS archival_rows"
            )
    print(f"database: {row['db']}")
    print(f"version:  {row['pg'].split(',')[0]}")
    print(f"archival_memory rows: {row['archival_rows']}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aios", description="AIOS workspace skill helper CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_archive = sub.add_parser("archive-search", help="Search archival_memory (vector / tsvector hybrid)")
    p_archive.add_argument("query", help="natural-language query")
    p_archive.add_argument("--user-id", type=int, default=None, help="restrict to a specific user_id")
    p_archive.add_argument("--limit", type=int, default=5)
    p_archive.add_argument("--json", action="store_true", help="emit JSON instead of pretty text")

    p_helper = sub.add_parser("code-helper", help="Delegate a coding task to claude CLI")
    p_helper.add_argument("--task", help="task name (kebab-case, ≤64 chars). Same name = continue session")
    p_helper.add_argument("description", nargs="?", help="task description (one shell-quoted string)")
    p_helper.add_argument("--timeout", type=int, default=None, help="max seconds before cancelling")
    p_helper.add_argument("--list-tasks", action="store_true", help="list known task workspaces and exit")
    p_helper.add_argument("--json", action="store_true")

    sub.add_parser("db-ping", help="Verify PostgreSQL connectivity and show row counts")

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_env()
    parser = _build_parser()
    args = parser.parse_args(argv)

    handlers: dict[str, Any] = {
        "archive-search": _cmd_archive_search,
        "code-helper": _cmd_code_helper,
        "db-ping": _cmd_db_ping,
    }
    handler = handlers[args.cmd]
    return asyncio.run(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
