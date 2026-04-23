# AIOS — Native AI OS (Fractal Nanobot Edition)

**Language / 语言**: **English** · [简体中文](README.zh-CN.md)

> A personal AI operating system. Built on [HKUDS/nanobot](https://github.com/HKUDS/nanobot) as the kernel, accessed through Feishu (Lark) as the unified conversational entry point, with a fractal "internal sub-agent + external sub-agent" collaboration architecture underneath.

![Status](https://img.shields.io/badge/version-1.0.0--fractal-blue)
![Python](https://img.shields.io/badge/python-3.12-green)
![Kernel](https://img.shields.io/badge/kernel-nanobot--ai-purple)

---

## 0. TL;DR

- **Entry point**: Feishu → nanobot Master Agent
- **Decision tree**: single-step questions → Master answers directly; multi-step life management → spawn an internal sub-agent (LifeManager); coding tasks → call an external sub-agent (`aios code-helper` → `claude` CLI)
- **Kernel**: [`vendor/nanobot/`](vendor/nanobot/) is a git submodule (Fork + Vendor model). Upstream upgrade SOP: [`docs/upgrade-from-upstream.md`](docs/upgrade-from-upstream.md)
- **AIOS-specific code**: `workspace/` (nanobot config + skills) + `aios/` (thin Python layer bridging PostgreSQL and the Claude CLI) + `deploy/` (systemd unit + scripts)

The legacy v0.x stack (custom Master + Agents + Channels) has been archived to [`legacy/`](legacy/) for historical reference only.

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Feishu / WebSocket / Webhook                     │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ lark-oapi
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│              nanobot Master Agent  (Master = SOUL.md)                │
│  ─────────────────────────────────────────────────────────────────   │
│  • Receives user messages, decides: answer directly / spawn sub-agent│
│    / delegate to external Claude                                     │
│  • Maintains SessionContext (short-term dialog + Dream two-stage     │
│    memory)                                                           │
│  • Every reply to the user comes from here (hard rule)               │
└──────┬────────────────────────┬──────────────────────────┬──────────┘
       │ spawn (built-in)       │ exec → aios CLI          │ built-in
       ▼                        ▼                          │ cron / read /
┌──────────────────┐  ┌─────────────────────┐              │ web_search …
│ Internal subagent│  │ aios code-helper    │              │
│ (LifeManager)    │  │ → claude CLI subproc│              │
│ Same process     │  │ Independent LLM     │              │
│ Same LLM provider│  │ Independent session │              │
│ Full nanobot kit │  │ stream-json output  │              │
└──────────────────┘  └─────────────────────┘              │
                                                           ▼
                              ┌─────────────────────────────────────┐
                              │   PostgreSQL 16 + pgvector          │
                              │   archival_memory(vector(1024))      │
                              │   queried via aios archive-search    │
                              └─────────────────────────────────────┘
```

Full architecture: [`docs/architecture.md`](docs/architecture.md).

---

## 2. Repository Layout

```
AIOS/
├── vendor/nanobot/          # git submodule → HKUDS/nanobot (or your fork)
├── workspace/               # nanobot workspace
│   ├── config.json          # provider / channel / tool toggles
│   ├── SOUL.md              # Master persona + Fractal decision tree (locally customizable)
│   ├── USER.md              # User profile (auto-maintained by Dream)
│   ├── memory/MEMORY.md     # Long-term memory seed
│   └── skills/              # AIOS custom skills
│       ├── pg_archive_search/
│       ├── code_helper/
│       └── life_manager/    # Internal sub-agent role template
├── aios/                    # AIOS-specific Python package
│   ├── pg/                  # asyncpg + pgvector bridge
│   ├── acp/                 # claude CLI bridge (stream-json)
│   └── cli.py               # Unified CLI: aios archive-search / code-helper / db-ping
├── deploy/                  # Server deployment
│   ├── aios.service         # systemd unit
│   ├── server_setup.sh      # one-shot server bootstrap
│   └── deploy.sh            # local one-click rsync + remote restart
├── scripts/                 # DB init / backup / local launcher
├── tests/                   # AIOS unit tests (aios.* package)
├── legacy/                  # Archived v0.x (app/, run_ws.py, admin-web/)
├── docs/
│   ├── architecture.md
│   ├── upgrade-from-upstream.md
│   └── evolution/
└── pyproject.toml
```

---

## 3. Quick Start (Local)

### 3.1 Prerequisites

- macOS / Linux
- Python 3.12 (managed via uv)
- Node.js 18+ (required by the `claude` CLI)
- PostgreSQL 16 + pgvector (database name `aios`, see [`scripts/init_db.sql`](scripts/init_db.sql))
- `.env` (not tracked):
  ```
  DATABASE_URL=postgresql://<user>@localhost:5432/aios
  SILICONFLOW_API_KEY=sk-...
  FEISHU_APP_ID=cli_...
  FEISHU_APP_SECRET=...
  ANTHROPIC_API_KEY=...     # optional: required by code_helper
  ```

### 3.2 Install

```bash
git clone <this-repo> AIOS && cd AIOS
git submodule update --init --recursive

# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create venv + editable installs
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e vendor/nanobot
uv pip install -e .

# Install the claude CLI (optional, used by code_helper)
npm install -g @anthropic-ai/claude-code
```

### 3.3 Run

```bash
# One-shot smoke test
bash scripts/run_nanobot.sh agent -m "hello"

# Long-running gateway (listens for Feishu)
bash scripts/run_nanobot.sh gateway

# Use AIOS tools directly from the CLI
aios db-ping
aios archive-search "nanobot integration" -k 5
aios code-helper --task hello "say hi in one line"
```

### 3.4 Test

```bash
pytest tests/
```

---

## 4. Server Deployment

### 4.1 One-time bootstrap (run on the server)

```bash
ssh root@<server>
cd /claude/aios
bash deploy/server_setup.sh
# Then follow the prompts:
systemctl enable aios
systemctl start aios
journalctl -u aios -f
```

### 4.2 Day-to-day updates (run locally)

```bash
# dry-run to preview which files will be transferred
bash deploy/deploy.sh dry

# actually deploy
bash deploy/deploy.sh
```

`deploy.sh` does three things: rsync (no `--delete`) → remote `git submodule update + uv pip install -e` → `systemctl restart aios`.

Detailed conventions (including "always confirm before any auto-ssh deploys to prod") live in [`CLAUDE.md`](CLAUDE.md) §8.

---

## 5. Upgrading the upstream nanobot

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

## 6. Personalizing your Master

The repo ships [`workspace/SOUL.md`](workspace/SOUL.md) as a **generic template** (the persona is named "Master"). To give your Master its own name, voice, or private preferences:

```bash
# 1. Edit it however you want (rename, change tone, add private instructions)
vim workspace/SOUL.md
vim workspace/USER.md

# 2. Hide your local edits from git so `git status` stays clean
#    and you never accidentally commit your private persona to a public repo
git update-index --skip-worktree workspace/SOUL.md workspace/USER.md

# 3. Use locally / on the server normally — nanobot reads your local version
```

Restore tracking (e.g. when you want to pull template updates):

```bash
git update-index --no-skip-worktree workspace/SOUL.md workspace/USER.md
```

`deploy.sh` already **excludes** `workspace/SOUL.md` / `workspace/USER.md` / `workspace/memory/MEMORY.md`, so the private persona on the server will not be overwritten by local pushes.

---

## 7. Documentation Map

| Document | Purpose |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | System architecture, Master ↔ Sub-Agent model |
| [`docs/upgrade-from-upstream.md`](docs/upgrade-from-upstream.md) | nanobot upstream upgrade SOP |
| [`docs/evolution/phases/phase-fractal-rewrite.md`](docs/evolution/phases/phase-fractal-rewrite.md) | Design decisions and history of this rewrite |
| [`workspace/SOUL.md`](workspace/SOUL.md) | Master persona + Fractal decision tree (locally customizable) |
| [`workspace/skills/life_manager/SKILL.md`](workspace/skills/life_manager/SKILL.md) | Internal sub-agent role template |
| [`workspace/skills/code_helper/SKILL.md`](workspace/skills/code_helper/SKILL.md) | External sub-agent (Claude CLI) usage |
| [`workspace/skills/pg_archive_search/SKILL.md`](workspace/skills/pg_archive_search/SKILL.md) | Long-term memory hybrid search |
| [`legacy/README.md`](legacy/README.md) | Notes on archived v0.x code |
| [`CLAUDE.md`](CLAUDE.md) | AI coding conventions (deployment discipline, secrets handling) |

---

## 8. License

MIT
