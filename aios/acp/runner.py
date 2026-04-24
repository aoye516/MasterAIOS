"""Background watcher process: spawn `claude -p` and stream status to disk.

This module is invoked as `python -m aios.acp.runner <task> <prompt>` by
:func:`aios.acp.client.start_task`. It is intentionally **not** registered
as a top-level `aios` subcommand because it's an internal daemon and
direct human invocation is rarely useful (use `aios code-helper start`
instead).

The watcher's job:

1. Spawn `claude -p` with the same three-layer safety boundary the sync
   wrapper uses (cwd isolation + safety prologue + workspace CLAUDE.md +
   IS_SANDBOX=1 + bypassPermissions).
2. Stream `--output-format stream-json` events from claude's stdout.
3. Mirror each raw event line to ``<cwd>/_run/stdout.jsonl`` so logs
   are recoverable.
4. After every event, atomically rewrite ``<cwd>/_run/status.json`` with
   accumulated counters (tool calls, files written, text chunks, last
   text snippet, last_event_at, elapsed_s, ...).
5. On completion / cancel / fatal error, write ``<cwd>/_run/result.json``
   with the full :class:`CodeHelperResult` payload AND update status to
   one of ``done`` / ``failed`` / ``cancelled``.

The CLI (`aios code-helper start`) just spawns this module detached and
returns the watcher PID immediately, so nanobot's 120s exec cap never
applies to the actual claude process.

Convention: Master should poll via `aios code-helper poll <task>` (which
reads status.json) on a 1-min cron, *not* tail stdout.jsonl directly.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aios.acp.client import (
    DEFAULT_PERMISSION_MODE,
    DEFAULT_TIMEOUT_S,
    DEFAULT_WORKSPACE_ROOT,
    _SAFETY_PROLOGUE,
    _ensure_workspace_claude_md,
    _last_session_id_for_cwd,
    _validate_task_name,
    task_session_path,
)


# ---------------------------------------------------------------------------
# status / result IO
# ---------------------------------------------------------------------------

_STATUS_VERSION = 1


def _run_dir(cwd: Path) -> Path:
    p = cwd / "_run"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write `payload` to `path` atomically (write tmp + rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


@dataclass
class _Status:
    task: str
    status: str = "starting"  # starting | running | done | failed | cancelled
    pid: int | None = None
    cwd: str = ""
    started_at: float = 0.0
    ended_at: float | None = None
    elapsed_s: float = 0.0
    last_event_at: float | None = None
    session_id: str | None = None
    resumed: bool = False

    tool_calls_count: int = 0
    tool_calls_recent: list[dict] = field(default_factory=list)  # last 5
    files_written: list[str] = field(default_factory=list)
    text_chunks_count: int = 0
    final_text_preview: str = ""  # last 200 chars of accumulated assistant text

    needs_confirmation: bool = False
    needs_confirmation_reason: str | None = None

    error: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    version: int = _STATUS_VERSION

    def snapshot(self) -> dict:
        d = asdict(self)
        d["elapsed_s"] = round(time.time() - self.started_at, 2) if self.started_at else 0.0
        return d


def _write_status(status_path: Path, status: _Status) -> None:
    _atomic_write_json(status_path, status.snapshot())


# ---------------------------------------------------------------------------
# event consumption — translate stream-json events into _Status mutations
# ---------------------------------------------------------------------------

_FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _summarize_tool(name: str, input_: dict | None) -> str:
    """Short, log-friendly label for a tool call (e.g. 'Write app.py')."""
    inp = input_ or {}
    if name in _FILE_TOOLS:
        path = inp.get("file_path") or inp.get("notebook_path") or "?"
        return f"{name}: {Path(path).name}"
    if name == "Read":
        return f"Read: {Path(inp.get('file_path', '?')).name}"
    if name == "Bash":
        cmd = inp.get("command", "")
        if isinstance(cmd, str):
            cmd = cmd.replace("\n", " ").strip()
        return f"Bash: {cmd[:80]}"
    if name == "Grep":
        return f"Grep: {inp.get('pattern','?')[:60]}"
    if name == "Glob":
        return f"Glob: {inp.get('pattern','?')[:60]}"
    if name == "WebFetch":
        return f"WebFetch: {inp.get('url','?')[:80]}"
    if name == "WebSearch":
        return f"WebSearch: {inp.get('query','?')[:60]}"
    if name == "TodoWrite":
        n = len(inp.get("todos", []) or [])
        return f"TodoWrite: {n} item(s)"
    return f"{name}({list(inp.keys())[:3]})"


def _looks_like_question(text: str) -> bool:
    """Cheap heuristic: does the latest assistant text read like CC needs input?

    Catches both English and Chinese variants. False positives are okay; Master
    will see the actual text in `final_text_preview` and can judge.
    """
    if not text:
        return False
    tail = text.strip()[-200:]
    cn_markers = ("？", "请告诉我", "请确认", "请问", "需要你", "希望你", "是否需要", "你希望", "想要我")
    en_markers = ("would you like", "shall i", "should i", "do you want",
                  "please confirm", "let me know", "could you tell me")
    return tail.endswith("?") or tail.endswith("？") \
        or any(m in tail for m in cn_markers) \
        or any(m.lower() in tail.lower() for m in en_markers)


def _ingest_event(status: _Status, event: dict, accumulated_text: list[str]) -> None:
    """Mutate `status` in-place based on a single stream-json event."""
    status.last_event_at = time.time()
    etype = event.get("type")

    if etype == "system" and event.get("subtype") == "init":
        status.session_id = event.get("session_id") or status.session_id
        return

    if etype == "assistant":
        msg = event.get("message", {}) or {}
        for block in msg.get("content", []) or []:
            btype = block.get("type")
            if btype == "text":
                t = block.get("text", "")
                if t:
                    status.text_chunks_count += 1
                    accumulated_text.append(t)
                    full = "".join(accumulated_text)
                    status.final_text_preview = full[-300:]
            elif btype == "tool_use":
                name = block.get("name") or "?"
                inp = block.get("input") or {}
                status.tool_calls_count += 1
                summary = _summarize_tool(name, inp)
                ts = time.time()
                status.tool_calls_recent.append({"name": name, "summary": summary, "ts": ts})
                # cap to last 5 to keep poll output small
                if len(status.tool_calls_recent) > 5:
                    status.tool_calls_recent = status.tool_calls_recent[-5:]
                if name in _FILE_TOOLS:
                    fpath = inp.get("file_path") or inp.get("notebook_path")
                    if fpath and fpath not in status.files_written:
                        status.files_written.append(fpath)
        return

    if etype == "result":
        # Final summary event from claude itself
        status.duration_ms = event.get("duration_ms")
        status.cost_usd = event.get("total_cost_usd")
        if event.get("session_id"):
            status.session_id = event.get("session_id")
        # subtype: success | error_max_turns | error_during_execution
        subtype = event.get("subtype") or "success"
        if subtype != "success":
            status.error = event.get("result") or event.get("error") or f"claude {subtype}"
        return


# ---------------------------------------------------------------------------
# main async loop
# ---------------------------------------------------------------------------


async def _run(task: str, prompt: str, *, timeout_s: int) -> int:
    cwd = task_session_path(task)
    _ensure_workspace_claude_md(DEFAULT_WORKSPACE_ROOT)

    run_dir = _run_dir(cwd)
    status_path = run_dir / "status.json"
    stdout_path = run_dir / "stdout.jsonl"
    stderr_path = run_dir / "stderr.log"
    result_path = run_dir / "result.json"
    pidfile = run_dir / "pidfile"

    # Truncate previous-run artifacts (a new --task <same> run overwrites).
    # Keep `result.json` from earlier runs around? No — a new start = a new
    # session, even if it auto-resumes claude's transcript. Old results are
    # stale; clear them to avoid Master reading the wrong final_text.
    for p in (stdout_path, stderr_path, result_path):
        if p.exists():
            p.unlink()

    pidfile.write_text(str(os.getpid()), encoding="utf-8")

    status = _Status(
        task=task,
        status="starting",
        pid=os.getpid(),
        cwd=str(cwd),
        started_at=time.time(),
    )
    _write_status(status_path, status)

    claude_bin = shutil.which("claude")
    if not claude_bin:
        status.status = "failed"
        status.error = "claude CLI not in PATH"
        status.ended_at = time.time()
        _write_status(status_path, status)
        return 2

    prior_session = _last_session_id_for_cwd(cwd)
    status.resumed = bool(prior_session)

    args = [
        claude_bin,
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", DEFAULT_PERMISSION_MODE,
        "--append-system-prompt", _SAFETY_PROLOGUE,
    ]
    if prior_session:
        args.extend(["--resume", prior_session])

    child_env = {**os.environ, "IS_SANDBOX": "1"}

    # Open stderr file to teE claude diagnostics.
    stderr_fp = stderr_path.open("ab")

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr_fp,
        cwd=str(cwd),
        env=child_env,
    )

    status.status = "running"
    _write_status(status_path, status)

    accumulated_text: list[str] = []

    cancelled = {"flag": False}

    def _on_signal(_signum, _frame):
        # Mark cancelled and try to terminate the child; the consumer loop
        # below will see EOF and finalize.
        cancelled["flag"] = True
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    async def _consume() -> None:
        assert proc.stdout is not None
        with stdout_path.open("ab") as raw_fp:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                raw_fp.write(line)
                raw_fp.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _ingest_event(status, event, accumulated_text)
                # opportunistic confirmation flag
                if status.final_text_preview and _looks_like_question(status.final_text_preview):
                    status.needs_confirmation = True
                    status.needs_confirmation_reason = status.final_text_preview[-200:]
                else:
                    status.needs_confirmation = False
                    status.needs_confirmation_reason = None
                _write_status(status_path, status)

    try:
        await asyncio.wait_for(_consume(), timeout=timeout_s)
        return_code = await proc.wait()
        if cancelled["flag"]:
            status.status = "cancelled"
            status.error = status.error or "cancelled by SIGTERM"
        elif return_code != 0 and not status.error:
            status.status = "failed"
            status.error = f"claude exited {return_code}"
        else:
            status.status = "done" if not status.error else "failed"
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
        status.status = "failed"
        status.error = f"runner timeout after {timeout_s}s"
    finally:
        with contextlib.suppress(Exception):
            stderr_fp.close()
        status.ended_at = time.time()
        full_text = "".join(accumulated_text)
        # Persist the canonical result for later `aios code-helper result`.
        result = {
            "task": task,
            "cwd": str(cwd),
            "session_id": status.session_id,
            "resumed": status.resumed,
            "final_text": full_text,
            "tool_calls_count": status.tool_calls_count,
            "tool_calls_recent": status.tool_calls_recent,
            "files_written": status.files_written,
            "duration_ms": status.duration_ms,
            "cost_usd": status.cost_usd,
            "error": status.error,
            "started_at": status.started_at,
            "ended_at": status.ended_at,
        }
        _atomic_write_json(result_path, result)
        _write_status(status_path, status)
        with contextlib.suppress(FileNotFoundError):
            pidfile.unlink()
    return 0 if status.status == "done" else 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m aios.acp.runner",
        description="Background watcher for `aios code-helper start`. "
                    "Not intended for direct human use.",
    )
    p.add_argument("task", help="task name (kebab-case)")
    p.add_argument("prompt", help="full prompt for claude -p")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                   help=f"max seconds before runner kills claude (default {DEFAULT_TIMEOUT_S})")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _validate_task_name(args.task)
    return asyncio.run(_run(args.task, args.prompt, timeout_s=args.timeout))


if __name__ == "__main__":
    sys.exit(main())
