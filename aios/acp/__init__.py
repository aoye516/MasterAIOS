"""External-agent client for the AIOS `code_helper` skill.

Despite the package name (`acp`), the MVP implementation does **not** speak the
ACP protocol — it spawns the `claude` CLI in `--output-format stream-json` mode,
which gives us:

  - structured per-event NDJSON (system/init, assistant deltas, tool calls, result)
  - native multi-turn continuity via `--continue` / `--resume <session_id>`

This keeps zero external dependencies (no AWS Bedrock, no `claude-as-acp`
wrapper) while preserving the same task-name → session continuity contract that
the OpenClaw `claude-as-acp` skill exposed.
"""

from aios.acp.client import (
    ClaudeCliError,
    CodeHelperResult,
    cancel_task,
    delegate_to_claude,
    list_running_tasks,
    list_tasks,
    list_tasks_with_status,
    start_task,
    task_result,
    task_session_path,
    task_status,
    wait_task,
)

__all__ = [
    "ClaudeCliError",
    "CodeHelperResult",
    "cancel_task",
    "delegate_to_claude",
    "list_running_tasks",
    "list_tasks",
    "list_tasks_with_status",
    "start_task",
    "task_result",
    "task_session_path",
    "task_status",
    "wait_task",
]
