"""`aios` CLI — single entry-point used by workspace skills.

Usage:
    aios archive-search "<query>" [--user-id N] [--limit K] [--embed] [--json]
    aios code-helper start  <task> "<prompt>" [--timeout SEC] [--json]
    aios code-helper status <task> [--json]
    aios code-helper poll   <task> [--json]    # friendly progress / [DONE] / [FAILED] / [NEEDS_CONFIRMATION]
    aios code-helper wait   <task> [--timeout SEC] [--json]
    aios code-helper cancel <task>
    aios code-helper logs   <task> [--tail N]
    aios code-helper result <task> [--json]
    aios code-helper list   [--running] [--json]
    # Sync (legacy, may hit nanobot 120s exec cap):
    aios code-helper run    <task> "<prompt>" [--timeout SEC] [--json]
    aios code-helper --task <name> "<description>" [--timeout SEC] [--json]   # alias of `run`
    aios code-helper --list-tasks                                              # alias of `list`
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


def _format_age(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "?"
    delta = (now or __import__("time").time()) - ts
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    return f"{int(delta // 3600)}h ago"


def _poll_snapshot(status: dict) -> dict:
    """Snapshot of the fields we use to decide if anything 'meaningful' changed."""
    import hashlib
    files = status.get("files_written") or []
    text = (status.get("final_text_preview") or "").strip()
    tool_recent = status.get("tool_calls_recent") or []
    tool_kinds = sorted({(tc.get("name") or "?") for tc in tool_recent})
    return {
        "status": status.get("status"),
        "files_count": len(files),
        "tool_calls_count": status.get("tool_calls_count") or 0,
        "tool_kinds": tool_kinds,
        "text_hash": hashlib.sha1(text.encode("utf-8")).hexdigest() if text else "",
        "elapsed_s": status.get("elapsed_s") or 0,
    }


def _has_meaningful_progress(prev: dict | None, curr: dict) -> tuple[bool, str]:
    """Return (is_meaningful, reason)."""
    if prev is None:
        return True, "first poll"
    if prev.get("status") != curr.get("status"):
        return True, f"status {prev.get('status')} → {curr.get('status')}"
    if curr["files_count"] > prev["files_count"]:
        return True, f"+{curr['files_count'] - prev['files_count']} 文件"
    if curr["tool_calls_count"] > prev["tool_calls_count"]:
        new_kinds = set(curr["tool_kinds"]) - set(prev.get("tool_kinds", []))
        if new_kinds:
            return True, f"new tool kinds: {','.join(sorted(new_kinds))}"
    if curr["text_hash"] and curr["text_hash"] != prev.get("text_hash"):
        return True, "CC 反馈有更新"
    # 5-minute heartbeat: 0-5 sleep, then notify at 5/10/15/20/25/30...
    prev_bucket = int((prev.get("elapsed_s") or 0) // 300)
    curr_bucket = int((curr.get("elapsed_s") or 0) // 300)
    if curr_bucket > prev_bucket and curr_bucket > 0:
        return True, f"心跳：跑了 {curr_bucket * 5} 分钟"
    return False, "no meaningful change"


def _render_poll(
    status: dict | None,
    result: dict | None,
    *,
    last_snapshot: dict | None = None,
) -> tuple[str, str]:
    """Friendly progress / final-state summary for `aios code-helper poll`.

    Returns (rendered_text, marker) where marker is one of:
    DONE / FAILED / CANCELLED / NEEDS_CONFIRMATION / PROGRESS / QUIET / UNKNOWN.

    The Master Agent reads the rendered text verbatim and forwards it to the user
    only when marker != QUIET. The CLI does the diff itself; Master no longer has
    to (unreliably) judge "is there meaningful progress".
    """
    import time as _t
    if status is None:
        return "❓ [UNKNOWN] task 不存在或还没启动 (no _run/status.json)", "UNKNOWN"

    task = status.get("task", "?")
    state = status.get("status", "?")
    elapsed = status.get("elapsed_s") or 0
    files = status.get("files_written") or []
    tool_n = status.get("tool_calls_count") or 0
    tool_recent = status.get("tool_calls_recent") or []
    text_preview = (status.get("final_text_preview") or "").strip()
    needs = status.get("needs_confirmation")
    error = status.get("error")
    cost = status.get("cost_usd")
    duration_ms = status.get("duration_ms")
    resumed = status.get("resumed")

    # Marker line — Master scans for these to drive cron lifecycle.
    if state == "done":
        marker = "DONE"
        marker_line = "✅ [DONE]"
    elif state == "failed":
        marker = "FAILED"
        marker_line = "❌ [FAILED]"
    elif state == "cancelled":
        marker = "CANCELLED"
        marker_line = "⛔ [CANCELLED]"
    elif needs:
        marker = "NEEDS_CONFIRMATION"
        marker_line = "❓ [NEEDS_CONFIRMATION]"
    else:
        # Running — diff against last snapshot to decide PROGRESS vs QUIET.
        curr_snap = _poll_snapshot(status)
        is_progress, reason = _has_meaningful_progress(last_snapshot, curr_snap)
        if is_progress:
            marker = "PROGRESS"
            marker_line = f"🔄 [PROGRESS] ({reason})"
        else:
            # Compact one-line QUIET output — Master sees this and stays silent.
            return (
                f"🤫 [QUIET] task={task}  elapsed={int(elapsed)}s  "
                f"files={len(files)}  tools={tool_n}  "
                f"(no meaningful change since last poll — DO NOT notify the user)",
                "QUIET",
            )

    lines = [f"{marker_line} task={task}  status={state}  elapsed={int(elapsed)}s"
             + (f"  resumed={resumed}" if resumed else "")]

    if cost is not None or duration_ms is not None:
        bits = []
        if duration_ms is not None:
            bits.append(f"duration={duration_ms}ms")
        if cost is not None:
            bits.append(f"cost=${cost:.4f}")
        lines.append("   " + "  ".join(bits))

    if files:
        shown = ", ".join(__import__("os").path.basename(f) for f in files[:8])
        more = f" (+{len(files)-8} more)" if len(files) > 8 else ""
        lines.append(f"📁 已写文件 ({len(files)}): {shown}{more}")

    if tool_n:
        lines.append(f"🔧 工具调用 {tool_n} 次")
        for tc in tool_recent[-3:]:
            age = _format_age(tc.get("ts"))
            lines.append(f"   · {tc.get('summary','?')}  ({age})")

    if text_preview:
        lines.append("💬 CC 最新反馈:")
        for ln in text_preview.splitlines()[-6:]:
            lines.append(f"   {ln.rstrip()}")

    if needs:
        reason = (status.get("needs_confirmation_reason") or "").strip()
        lines.append("⚠️  CC 在等你确认 — 把上面问题转给用户，等用户回复后用 "
                     f"`aios code-helper start {task} \"<用户的回复>\"` 续接")
        if reason and reason not in text_preview:
            lines.append(f"   {reason[:200]}")

    if error:
        lines.append(f"⚠️  error: {error}")

    if state == "done":
        lines.append(f"📎 CC task: {task}  (续接同一名字即可继续)")
        if result:
            ft = (result.get("final_text") or "").strip()
            if ft and len(ft) > len(text_preview):
                lines.append("--- final ---")
                lines.append(ft[:1200])
                if len(ft) > 1200:
                    lines.append(f"... (+{len(ft)-1200} chars, see _run/result.json)")

    return "\n".join(lines), marker


async def _cmd_code_helper(args: argparse.Namespace) -> int:
    from aios.acp import (
        cancel_task,
        delegate_to_claude,
        list_running_tasks,
        list_tasks_with_status,
        start_task,
        task_result,
        task_status,
        wait_task,
    )

    sub = getattr(args, "helper_cmd", None)

    # ---- legacy flags fall through to `run` / `list` ----
    if sub is None:
        if getattr(args, "list_tasks", False):
            sub = "list"
        else:
            if not getattr(args, "task", None) or not getattr(args, "description", None):
                print("ERROR: must use a subcommand (start/status/poll/wait/...) or "
                      "legacy `--task NAME \"desc\"` form. See `aios code-helper -h`.",
                      file=sys.stderr)
                return 2
            sub = "run"

    # ---- start: spawn detached watcher, return immediately ----
    if sub == "start":
        try:
            info = start_task(args.task, args.prompt, timeout_s=args.timeout)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"📤 派给 Claude Code 处理 task={info['task']} pid={info['pid']}")
            print(f"   cwd: {info['cwd']}")
            print(f"   超时上限: {info['timeout_s']}s")
            print(f"   poll: aios code-helper poll {info['task']}")
            print(f"   logs: aios code-helper logs {info['task']} --tail 30")
        return 0

    # ---- status: raw status.json ----
    if sub == "status":
        s = task_status(args.task)
        if s is None:
            print(f"(no status for task={args.task})", file=sys.stderr)
            return 1
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0

    # ---- poll: friendly progress + DONE/FAILED/CANCELLED/NEEDS_CONFIRMATION/PROGRESS/QUIET ----
    if sub == "poll":
        s = task_status(args.task)
        r = task_result(args.task) if (s and s.get("status") == "done") else None
        # Read previous snapshot for diff (if any), then render, then save new snapshot.
        run_dir = Path.home() / "aios-cc-workspace" / args.task / "_run"
        last_snap_path = run_dir / "last_poll.json"
        last_snap = None
        if last_snap_path.exists():
            try:
                last_snap = json.loads(last_snap_path.read_text(encoding="utf-8"))
            except Exception:
                last_snap = None
        text, marker = _render_poll(s, r, last_snapshot=last_snap)
        # Always update snapshot so the next poll diffs against the fresh baseline.
        if s is not None and run_dir.exists():
            try:
                last_snap_path.write_text(
                    json.dumps(_poll_snapshot(s), ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                pass
        if args.json:
            print(json.dumps({"status": s, "result": r, "rendered": text, "marker": marker},
                             ensure_ascii=False, indent=2))
        else:
            print(text)
        # exit code: 0 terminal, 2 running (PROGRESS or QUIET), 1 missing
        if s is None:
            return 1
        return 0 if s.get("status") in ("done", "failed", "cancelled") else 2

    # ---- wait: block up to --timeout seconds for terminal state ----
    if sub == "wait":
        s = await wait_task(args.task, timeout_s=args.timeout)
        r = task_result(args.task) if (s and s.get("status") == "done") else None
        if args.json:
            print(json.dumps({"status": s, "result": r}, ensure_ascii=False, indent=2))
        else:
            text, _marker = _render_poll(s, r)
            print(text)
        return 0 if s and s.get("status") == "done" else 1

    # ---- cancel: SIGTERM the watcher ----
    if sub == "cancel":
        ok = cancel_task(args.task)
        print("ok" if ok else "(no running task to cancel)")
        return 0 if ok else 1

    # ---- logs: tail stdout.jsonl (raw stream-json) ----
    if sub == "logs":
        run_dir = Path.home() / "aios-cc-workspace" / args.task / "_run"
        path = run_dir / "stdout.jsonl"
        if not path.exists():
            print(f"(no logs at {path})", file=sys.stderr)
            return 1
        lines = path.read_text(encoding="utf-8").splitlines()
        for ln in lines[-args.tail:]:
            print(ln)
        return 0

    # ---- result: dump result.json ----
    if sub == "result":
        r = task_result(args.task)
        if r is None:
            print(f"(no result for task={args.task} — still running or never finished)",
                  file=sys.stderr)
            return 1
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    # ---- list: every task workspace + status (or only running) ----
    if sub == "list":
        if getattr(args, "running", False):
            tasks = list_running_tasks()
            if args.json:
                print(json.dumps(tasks, ensure_ascii=False, indent=2))
            else:
                if not tasks:
                    print("(no running code-helper tasks)")
                for t in tasks:
                    print(f"  🔄 {t.get('task')}  pid={t.get('pid')}  "
                          f"elapsed={int(t.get('elapsed_s') or 0)}s")
            return 0
        rows = list_tasks_with_status()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            if not rows:
                print("(no code-helper tasks yet)")
            for row in rows:
                s = row.get("status") or {}
                state = s.get("status", "—")
                elapsed = s.get("elapsed_s")
                tail = f"  elapsed={int(elapsed)}s" if elapsed else ""
                print(f"  {row['task']:30s}  {state}{tail}")
        return 0

    # ---- run: legacy synchronous (start + wait_long + render) ----
    if sub == "run":
        result = await delegate_to_claude(
            args.task,
            args.description if hasattr(args, "description") else args.prompt,
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

    print(f"ERROR: unknown subcommand {sub!r}", file=sys.stderr)
    return 2


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

    p_helper = sub.add_parser(
        "code-helper",
        help="Delegate a coding task to claude CLI (background workflow recommended)",
        description="Recommended workflow: `start` to spawn detached, then poll on a "
                    "1-min cron until you see [DONE] / [FAILED] / [NEEDS_CONFIRMATION]. "
                    "Use `run` for a synchronous (blocking) call — but it will be killed "
                    "by nanobot's 120s exec cap on long tasks.",
    )

    helper_sub = p_helper.add_subparsers(dest="helper_cmd", required=True,
                                         metavar="{start,status,poll,wait,cancel,logs,result,list,run}")

    p_h_start = helper_sub.add_parser("start", help="spawn a detached watcher and return immediately")
    p_h_start.add_argument("task", help="task name (kebab-case, ≤64 chars). Same name = continue session")
    p_h_start.add_argument("prompt", help="full prompt for claude -p (shell-quoted)")
    p_h_start.add_argument("--timeout", type=int, default=None,
                           help="hard ceiling for the runner (default 1800s)")
    p_h_start.add_argument("--json", action="store_true")

    p_h_status = helper_sub.add_parser("status", help="dump _run/status.json (raw)")
    p_h_status.add_argument("task")
    p_h_status.add_argument("--json", action="store_true")

    p_h_poll = helper_sub.add_parser(
        "poll",
        help="friendly progress + [DONE]/[FAILED]/[NEEDS_CONFIRMATION] marker for cron",
    )
    p_h_poll.add_argument("task")
    p_h_poll.add_argument("--json", action="store_true")

    p_h_wait = helper_sub.add_parser("wait", help="block until terminal state or --timeout")
    p_h_wait.add_argument("task")
    p_h_wait.add_argument("--timeout", type=float, default=60.0)
    p_h_wait.add_argument("--json", action="store_true")

    p_h_cancel = helper_sub.add_parser("cancel", help="SIGTERM the watcher")
    p_h_cancel.add_argument("task")

    p_h_logs = helper_sub.add_parser("logs", help="tail _run/stdout.jsonl (raw stream-json)")
    p_h_logs.add_argument("task")
    p_h_logs.add_argument("--tail", type=int, default=50)

    p_h_result = helper_sub.add_parser("result", help="dump _run/result.json")
    p_h_result.add_argument("task")
    p_h_result.add_argument("--json", action="store_true")

    p_h_list = helper_sub.add_parser("list", help="list all known tasks (or only running)")
    p_h_list.add_argument("--running", action="store_true", help="only show running/starting tasks")
    p_h_list.add_argument("--json", action="store_true")

    p_h_run = helper_sub.add_parser(
        "run",
        help="(legacy sync) start + block until done; capped by nanobot 120s exec",
    )
    p_h_run.add_argument("task")
    p_h_run.add_argument("prompt")
    p_h_run.add_argument("--timeout", type=int, default=None)
    p_h_run.add_argument("--json", action="store_true")

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


def _rewrite_legacy_helper_argv(argv: list[str] | None) -> list[str] | None:
    """Translate legacy `aios code-helper [--task X] [<desc>] [--list-tasks]` to the new sub form.

    Old usage we still want to honor:
      aios code-helper --task pomodoro "build a pomodoro app"
      aios code-helper --task pomodoro "..." --json --timeout 600
      aios code-helper --list-tasks
      aios code-helper --list-tasks --json

    Anything else (already using `start` / `status` / ... / `run`) is passed through.
    """
    if not argv or argv[0] != "code-helper":
        return argv
    rest = argv[1:]
    new_subs = {"start", "status", "poll", "wait", "cancel", "logs", "result", "list", "run", "-h", "--help"}
    if rest and rest[0] in new_subs:
        return argv

    # legacy: scan for --task X / --list-tasks / first positional
    task = None
    description = None
    timeout = None
    json_flag = False
    list_flag = False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--task" and i + 1 < len(rest):
            task = rest[i + 1]; i += 2; continue
        if a.startswith("--task="):
            task = a.split("=", 1)[1]; i += 1; continue
        if a == "--list-tasks":
            list_flag = True; i += 1; continue
        if a == "--timeout" and i + 1 < len(rest):
            timeout = rest[i + 1]; i += 2; continue
        if a.startswith("--timeout="):
            timeout = a.split("=", 1)[1]; i += 1; continue
        if a == "--json":
            json_flag = True; i += 1; continue
        if not a.startswith("-") and description is None:
            description = a; i += 1; continue
        # unknown / leftover — bail out, let argparse complain
        return argv

    if list_flag:
        new = ["code-helper", "list"]
        if json_flag:
            new.append("--json")
        return new

    if task and description:
        new = ["code-helper", "run", task, description]
        if timeout:
            new.extend(["--timeout", timeout])
        if json_flag:
            new.append("--json")
        return new

    return argv


def main(argv: list[str] | None = None) -> int:
    _load_env()
    if argv is None:
        argv = sys.argv[1:]
    argv = _rewrite_legacy_helper_argv(argv)
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
