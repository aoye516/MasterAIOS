# AIOS — Native AI OS (Fractal Nanobot Edition)

**Language / 语言**: **English** · [简体中文](README.zh-CN.md)

> A personal AI operating system. Built on [HKUDS/nanobot](https://github.com/HKUDS/nanobot) as the kernel, accessed through Feishu (Lark) as the unified conversational entry point, with a fractal **"Master + internal sub-agents + external sub-agents"** architecture underneath.

![Status](https://img.shields.io/badge/version-1.0.0--fractal-blue)
![Python](https://img.shields.io/badge/python-3.12-green)
![Kernel](https://img.shields.io/badge/kernel-nanobot--ai-purple)

---

## 0. TL;DR

- **Entry point**: Feishu → nanobot Master Agent.
- **Decision tree**: single-step questions → Master answers directly; domain-specific life management (money / knowledge / tools / wellbeing / …) → spawn an **internal sub-agent**; large coding tasks → delegate to an **external sub-agent** (`aios code-helper` → `claude` CLI subprocess, with the same pattern reusable for Codex / Cursor CLI / other coding agents).
- **Kernel**: [`vendor/nanobot/`](vendor/nanobot/) is a git submodule (Fork + Vendor model). Upstream upgrade SOP: [`docs/upgrade-from-upstream.md`](docs/upgrade-from-upstream.md).
- **AIOS-specific code**: `workspace/` (nanobot config + skills + agent routing examples) + `aios/` (CLI + sub-agent packages + PG bridge + external-agent bridges) + `deploy/` (systemd unit + scripts).

The legacy v0.x stack (custom Master + Agents + Channels) has been archived to [`legacy/`](legacy/) for historical reference only.

---

## 1. Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                    Feishu / WebSocket / Webhook                       │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │ lark-oapi
                               ▼
┌────────────────────────────────────────────────────────────────────────┐
│              nanobot Master Agent  (Master = SOUL.md)                  │
│  ────────────────────────────────────────────────────────────────────  │
│  • Receives every user message, decomposes intents, routes each one    │
│  • Maintains SessionContext (short-term dialog + Dream long-term       │
│    memory) and is the only place that talks back to the user           │
│  • Tier 1 routing = LLM reads each sub-agent's SKILL.md description    │
│  • Tier 2 routing = self-evolving examples pulled from routing_traces  │
└──┬──────────────────────┬──────────────────────────────┬───────────────┘
   │ spawn (in-process)   │ exec → bash aios <name> ...  │ built-in tools
   ▼                      ▼                              ▼
┌──────────────────────┐  ┌────────────────────────────┐ ┌────────────────┐
│ Internal sub-agents  │  │ External sub-agents         │ │ cron / read /  │
│ (one nanobot spawn   │  │ (independent subprocess +   │ │ web_search /   │
│  per domain)         │  │  independent LLM session)   │ │ message / …    │
│                      │  │                             │ │                │
│ • steward 💰         │  │ • code_helper 🛠️           │ └────────────────┘
│   ledger + inventory │  │   → claude CLI              │
│ • mindscape 📚       │  │   (stream-json subprocess)  │
│   notes + watch_list │  │                             │
│   + learning plans   │  │ Pattern is generic — drop   │
│ • toolbox 🧰         │  │ in codex / cursor / etc.    │
│   amap (weather /    │  │ as additional bridges       │
│   route / transit /  │  │ under aios/<name>/          │
│   POI / …) + mini    │  │                             │
│ • wellbeing 🌅       │  └────────────────────────────┘
│   morning brief +    │
│   habits + health    │
│ • life_manager 🗂️   │
│   cross-domain glue  │
└──────────┬───────────┘
           │ all writes go through `bash aios <name> ...`
           ▼
┌────────────────────────────────────────────────────────────────────────┐
│   PostgreSQL 16 + pgvector                                             │
│   archival_memory (vector(1024) + tsvector)  +  per-agent tables       │
│   migrated by deploy/run_migrations.sh from aios/db/migrations/*.sql   │
└────────────────────────────────────────────────────────────────────────┘
```

Full architecture rationale: [`docs/architecture.md`](docs/architecture.md).
Sub-agent contract (the four-piece convention every internal agent must follow): [`docs/agent-contract.md`](docs/agent-contract.md).

---

## 2. Repository Layout

```
AIOS/
├── vendor/nanobot/             # git submodule → HKUDS/nanobot (or your fork)
├── workspace/                  # nanobot workspace
│   ├── config.json             # provider / channel / tool toggles
│   ├── SOUL.md                 # Master persona (locally customizable)
│   ├── USER.md                 # User profile (auto-maintained by Dream)
│   ├── AGENTS.md               # Always-on system prompt: sub-agent directory + playbooks
│   ├── memory/MEMORY.md        # Long-term memory seed
│   ├── skills/                 # One folder per role/skill
│   │   ├── pg_archive_search/  # Hybrid vector + fulltext search over archival_memory
│   │   ├── router/             # Tier-1/2 routing playbook (read by Master)
│   │   ├── code_helper/        # External sub-agent: Claude Code via CLI
│   │   ├── life_manager/       # Internal sub-agent: cross-domain glue
│   │   ├── steward/            # Internal sub-agent: money + things
│   │   ├── mindscape/          # Internal sub-agent: knowledge
│   │   ├── toolbox/            # Internal sub-agent: utilities (amap + mini-tools)
│   │   └── wellbeing/          # Internal sub-agent: daily brief + habits + health
│   └── agents/                 # Routing memory per agent (seed + eval)
│       ├── steward/{seed_examples,routing_eval}.jsonl
│       ├── mindscape/...
│       ├── toolbox/...
│       └── wellbeing/...
├── aios/                       # AIOS-specific Python package
│   ├── cli.py                  # Single CLI entry: `aios <subcmd> ...`
│   ├── pg/                     # asyncpg + pgvector bridge
│   ├── embed.py                # Shared SiliconFlow embedding helper (1024-d)
│   ├── route/                  # routing_traces read/write + Tier 2 example fetch
│   ├── scaffold/               # `aios scaffold-agent <name>` generator
│   ├── integrations/           # Shared external clients (e.g. amap.py for Gaode)
│   ├── acp/                    # External coding-agent bridges (today: claude CLI)
│   ├── steward/                # Per-agent: db.py + cli.py
│   ├── mindscape/              #   ditto
│   ├── toolbox/                #   ditto (+ db for places aliases)
│   ├── wellbeing/              #   ditto (+ brief.py for rule-based dressing tips)
│   └── db/migrations/          # 0001-routing.sql, 0002-steward.sql, ...
├── deploy/                     # Server deployment
│   ├── aios.service            # systemd unit
│   ├── server_setup.sh         # One-shot server bootstrap
│   ├── run_migrations.sh       # Idempotent migration runner
│   └── deploy.sh               # Local one-click rsync + remote restart
├── scripts/                    # DB init / backup / local launcher / data backfills
├── tests/                      # AIOS unit tests
├── legacy/                     # Archived v0.x (app/, run_ws.py, admin-web/)
├── docs/
│   ├── architecture.md
│   ├── agent-contract.md       # ← read this before adding a sub-agent
│   ├── upgrade-from-upstream.md
│   └── evolution/
└── pyproject.toml
```

---

## 3. Sub-Agent Catalogue (currently shipped)

| Agent | Kind | Domain | Key CLI |
|---|---|---|---|
| **steward** 💰 | Internal | Personal finance + household inventory (natural-language ledger, "where is X", warranty / lent-out tracking) | `aios steward {expense, income, tx-list, tx-sum, report, put, where, item-list, item-move, item-update, account-*, category-*, location-*}` |
| **mindscape** 📚 | Internal | Notes + want-to-read/want-to-watch lists (with optional rating lookup) + learning plans | `aios mind {note, notes, want, watchlist, finish, drop, recall, plan-add, plan-list, plan-update}` |
| **toolbox** 🧰 | Internal | Gaode (Amap) full kit (weather / driving route / transit / metro-near / traffic / POI / geocoding) + place aliases + calculator / units / timezone | `aios toolbox {weather, route, transit, metro-near, traffic-road, poi, geo, regeo, where-add, where-list, where-rm, calc, units, tz}` |
| **wellbeing** 🌅 | Internal | Daily morning brief (weather + dressing tip + personal health hint) + habit checkins (streak) + health metric time-series | `aios wellbeing {morning-brief, habit-add, habit-done, habit-list, habit-streak, habit-pause, habit-resume, habit-archive, log, log-list, log-stats}` |
| **life_manager** 🗂️ | Internal | Cross-step orchestration: review / summarize / curate `archival_memory` | nanobot built-in `spawn` + `aios archive-search` |
| **code_helper** 🛠️ | **External** | Coding tasks > 30 lines / multi-file / strict step-by-step — delegated to Claude Code as a subprocess (same pattern reusable for Codex / Cursor CLI) | `aios code-helper --task <name> "<description>"` |

Each internal sub-agent follows the **four-piece contract** documented in [`docs/agent-contract.md`](docs/agent-contract.md):

1. `workspace/skills/<name>/SKILL.md` — domain definition + spawn template + few-shot slot
2. `workspace/agents/<name>/{seed_examples,routing_eval}.jsonl` — routing seeds + eval set
3. `aios/<name>/` Python package + `aios <name> ...` CLI subcommand
4. `aios/db/migrations/NNNN-<name>.sql` — idempotent schema migration

---

## 4. Quick Start (Local)

### 4.1 Prerequisites

- macOS / Linux
- Python 3.12 (managed via [uv](https://docs.astral.sh/uv/))
- Node.js 18+ (required by the `claude` CLI, only needed if you use `code_helper`)
- PostgreSQL 16 + [pgvector](https://github.com/pgvector/pgvector) (db name `aios`, see [`scripts/init_db.sql`](scripts/init_db.sql))
- `.env` (not tracked):
  ```
  DATABASE_URL=postgresql://<user>@localhost:5432/aios
  SILICONFLOW_API_KEY=sk-...                 # for embeddings (BAAI/bge-large-zh-v1.5)
  FEISHU_APP_ID=cli_...
  FEISHU_APP_SECRET=...
  ANTHROPIC_API_KEY=...                      # optional: required by code_helper
  AMAP_API_KEY=...                           # optional: required by toolbox + wellbeing morning-brief
  ```

### 4.2 Install

```bash
git clone <this-repo> AIOS && cd AIOS
git submodule update --init --recursive

curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv is missing

uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e vendor/nanobot
uv pip install -e .

# Optional: install the claude CLI for code_helper
npm install -g @anthropic-ai/claude-code

# Apply all DB migrations (idempotent; tracked in schema_migrations)
bash deploy/run_migrations.sh
```

### 4.3 Run

```bash
# One-shot smoke test
bash scripts/run_nanobot.sh agent -m "hello"

# Long-running gateway (connects to Feishu)
bash scripts/run_nanobot.sh gateway

# Use AIOS tools directly from the CLI
aios db-ping
aios archive-search "nanobot integration" --embed --limit 5
aios code-helper --task hello "say hi in one line"

# Try every internal sub-agent's ping
aios steward --help        # | aios mind --help | aios toolbox --help | aios wellbeing --help
aios wellbeing morning-brief --place "北京市朝阳区" --format plain
```

### 4.4 Test

```bash
pytest tests/
```

---

## 5. Server Deployment

### 5.1 One-time bootstrap (run on the server)

```bash
ssh root@<server>
cd /claude/aios
bash deploy/server_setup.sh
systemctl enable aios && systemctl start aios && journalctl -u aios -f
```

### 5.2 Day-to-day updates (run locally)

```bash
# 1. push to GitHub first — GitHub is the source of truth
git push origin <branch>

# 2. dry-run to preview which files will be transferred
AIOS_REMOTE=root@<server> bash deploy/deploy.sh dry

# 3. actually deploy: rsync (no --delete) + remote submodule update + systemctl restart
AIOS_REMOTE=root@<server> bash deploy/deploy.sh

# 4. on the server, apply any new migrations
ssh root@<server> "cd /claude/aios && bash deploy/run_migrations.sh"
```

`deploy.sh` excludes `.env`, `workspace/SOUL.md`, `workspace/USER.md`, `workspace/memory/`, `workspace/sessions/`, `vendor/nanobot/.git/`, etc. — so your private persona, runtime artefacts, and DB content on the server are never overwritten.

Detailed conventions (including "always confirm before any auto-ssh deploys to prod") live in [`CLAUDE.md`](CLAUDE.md) §8.

### 5.x Hot-swap the Master model (one liner)

`agents.defaults.model` in `workspace/config.json` is now `${LLM_MODEL_MAIN}` — nanobot resolves it from the server `.env` at startup. `deploy/switch-model.sh` wraps the workflow with a hard SF preflight:

```bash
# show current model
AIOS_REMOTE=root@<server> bash deploy/switch-model.sh --show

# probe a candidate model on SiliconFlow (no config change, no restart)
AIOS_REMOTE=root@<server> bash deploy/switch-model.sh --check deepseek-ai/DeepSeek-V4-Flash

# actually swap: SF probe → patch .env → systemctl restart aios → verify active
AIOS_REMOTE=root@<server> bash deploy/switch-model.sh deepseek-ai/DeepSeek-V3.2
```

The script fires a real `chat/completions` call against the target (10 s timeout). **Only HTTP 200 leads to a config change** — so a typo or a not-yet-released model can never take Master down. After restart it `systemctl is-active`s the service and dumps the last 20 log lines on failure.

---

## 6. Upgrading the upstream nanobot

```bash
cd vendor/nanobot
git fetch upstream
git rebase upstream/main
cd ../..
git add vendor/nanobot
git commit -m "chore(vendor): bump nanobot"
uv pip install -e vendor/nanobot
pytest tests/
bash deploy/deploy.sh
```

Full SOP (fork setup, rollback, when to tag `[AIOS-PATCH]`): [`docs/upgrade-from-upstream.md`](docs/upgrade-from-upstream.md).

---

## 7. Adding a new internal sub-agent

> Full spec: [`docs/agent-contract.md`](docs/agent-contract.md). The summary below is the happy path — use `wellbeing` as a complete worked example to copy from.

### 7.1 Generate the skeleton

```bash
aios scaffold-agent <name> --domain <tag> --emoji 🤖
# e.g.
aios scaffold-agent roster --domain contacts --emoji 👥
```

This creates the **four pieces** required by the contract:

```
workspace/skills/roster/SKILL.md             # routing description + spawn template
workspace/agents/roster/seed_examples.jsonl  # cold-start routing seeds (empty)
workspace/agents/roster/routing_eval.jsonl   # eval set (empty)
aios/roster/__init__.py
aios/roster/cli.py                           # subcommand skeleton with `ping`
aios/db/migrations/00NN-roster.sql           # empty schema (auto-numbered)
```

### 7.2 Fill in business logic

Use [`aios/wellbeing/`](aios/wellbeing/) and [`workspace/skills/wellbeing/SKILL.md`](workspace/skills/wellbeing/SKILL.md) as a complete reference. Roughly:

1. **Schema** — write `aios/db/migrations/00NN-<name>.sql`. Always idempotent (`CREATE TABLE IF NOT EXISTS`), prefix tables with the agent name, reuse `users(id)` / `VECTOR(1024)` / `tsvector` patterns.
2. **DB helpers** — `aios/<name>/db.py` with async functions that wrap `PgClient`.
3. **CLI** — `aios/<name>/cli.py` with `add_subparsers()` registering each subcommand and a `dispatch()` async handler. Every command supports `--json`. Code lives in the sub-package; nothing leaks into `aios/cli.py` except a one-line wiring (see step 5).
4. **Skill** — flesh out `workspace/skills/<name>/SKILL.md`:
   - frontmatter `description` should be 1–2 sentences answering "what is this agent for / not for"
   - `## 领域定义` lists 3–5 concrete scenarios + 2–3 boundary cases
   - `## Spawn Task 模板` is a fixed prompt block Master pastes into `spawn(task=...)`
   - `## CLI 一览` documents each subcommand
   - `{{ROUTING_EXAMPLES}}` placeholder gets filled at runtime by `aios route examples <name>`
5. **Wire it into the main CLI** — add three lines to [`aios/cli.py`](aios/cli.py):
   ```python
   from aios.<name> import cli as <name>_cli
   ...
   <name>_cli.add_subparsers(sub)               # in _build_parser()
   ...
   "<name>": <name>_cli.dispatch,                # in handlers dict
   ```
6. **Routing memory** — at least 10 lines in `seed_examples.jsonl` and 30 lines in `routing_eval.jsonl`. Format:
   ```jsonl
   {"query": "我体重 70.5", "routed_to": "wellbeing", "rationale": "log weight"}
   ```
7. **Teach Master about it** — append a row to the sub-agent table in [`workspace/AGENTS.md`](workspace/AGENTS.md) so Master knows the new agent exists from message 1 (don't wait for Tier 2 examples to accumulate).

### 7.3 Test, ship, deploy

```bash
# local
uv run python -m aios.cli <name> ping --json
bash deploy/run_migrations.sh
uv run python -m aios.cli <name> <subcmd> ...     # smoke a few commands

# git
git add . && git commit -m "feat(<name>): ..." && git push

# server
AIOS_REMOTE=root@<server> bash deploy/deploy.sh
ssh root@<server> "cd /claude/aios && bash deploy/run_migrations.sh && systemctl restart aios"
```

After a few days of real use, run `aios route eval --agent <name>` to see how often Master routes correctly to it; if the seed examples are clearly off, edit the JSONL file and the SKILL.md `## 领域定义` (these two are the only Tier-1 routing levers).

---

## 8. Plugging in another external coding agent (Codex, Cursor CLI, …)

Internal sub-agents share the Master's LLM, session, and process. When you want a **fully isolated** agent — its own LLM provider, its own subprocess, its own session log — you use the **external sub-agent** pattern. AIOS already has one bridge in production (`code_helper` → `claude` CLI); the same shape applies to any other CLI-based coding agent.

### 8.1 The pattern (3 layers)

```
┌──────────────────────────────────────────────────────────────────────┐
│ workspace/skills/<bridge>/SKILL.md                                  │
│   • When to use it (decision rules for Master)                      │
│   • CLI invocation contract: `aios <bridge> --task <name> "<desc>"` │
│   • Task-name continuity rules (same name = same external session)  │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼────────────────────────────────────┐
│ aios/cli.py subcommand → aios/<bridge>/                             │
│   delegate_to_<external_cli>(task, description, timeout) -> Result  │
│   • Spawns the external CLI with stream-json (or whatever NDJSON    │
│     format it supports) and parses incrementally                    │
│   • Persists session ID per task name in <workspace>/<bridge>/...   │
│   • Returns final_text + tool_calls + cost_usd + error              │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼────────────────────────────────────┐
│ External coding agent (independent LLM, independent process)        │
│   claude / codex / cursor-cli / aider / ...                         │
└──────────────────────────────────────────────────────────────────────┘
```

The two non-negotiables:

- **`--task <name>` is sticky.** Same name → resume the external session (keeps memory across turns). Different name → fresh session. Master must learn this rule from the SKILL.md and echo `📎 task: <name>` at the end of every reply so the next turn can copy it back.
- **Master always renders the result.** The bridge returns structured JSON; the SKILL.md instructs Master to fold `tool_calls` into one-line progress (`🔧 Write hello.py`) and present `final_text` — never dump the raw JSON to the user.

### 8.2 Reference implementation: `code_helper` → Claude Code

| Layer | File | Lines |
|---|---|---|
| Skill (when + how) | [`workspace/skills/code_helper/SKILL.md`](workspace/skills/code_helper/SKILL.md) | Decision rules, task-name regex, JSON output schema, error matrix |
| CLI subcommand | [`aios/cli.py`](aios/cli.py) (`_cmd_code_helper`) | Argparse + `delegate_to_claude` call |
| Bridge package | [`aios/acp/`](aios/acp/) | `client.py` spawns `claude --output-format stream-json --resume <session_id>`, parses NDJSON events |
| Per-task state | `~/aios-cc-workspace/<task>/` | One subdir per task, with `session_id` file for `--resume` |

### 8.3 Adding a new bridge in three steps

For example, to add **Codex CLI** as a second external coding agent:

1. **Mirror the bridge package** under `aios/codex/` with the same shape as `aios/acp/`:
   - `client.py` exposing `delegate_to_codex(task, description, timeout) -> CodexResult`
   - Use whatever streaming format Codex provides (NDJSON / SSE / final-only); keep the public dataclass identical to `CodeHelperResult` so the SKILL.md template can be copied with minimal edits.
   - Persist session continuity per task name.
2. **Register a CLI subcommand** in `aios/cli.py`:
   ```python
   p_codex = sub.add_parser("codex", help="Delegate a coding task to codex CLI")
   p_codex.add_argument("--task")
   p_codex.add_argument("description", nargs="?")
   p_codex.add_argument("--timeout", type=int, default=None)
   p_codex.add_argument("--json", action="store_true")
   ...
   "codex": _cmd_codex,
   ```
3. **Write the skill** at `workspace/skills/codex/SKILL.md` — copy `code_helper/SKILL.md` and edit:
   - When to use Codex vs Claude Code (model preferences, cost, capabilities)
   - The CLI invocation contract is identical: `aios codex --task <name> "<desc>" --json`
   - Add Codex to the sub-agent directory in `workspace/AGENTS.md` so Master sees it on the very first message

That's it — Master learns to route to it from Tier 1 (the SKILL description) and starts collecting Tier 2 examples automatically. The same recipe works for Cursor CLI, aider, Continue, or any other CLI-driven coding agent that supports session resumption.

---

## 9. Personalizing your Master

The repo ships [`workspace/SOUL.md`](workspace/SOUL.md) as a **generic template** (the persona is named "Master"). To give your Master its own name, voice, or private preferences:

```bash
# 1. Edit it however you want (rename, change tone, add private instructions)
vim workspace/SOUL.md
vim workspace/USER.md
vim workspace/memory/MEMORY.md

# 2. Hide your local edits from git so `git status` stays clean
git update-index --skip-worktree workspace/SOUL.md workspace/USER.md workspace/memory/MEMORY.md

# 3. Use locally / on the server normally — nanobot reads your local version
```

Restore tracking (e.g. when you want to pull template updates):

```bash
git update-index --no-skip-worktree workspace/SOUL.md workspace/USER.md
```

`deploy.sh` already **excludes** `workspace/SOUL.md` / `workspace/USER.md` / `workspace/memory/MEMORY.md`, so the private persona on the server is never overwritten by local pushes.

---

## 10. Documentation Map

| Document | Purpose |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | System architecture, Master ↔ Sub-Agent model, data layout |
| [`docs/agent-contract.md`](docs/agent-contract.md) | **Read first** before adding an internal sub-agent — the four-piece contract, routing tiers, scaffold flow |
| [`docs/upgrade-from-upstream.md`](docs/upgrade-from-upstream.md) | nanobot upstream upgrade SOP (fork, rebase, rollback) |
| [`docs/evolution/phases/phase-fractal-rewrite.md`](docs/evolution/phases/phase-fractal-rewrite.md) | Design decisions and history of this rewrite |
| [`workspace/AGENTS.md`](workspace/AGENTS.md) | Master's always-on system prompt — sub-agent directory + per-agent playbooks |
| [`workspace/SOUL.md`](workspace/SOUL.md) | Master persona (locally customizable) |
| [`workspace/skills/router/SKILL.md`](workspace/skills/router/SKILL.md) | Tier 1 / Tier 2 routing playbook |
| [`workspace/skills/code_helper/SKILL.md`](workspace/skills/code_helper/SKILL.md) | External sub-agent reference (Claude CLI) — copy this when adding Codex / Cursor CLI / etc. |
| [`workspace/skills/wellbeing/SKILL.md`](workspace/skills/wellbeing/SKILL.md) | Internal sub-agent reference (most recently built) — copy this when scaffolding a new domain |
| [`workspace/skills/pg_archive_search/SKILL.md`](workspace/skills/pg_archive_search/SKILL.md) | Long-term memory hybrid search |
| [`legacy/README.md`](legacy/README.md) | Notes on archived v0.x code |
| [`CLAUDE.md`](CLAUDE.md) | AI coding conventions (deployment discipline, secrets handling) |

---

## 11. License

MIT
