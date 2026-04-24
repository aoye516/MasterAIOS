"""Generate the four-piece skeleton for a new AIOS sub-agent.

Files created (all skipped if already exist, unless --force):
    workspace/skills/<name>/SKILL.md
    workspace/agents/<name>/seed_examples.jsonl
    workspace/agents/<name>/routing_eval.jsonl
    aios/<name>/__init__.py
    aios/<name>/cli.py
    aios/db/migrations/NNNN-<name>.sql

Plus a hint to register the CLI subparser in aios/cli.py manually
(scaffold doesn't auto-edit cli.py to keep diffs reviewable).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _slug_ok(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]*", name))


@dataclass
class ScaffoldResult:
    created: list[Path]
    skipped: list[Path]
    next_steps: list[str]


SKILL_TPL = """\
---
name: {name}
description: |
  {description}
  典型场景由运行时从 routing_traces 拼接注入。
metadata:
  nanobot:
    emoji: "{emoji}"
    requires:
      bins: ["aios"]
domain: {domain}
---

# {title}

## 领域定义

> 这一段是路由的 **核心信号**。写清楚"我负责什么 / 不负责什么"，让 Master LLM 一眼能区分。

- 负责：（写 3-5 条具体场景）
- 不负责：（写 2-3 条边界，避免和邻居 agent 抢活）

## Spawn Task 模板

> Master 用 spawn 调用本子代理时，task 文本应包含本段（占位符 {{USER_QUERY}} 由 Master 替换）。

```
你是 AIOS {title} 子代理，专注于「{domain}」域。当前任务：

{{USER_QUERY}}

你可以也只可以使用以下 CLI（通过 bash 工具调用）：
- aios {name} <subcmd> ...     # 见下方 "CLI 一览"

完成后用一句中文向 Master 汇报结果（含关键数字 / id），不要复述用户原话。
```

## CLI 一览

- `aios {name} ping` — 连通性自检（脚手架默认提供）
- `aios {name} <add/list/...>` — 业务子命令（待补）

强约定：所有写库 / 调外部 API 必须走 CLI，subagent 不直连 PG。

## Few-shot 示例

{{ROUTING_EXAMPLES}}
"""

CLI_TPL = '''\
"""CLI sub-commands for the {name} agent. Wired into aios/cli.py top-level parser."""

from __future__ import annotations

import argparse
import json


async def cmd_ping(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Smoke: confirm the {name} agent CLI is wired up."""
    payload = {{"agent": "{name}", "status": "ok"}}
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"{{payload['agent']}}: {{payload['status']}}")
    return 0


def add_subparsers(parent_sub: argparse._SubParsersAction) -> None:
    """Called from aios/cli.py to register `aios {name} ...` subcommands."""
    p_root = parent_sub.add_parser("{name}", help="{title} agent CLI")
    sub = p_root.add_subparsers(dest="{name}_cmd", required=True)

    p_ping = sub.add_parser("ping", help="connectivity self-check")
    p_ping.add_argument("--json", action="store_true")


HANDLERS = {{
    "ping": cmd_ping,
}}


async def dispatch(args: argparse.Namespace) -> int:
    """Top-level dispatch for `aios {name}`. Called from aios/cli.py."""
    cmd = getattr(args, "{name}_cmd")
    return await HANDLERS[cmd](args)
'''

INIT_TPL = '"""{title} sub-agent — see workspace/skills/{name}/SKILL.md."""\n'

SQL_TPL = """\
-- Migration: {migration_id}-{name}
-- Purpose: {title} sub-agent schema
-- Idempotent: re-running has no side effects.

-- Example: replace with your real tables.
-- CREATE TABLE IF NOT EXISTS {name}_items (
--     id          BIGSERIAL PRIMARY KEY,
--     user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
--     ...
--     created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- );
"""

SEED_TPL = """\
{{"query": "TODO: 一条典型用户原话", "expected": "{name}", "note": "代表场景 1"}}
{{"query": "TODO: 另一条典型用户原话", "expected": "{name}", "note": "代表场景 2"}}
"""

EVAL_TPL = """\
{{"query": "TODO: eval 集第 1 条", "expected": "{name}"}}
{{"query": "TODO: eval 集第 2 条", "expected": "{name}"}}
"""


def _next_migration_id(migrations_dir: Path) -> str:
    nums: list[int] = []
    if migrations_dir.exists():
        for f in migrations_dir.glob("*.sql"):
            m = re.match(r"^(\d+)-", f.name)
            if m:
                nums.append(int(m.group(1)))
    nxt = (max(nums) if nums else 0) + 1
    return f"{nxt:04d}"


def scaffold_agent(
    name: str,
    *,
    domain: str,
    emoji: str = "🤖",
    title: str | None = None,
    description: str = "TODO: 一两句话写清这个代理负责的领域。",
    force: bool = False,
    repo_root: Path | None = None,
) -> ScaffoldResult:
    if not _slug_ok(name):
        raise ValueError(
            f"agent name must match [a-z][a-z0-9_]*, got {name!r} "
            "(use snake_case for python module compatibility)"
        )

    root = repo_root or REPO_ROOT
    title = title or name.replace("_", " ").title()

    skill_dir = root / "workspace" / "skills" / name
    agent_data_dir = root / "workspace" / "agents" / name
    pkg_dir = root / "aios" / name
    migrations_dir = root / "aios" / "db" / "migrations"
    migration_id = _next_migration_id(migrations_dir)

    targets: dict[Path, str] = {
        skill_dir / "SKILL.md": SKILL_TPL.format(
            name=name, title=title, emoji=emoji, domain=domain, description=description
        ),
        agent_data_dir / "seed_examples.jsonl": SEED_TPL.format(name=name),
        agent_data_dir / "routing_eval.jsonl": EVAL_TPL.format(name=name),
        pkg_dir / "__init__.py": INIT_TPL.format(name=name, title=title),
        pkg_dir / "cli.py": CLI_TPL.format(name=name, title=title),
        migrations_dir / f"{migration_id}-{name}.sql": SQL_TPL.format(
            migration_id=migration_id, name=name, title=title
        ),
    }

    created: list[Path] = []
    skipped: list[Path] = []
    for path, content in targets.items():
        if path.exists() and not force:
            skipped.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(path)

    next_steps = [
        f"1. 编辑 {skill_dir / 'SKILL.md'} 写清「领域定义」和「Spawn Task 模板」",
        f"2. 填 {agent_data_dir / 'seed_examples.jsonl'}（≥10 条）和 routing_eval.jsonl（≥30 条）",
        f"3. 设计表结构，写 {migrations_dir / f'{migration_id}-{name}.sql'}",
        f"4. 在 {pkg_dir / 'cli.py'} 加业务子命令",
        f"5. 在 aios/cli.py 顶部加：`from aios.{name} import cli as {name}_cli`",
        f"   并在 _build_parser() 末尾调：`{name}_cli.add_subparsers(sub)`",
        f"   再在 main() 的 handlers 字典里加：`\"{name}\": {name}_cli.dispatch`",
        f"6. 本地：uv run python -m aios {name} ping",
        f"7. push → 服务器 git pull → bash deploy/run_migrations.sh → systemctl restart aios.service",
    ]

    return ScaffoldResult(created=created, skipped=skipped, next_steps=next_steps)
