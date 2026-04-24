"""`aios` CLI — single entry-point used by workspace skills.

Usage:
    aios archive-search "<query>" [--user-id N] [--limit K] [--embed] [--json]
    aios code-helper --task <name> "<description>" [--timeout SEC] [--json]
    aios code-helper --list-tasks
    aios db-ping

    aios route record    --query "..." --routed-to <agent> [--confidence F]
                         [--spawn-task-id ID] [--spawn-label STR] [--intent-index N]
                         [--user-id N] [--embed] [--json]
    aios route finalize  --trace-id N --outcome success|reroute|failed
                         [--duration-ms N] [--error STR]
    aios route feedback  --task-id ID --feedback thumbs_up|thumbs_down
    aios route examples  <agent> [--top 8] [--recent-days 30]
                         [--min-confidence F] [--seed-fallback] [--json]
    aios route count     <agent>

The skill files in `workspace/skills/<name>/SKILL.md` teach the LLM to invoke
this CLI through nanobot's built-in `bash` tool.

`--embed` runs vector recall (SiliconFlow BAAI/bge-large-zh-v1.5, 1024-d).
Without it, plain tsvector keyword recall is used (limited for Chinese).
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

from aios.mindscape import cli as mindscape_cli
from aios.steward import cli as steward_cli
from aios.toolbox import cli as toolbox_cli
from aios.wellbeing import cli as wellbeing_cli


def _load_env() -> None:
    """Load .env from project root if present (no-op when already exported)."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            load_dotenv(candidate, override=False)
            return


async def _embed_query(query: str) -> list[float]:
    """Backwards-compat shim; new code should use aios.embed.embed_query directly."""
    from aios.embed import embed_query

    return await embed_query(query)


