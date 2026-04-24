"""Spawn `claude` CLI as a sub-agent for code-heavy tasks.

Three-layer safety boundary (cheap, nothing to break):

1. **cwd 隔离**：每个 task 的 cwd = `<workspace_root>/<task>/`，物理上跟
   `/claude/aios` 主项目分开。
2. **system prompt 注入**（`--append-system-prompt`）：在 Claude Code 自带
   system prompt 末尾追加一段硬约束，告诉它别越界、别动 AIOS、别 sudo。
3. **workspace CLAUDE.md**：首次调用时在 workspace_root 写一份 CLAUDE.md，
   内容是同一份边界约定。Claude Code 会自动从 cwd 往上找 CLAUDE.md，
   所以所有 task 自动继承同一份约定 —— 跟 #2 互为兜底。

Permission：默认 `--permission-mode bypassPermissions`，让 file edit / Bash
都自动通过（headless 模式下 default 模式会静默拒工具）。配合上面的软约束
够用了。如果想更严，env `AIOS_CC_PERMISSION_MODE=acceptEdits` / `default`
临时降权。

Root 用户特殊：claude 在 root 下默认会禁用 bypassPermissions。设
`IS_SANDBOX=1` 让它放行 —— AIOS 跑在 systemd `User=root` 下，必须这么做。
"""

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
DEFAULT_PERMISSION_MODE = os.environ.get("AIOS_CC_PERMISSION_MODE", "bypassPermissions")
TASK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


# 注入到每个 claude 子进程的 system prompt 末尾。Claude Code 自带的 prompt
# 已经在前面，这里只追加边界约束。改这段时 SKILL.md 的"安全边界"段也要同步。
_SAFETY_PROLOGUE = """\

---

You are running in non-interactive (`-p`) mode as an external sub-agent of AIOS,
a personal AI OS built on nanobot. Your output is consumed by the AIOS Master
agent, not directly by a human.

**Hard boundaries (violating any of these breaks the user's host):**

1. **Stay inside your current working directory.** Don't Read/Write/Edit any
   file outside it. Your cwd is a per-task workspace; everything you produce
   goes here.
2. **Don't touch /claude/aios** — that's the AIOS project itself. Modifying it,
   `cd`-ing into it, or running its scripts (`bash deploy/...`, `aios ...`,
   etc.) corrupts the host. If the user wants AIOS code changed, they will say
   so explicitly and the Master will route to a different mechanism.
3. **No root-level system changes**: never run `sudo`, `systemctl`, `service`,
   `apt`, `dpkg`, `npm install -g`, `pip install` (without `--user`), or
   anything else that needs root to take effect.
4. **Don't touch the AIOS Postgres database** (no `psql`, `pg_dump`, `dropdb`,
   `psycopg2.connect("aios", ...)`, etc.). It belongs to the Master.
5. **Don't `rm -rf` or `git push --force`** anything outside cwd.
6. **Don't read host secrets**: `~/.claude/settings.json`, `~/.aws/`, `/root/.env`,
   `/claude/aios/.env` are off-limits — including `cat`, `grep`, or any tool that
   would surface their contents.

If your task seems to require any of the above, **stop and reply** with what you
would need; do not improvise. The Master will figure out another way.

Inside cwd you have free rein: write source, run tests, build, edit, `git`,
`python3`, `node`, `bash`. Same task name across calls = same cwd = continued
session.
"""


# Workspace-level CLAUDE.md content. 写一次就不再覆盖（用户改了不要丢）。
_WORKSPACE_CLAUDE_MD = """\
# AIOS code-helper workspace contract

You are running as the **external coding sub-agent** of AIOS — a personal AI OS
built on nanobot. AIOS Master orchestrates you via `claude -p` in headless mode.

This file lives at the workspace root. Claude Code auto-discovers `CLAUDE.md`
by walking up from cwd, so every task under this root inherits the same rules.

## Your scope

Your cwd is `/root/aios-cc-workspace/<task-name>/`. Same task name across
multiple invocations = same cwd = continued session (the wrapper auto-resumes
the most recent jsonl transcript for this cwd).

Source files, build artifacts, scratch notes — anything you produce — stays
inside your task folder.

## Boundaries (hard, no exceptions)

- **Don't read/write/edit anything outside your cwd**, except for safe
  read-only inspection of system locations like `/etc/os-release` or
  `which python3`.
- **Don't touch `/claude/aios`** — that's the AIOS project. Reading is okay if
  the user explicitly asks "show me how AIOS does X"; writing/running its
  scripts (`bash deploy/...`, `aios ...`) is **never** okay from this context.
- **No root system changes**: no `sudo`, `systemctl`, `service`, `apt`,
  `dpkg`, `npm install -g`, `pip install` (without `--user`), or anything
  similar.
- **No AIOS database access**: no `psql`, `pg_dump`, `dropdb`, no Python
  `psycopg2.connect("aios", ...)`. The Master owns the database.
- **No `rm -rf` or `git push --force`** against anything outside cwd.
- **No host secrets**: `~/.claude/`, `~/.aws/`, `/root/.env`, `/claude/aios/.env`
  are off-limits.

If a task seems to need any of the above, stop and explain what you would need.
The Master will route to a different mechanism.

## What you can freely do

Inside your cwd:

- write/edit any source code, run `python3`, `node`, `bash`, `pytest`, etc.
- `git init`, clone repos, commit, branch, push to remotes you (the user) own
- install per-project deps via `npm install` (local), `pip install --user`,
  `uv pip install --python ./venv/bin/python`, etc.

## How AIOS reads your work

The wrapper streams your assistant text and tool calls back to Master as JSON.
Master shows the user a folded summary (e.g. "🔧 Write: hello.py") plus your
final reply text — never the raw JSON. Keep your final replies short and
factual; details go in code comments / commit messages, not in chat.
"""


def _ensure_workspace_claude_md(workspace_root: Path) -> None:
    """Idempotent bootstrap of the workspace-level CLAUDE.md.

    Only writes if the file is missing. If the user (or Claude itself) edits
    it later, we leave it alone.
    """
    target = workspace_root / "CLAUDE.md"
    if target.exists():
        return
    workspace_root.mkdir(parents=True, exist_ok=True)
    target.write_text(_WORKSPACE_CLAUDE_MD, encoding="utf-8")


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
    base = workspace_root or DEFAULT_WORKSPACE_ROOT
    _ensure_workspace_claude_md(base)
    cwd = task_session_path(task, workspace_root)
    timeout = timeout_s or DEFAULT_TIMEOUT_S

    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise ClaudeCliError(
            "`claude` CLI not found in PATH. Install via npm: "
            "`npm install -g @anthropic-ai/claude-code`"
        )

    prior_session = _last_session_id_for_cwd(cwd)
    args = [
        claude_bin,
        "-p",
        description,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", DEFAULT_PERMISSION_MODE,
        "--append-system-prompt", _SAFETY_PROLOGUE,
    ]
    if prior_session:
        args.extend(["--resume", prior_session])
    if extra_args:
        args.extend(extra_args)

    # IS_SANDBOX=1 让 root 用户也能用 bypassPermissions —— claude-agent 内部
    # 默认 root 时禁用 bypass，必须显式开 sandbox flag。AIOS 跑在
    # systemd User=root 下绕不过去这步。
    child_env = {**os.environ, "IS_SANDBOX": "1"}

    result = CodeHelperResult(task=task, cwd=str(cwd), resumed=bool(prior_session))

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=child_env,
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
