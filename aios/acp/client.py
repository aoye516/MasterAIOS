"""Spawn `claude` CLI as a sub-agent for code-heavy tasks."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

DEFAULT_WORKSPACE_ROOT = Path(os.environ.get("AIOS_CC_WORKSPACE_ROOT", str(Path.home() / "aios-cc-workspace")))
DEFAULT_TIMEOUT_S = int(os.environ.get("AIOS_CC_TIMEOUT_S", "1800"))  # 30 min default
TASK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ClaudeCliError(RuntimeError):
    """Raised when the `claude` CLI is unavailable or returns failure."""


@dataclass
class CodeHelperResult:
    task: str
    cwd: str
    session_id: str | None = None
    final_text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    raw_events: list[dict] = field(default_factory=list)
    duration_ms: int | None = None
    cost_usd: float | None = None
    error: str | None = None
    resumed: bool = False

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "cwd": self.cwd,
            "session_id": self.session_id,
            "final_text": self.final_text,
            "tool_calls": self.tool_calls,
            "duration_ms": self.duration_ms,
            "cost_usd": self.cost_usd,
            "error": self.error,
            "resumed": self.resumed,
        }


def _validate_task_name(name: str) -> None:
    if not TASK_NAME_RE.match(name):
        raise ValueError(
            f"invalid task name {name!r}: must match {TASK_NAME_RE.pattern} "
            "(lowercase letters, digits, hyphens; cannot start with hyphen; max 64 chars)"
        )


def task_session_path(task: str, root: Path | None = None) -> Path:
    """Return the workspace directory for `task`, creating it if missing."""
    _validate_task_name(task)
    base = root or DEFAULT_WORKSPACE_ROOT
    cwd = base / task
    cwd.mkdir(parents=True, exist_ok=True)
    return cwd


def list_tasks(root: Path | None = None) -> list[str]:
    base = root or DEFAULT_WORKSPACE_ROOT
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def _last_session_id_for_cwd(cwd: Path) -> str | None:
    """Detect a previous claude session associated with this workspace.

    Claude CLI persists session transcripts under
    `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. We pick the most
    recently modified one.
    """
    encoded = "-" + str(cwd).replace("/", "-").lstrip("-")
    proj_root = Path.home() / ".claude" / "projects" / encoded
    if not proj_root.exists():
        return None
    candidates = sorted(proj_root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    return candidates[0].stem


async def _stream_events(proc: asyncio.subprocess.Process) -> AsyncIterator[dict]:
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


async def delegate_to_claude(
    task: str,
    description: str,
    *,
    workspace_root: Path | None = None,
    timeout_s: int | None = None,
    extra_args: list[str] | None = None,
) -> CodeHelperResult:
    """Spawn `claude -p` against a task workspace and collect a structured result.

    - `task` selects (and creates) `<workspace_root>/<task>/` as cwd.
    - If a prior claude session exists for that cwd, continue it (`--continue`).
    - Streams events; aggregates final text + tool-call summary + cost/duration.
    """
    _validate_task_name(task)
    cwd = task_session_path(task, workspace_root)
    timeout = timeout_s or DEFAULT_TIMEOUT_S

    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise ClaudeCliError(
            "`claude` CLI not found in PATH. Install via npm: "
            "`npm install -g @anthropic-ai/claude-code`"
        )

    prior_session = _last_session_id_for_cwd(cwd)
    args = [claude_bin, "-p", description, "--output-format", "stream-json", "--verbose"]
    if prior_session:
        args.extend(["--resume", prior_session])
    if extra_args:
        args.extend(extra_args)

    result = CodeHelperResult(task=task, cwd=str(cwd), resumed=bool(prior_session))

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
    )

    try:
        async def _consume() -> None:
            async for event in _stream_events(proc):
                result.raw_events.append(event)
                etype = event.get("type")
                if etype == "system" and event.get("subtype") == "init":
                    result.session_id = event.get("session_id")
                elif etype == "assistant":
                    msg = event.get("message", {})
                    for block in msg.get("content", []) or []:
                        if block.get("type") == "text":
                            result.final_text += block.get("text", "")
                        elif block.get("type") == "tool_use":
                            result.tool_calls.append(
                                {
                                    "name": block.get("name"),
                                    "id": block.get("id"),
                                    "input": block.get("input"),
                                }
                            )
                elif etype == "result":
                    result.duration_ms = event.get("duration_ms")
                    result.cost_usd = event.get("total_cost_usd")
                    if event.get("subtype") != "success":
                        result.error = event.get("result") or event.get("error") or "unknown failure"
                    if event.get("session_id"):
                        result.session_id = event.get("session_id")

        await asyncio.wait_for(_consume(), timeout=timeout)
        return_code = await proc.wait()
        if return_code != 0 and not result.error:
            stderr = (await proc.stderr.read()).decode("utf-8", errors="replace") if proc.stderr else ""
            result.error = f"claude exited {return_code}: {stderr.strip()[:500]}"
        return result
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
        result.error = f"timeout after {timeout}s"
        return result