async def _cmd_archive_search(args: argparse.Namespace) -> int:
    from aios.pg import PgClient, search_archival

    embedding = None
    if args.embed:
        embedding = await _embed_query(args.query)

    async with PgClient() as pg:
        rows = await search_archival(
            pg,
            args.query,
            user_id=args.user_id,
            embedding=embedding,
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


async def _cmd_route_record(args: argparse.Namespace) -> int:
    from aios.pg import PgClient
    from aios.route.db import record_trace

    embedding = None
    if args.embed:
        embedding = await _embed_query(args.query)

    async with PgClient() as pg:
        trace_id = await record_trace(
            pg,
            query=args.query,
            routed_to=args.routed_to,
            user_id=args.user_id,
            spawn_label=args.spawn_label,
            spawn_task_id=args.spawn_task_id,
            intent_index=args.intent_index,
            confidence=args.confidence,
            embedding=embedding,
        )
    if args.json:
        print(json.dumps({"trace_id": trace_id}, ensure_ascii=False))
    else:
        print(f"trace_id={trace_id}")
    return 0


async def _cmd_route_finalize(args: argparse.Namespace) -> int:
    from aios.pg import PgClient
    from aios.route.db import finalize_trace

    async with PgClient() as pg:
        ok = await finalize_trace(
            pg,
            trace_id=args.trace_id,
            outcome=args.outcome,
            duration_ms=args.duration_ms,
            error=args.error,
        )
    if not ok:
        print(f"WARN: trace_id={args.trace_id} not found", file=sys.stderr)
        return 1
    print("ok")
    return 0


async def _cmd_route_feedback(args: argparse.Namespace) -> int:
    from aios.pg import PgClient
    from aios.route.db import feedback_by_task

    async with PgClient() as pg:
        n = await feedback_by_task(pg, spawn_task_id=args.task_id, feedback=args.feedback)
    if args.json:
        print(json.dumps({"updated": n}, ensure_ascii=False))
    else:
        print(f"updated {n} trace(s)")
    return 0


async def _cmd_route_examples(args: argparse.Namespace) -> int:
    from aios.pg import PgClient
    from aios.route.db import count_traces, fetch_examples, load_seed_examples

    async with PgClient() as pg:
        examples = await fetch_examples(
            pg,
            agent_name=args.agent,
            top=args.top,
            recent_days=args.recent_days,
            min_confidence=args.min_confidence,
            require_positive_feedback=args.positive_only,
        )
        total = await count_traces(pg, agent_name=args.agent)

    used_seed = False
    if (len(examples) < args.top or total < args.cold_start_threshold) and args.seed_fallback:
        seeds = load_seed_examples(args.agent)
        existing = {e["query"] for e in examples}
        for s in seeds:
            q = s.get("query")
            if q and q not in existing:
                examples.append({
                    "query": q,
                    "confidence": None,
                    "user_feedback": None,
                    "created_at": None,
                    "source": "seed",
                })
                if len(examples) >= args.top:
                    break
        used_seed = True

    if args.json:
        print(json.dumps(
            {"agent": args.agent, "total_traces": total, "used_seed": used_seed,
             "examples": examples},
            ensure_ascii=False, indent=2,
        ))
    else:
        if not examples:
            print(f"(no examples for agent={args.agent})")
            return 0
        for i, ex in enumerate(examples, 1):
            tag = " [seed]" if ex.get("source") == "seed" else ""
            print(f"#{i}{tag} {ex['query']}")
    return 0


async def _cmd_route_count(args: argparse.Namespace) -> int:
    from aios.pg import PgClient
    from aios.route.db import count_traces

    async with PgClient() as pg:
        n = await count_traces(pg, agent_name=args.agent)
    if args.json:
        print(json.dumps({"agent": args.agent, "count": n}, ensure_ascii=False))
    else:
        print(n)
    return 0


async def _cmd_route(args: argparse.Namespace) -> int:
    handlers = {
        "record": _cmd_route_record,
        "finalize": _cmd_route_finalize,
        "feedback": _cmd_route_feedback,
        "examples": _cmd_route_examples,
        "count": _cmd_route_count,
    }
    return await handlers[args.route_cmd](args)


async def _cmd_scaffold_agent(args: argparse.Namespace) -> int:
    from aios.scaffold.agent import scaffold_agent

    result = scaffold_agent(
        args.name,
        domain=args.domain,
        emoji=args.emoji,
        title=args.title,
        description=args.description,
        force=args.force,
    )
    if args.json:
        print(json.dumps({
            "created": [str(p) for p in result.created],
            "skipped": [str(p) for p in result.skipped],
            "next_steps": result.next_steps,
        }, ensure_ascii=False, indent=2))
        return 0

    if result.created:
        print("created:")
        for p in result.created:
            print(f"  + {p}")
    if result.skipped:
        print("skipped (use --force to overwrite):")
        for p in result.skipped:
            print(f"  - {p}")
    print()
    print("next steps:")
    for step in result.next_steps:
        print(f"  {step}")
    return 0


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
    p_archive.add_argument(
        "--embed",
        action="store_true",
        help="vector recall via SiliconFlow embeddings (needed for Chinese)",
    )
    p_archive.add_argument("--json", action="store_true", help="emit JSON instead of pretty text")

    p_helper = sub.add_parser("code-helper", help="Delegate a coding task to claude CLI")
    p_helper.add_argument("--task", help="task name (kebab-case, ≤64 chars). Same name = continue session")
    p_helper.add_argument("description", nargs="?", help="task description (one shell-quoted string)")
    p_helper.add_argument("--timeout", type=int, default=None, help="max seconds before cancelling")
    p_helper.add_argument("--list-tasks", action="store_true", help="list known task workspaces and exit")
    p_helper.add_argument("--json", action="store_true")

    sub.add_parser("db-ping", help="Verify PostgreSQL connectivity and show row counts")

    p_scaf = sub.add_parser("scaffold-agent", help="Generate the four-piece skeleton for a new sub-agent")
    p_scaf.add_argument("name", help="agent name, snake_case (e.g. steward, mindscape)")
    p_scaf.add_argument("--domain", required=True,
                        help="short domain tag (finance / knowledge / wellbeing / tools / contacts)")
    p_scaf.add_argument("--emoji", default="🤖")
    p_scaf.add_argument("--title", default=None, help="display title (defaults to Title Case of name)")
    p_scaf.add_argument("--description", default="TODO: 一两句话写清这个代理负责的领域。",
                        help="one-line domain description for SKILL.md frontmatter")
    p_scaf.add_argument("--force", action="store_true", help="overwrite existing files")
    p_scaf.add_argument("--json", action="store_true")

    p_route = sub.add_parser("route", help="Tier 2 self-evolving routing memory ops")
    route_sub = p_route.add_subparsers(dest="route_cmd", required=True)

    p_rrec = route_sub.add_parser("record", help="Insert a routing trace (outcome=pending)")
    p_rrec.add_argument("--query", required=True)
    p_rrec.add_argument("--routed-to", required=True, help="target agent name (kebab-case)")
    p_rrec.add_argument("--user-id", type=int, default=None)
    p_rrec.add_argument("--spawn-label", default=None)
    p_rrec.add_argument("--spawn-task-id", default=None)
    p_rrec.add_argument("--intent-index", type=int, default=0)
    p_rrec.add_argument("--confidence", type=float, default=None)
    p_rrec.add_argument("--embed", action="store_true",
                        help="embed query (SiliconFlow) and store in query_embedding")
    p_rrec.add_argument("--json", action="store_true")

    p_rfin = route_sub.add_parser("finalize", help="Update outcome of an existing trace")
    p_rfin.add_argument("--trace-id", type=int, required=True)
    p_rfin.add_argument("--outcome", required=True, choices=["success", "reroute", "failed"])
    p_rfin.add_argument("--duration-ms", type=int, default=None)
    p_rfin.add_argument("--error", default=None)

    p_rfb = route_sub.add_parser("feedback", help="Backfill user feedback by spawn_task_id")
    p_rfb.add_argument("--task-id", required=True, dest="task_id")
    p_rfb.add_argument("--feedback", required=True, choices=["thumbs_up", "thumbs_down"])
    p_rfb.add_argument("--json", action="store_true")

    p_rex = route_sub.add_parser("examples", help="Fetch top recent successful traces for one agent")
    p_rex.add_argument("agent", help="agent name (kebab-case)")
    p_rex.add_argument("--top", type=int, default=8)
    p_rex.add_argument("--recent-days", type=int, default=30)
    p_rex.add_argument("--min-confidence", type=float, default=0.5)
    p_rex.add_argument("--positive-only", action="store_true",
                       help="only traces with user_feedback=thumbs_up")
    p_rex.add_argument("--seed-fallback", action="store_true", default=True,
                       help="fill from workspace/agents/<agent>/seed_examples.jsonl when sparse")
    p_rex.add_argument("--no-seed-fallback", action="store_false", dest="seed_fallback")
    p_rex.add_argument("--cold-start-threshold", type=int, default=50,
                       help="if total traces < N, force seed fallback")
    p_rex.add_argument("--json", action="store_true")

    p_rct = route_sub.add_parser("count", help="Count traces routed to an agent")
    p_rct.add_argument("agent")
    p_rct.add_argument("--json", action="store_true")

    steward_cli.add_subparsers(sub)
    mindscape_cli.add_subparsers(sub)
    toolbox_cli.add_subparsers(sub)
    wellbeing_cli.add_subparsers(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_env()
    parser = _build_parser()
    args = parser.parse_args(argv)

    handlers: dict[str, Any] = {
        "archive-search": _cmd_archive_search,
        "code-helper": _cmd_code_helper,
        "db-ping": _cmd_db_ping,
        "route": _cmd_route,
        "scaffold-agent": _cmd_scaffold_agent,
        "steward": steward_cli.dispatch,
        "mind": mindscape_cli.dispatch,
        "toolbox": toolbox_cli.dispatch,
        "wellbeing": wellbeing_cli.dispatch,
    }
    handler = handlers[args.cmd]
    return asyncio.run(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
