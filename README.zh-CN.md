# AIOS — Native AI OS (Fractal Nanobot Edition)

**语言 / Language**: [English](README.md) · **简体中文**

> 个人 AI 操作系统。以 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 为内核，通过飞书提供统一对话入口，背后跑「内部 sub-agent + 外部 sub-agent」的分形协作架构。

![Status](https://img.shields.io/badge/version-1.0.0--fractal-blue)
![Python](https://img.shields.io/badge/python-3.12-green)
![Kernel](https://img.shields.io/badge/kernel-nanobot--ai-purple)

---

## 0. 你只需要看这一段

- **入口**：飞书 → nanobot Master Agent
- **决策树**：单步问题 master 直接答；多步生活管理 → spawn 内部 sub-agent (LifeManager)；写代码 → 调外部 sub-agent (`aios code-helper` → claude CLI)
- **内核**：[`vendor/nanobot/`](vendor/nanobot/) 是 git submodule（Fork + Vendor 模式）。上游升级见 [`docs/upgrade-from-upstream.md`](docs/upgrade-from-upstream.md)
- **AIOS 自己写的**：`workspace/`（nanobot 配置 + skills）+ `aios/`（PG 桥 + Claude CLI 桥的 Python 薄层）+ `deploy/`（systemd unit + 脚本）

旧的 v0.x（自研 Master + Agents + Channels）已归档到 [`legacy/`](legacy/)，仅作历史参考。

---

## 1. 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                        飞书 / WS / Webhook                          │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ lark-oapi
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│              nanobot Master Agent  (Master = SOUL.md)                │
│  ─────────────────────────────────────────────────────────────────   │
│  • 接收用户消息，决定是自己回 / spawn 子代理 / 调外部 Claude          │
│  • 维护 SessionContext（短期对话 + Dream 两阶段记忆）                │
│  • 所有发给用户的话都从这里出（铁律）                                │
└──────┬────────────────────────┬──────────────────────────┬──────────┘
       │ spawn (内置)           │ exec → aios CLI          │ 内置 cron / read /
       ▼                        ▼                          │ web_search 等
┌──────────────────┐  ┌─────────────────────┐              │
│ 内部 sub-agent    │  │ aios code-helper    │              │
│ (LifeManager)    │  │ → claude CLI 子进程 │              │
│ 同进程后台 task   │  │ 独立 LLM (Claude)   │              │
│ 同 LLM provider  │  │ 独立 session.jsonl  │              │
│ 全套 nanobot 工具 │  │ stream-json 增量    │              │
└──────────────────┘  └─────────────────────┘              │
                                                           ▼
                              ┌─────────────────────────────────────┐
                              │   PostgreSQL 16 + pgvector          │
                              │   archival_memory(vector(1024))      │
                              │   通过 aios archive-search 检索      │
                              └─────────────────────────────────────┘
```

完整架构见 [`docs/architecture.md`](docs/architecture.md)。

---

## 2. 目录结构

```
AIOS/
├── vendor/nanobot/          # git submodule → HKUDS/nanobot（或 fork）
├── workspace/               # nanobot 工作区
│   ├── config.json          # provider / channel / 工具开关
│   ├── SOUL.md              # Master 人格 + Fractal 决策树（可本地个性化）
│   ├── USER.md              # 用户画像（Dream 自动维护）
│   ├── memory/MEMORY.md     # 长期记忆种子
│   └── skills/              # AIOS 自定义 skills
│       ├── pg_archive_search/
│       ├── code_helper/
│       └── life_manager/    # 内部 sub-agent 角色模板
├── aios/                    # AIOS 差异化 Python 包
│   ├── pg/                  # asyncpg + pgvector 桥
│   ├── acp/                 # claude CLI 桥（stream-json）
│   └── cli.py               # 统一 CLI: aios archive-search / code-helper / db-ping
├── deploy/                  # 服务器部署
│   ├── aios.service         # systemd unit
│   ├── server_setup.sh      # 一次性初始化
│   └── deploy.sh            # 本地一键 rsync + 远程 restart
├── scripts/                 # DB init / 备份 / 本地 launcher
├── tests/                   # AIOS 单测（aios.* 包）
├── legacy/                  # 旧 v0.x 全部归档（app/ run_ws.py admin-web/）
├── docs/
│   ├── architecture.md
│   ├── upgrade-from-upstream.md
│   └── evolution/
└── pyproject.toml
```

---

## 3. 快速开始（本地）

### 3.1 准备

- macOS / Linux
- Python 3.12（uv 自带管理）
- Node.js 18+（给 `claude` CLI）
- PostgreSQL 16 + pgvector（数据库名 `aios`，参见 [`scripts/init_db.sql`](scripts/init_db.sql)）
- `.env`（不入库）：
  ```
  DATABASE_URL=postgresql://<user>@localhost:5432/aios
  SILICONFLOW_API_KEY=sk-...
  FEISHU_APP_ID=cli_...
  FEISHU_APP_SECRET=...
  ANTHROPIC_API_KEY=...     # 可选：code_helper 用
  ```

### 3.2 安装

```bash
git clone <this-repo> AIOS && cd AIOS
git submodule update --init --recursive

# 装 uv（如未装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 创建虚拟环境 + editable 安装
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e vendor/nanobot
uv pip install -e .

# 装 claude CLI（可选，给 code_helper 用）
npm install -g @anthropic-ai/claude-code
```

### 3.3 跑

```bash
# 单条消息冒烟
bash scripts/run_nanobot.sh agent -m "你好"

# 长跑 gateway（监听飞书）
bash scripts/run_nanobot.sh gateway

# CLI 直接用 AIOS 工具
aios db-ping
aios archive-search "nanobot 集成" -k 5
aios code-helper --task hello "say hi in one line"
```

### 3.4 测

```bash
pytest tests/
```

---

## 4. 服务器部署

### 4.1 一次性初始化（在服务器上执行）

```bash
ssh root@<server>
cd /claude/aios
bash deploy/server_setup.sh
# 然后按提示：
systemctl enable aios
systemctl start aios
journalctl -u aios -f
```

### 4.2 日常更新（在本地执行）

```bash
# dry-run 看会传什么
bash deploy/deploy.sh dry

# 真传
bash deploy/deploy.sh
```

`deploy.sh` 做三件事：rsync（无 `--delete`）→ 远程 `git submodule update + uv pip install -e` → `systemctl restart aios`。

详细规范（包括「禁止 ssh 自动部署到生产前先确认」等纪律）见 [`CLAUDE.md`](CLAUDE.md) 第 8 节。

---

## 5. 上游 nanobot 升级

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

完整 SOP（含 fork 配置、回滚、何时打 `[AIOS-PATCH]`）见 [`docs/upgrade-from-upstream.md`](docs/upgrade-from-upstream.md)。

---

## 6. 个性化你的 Master

仓库里 [`workspace/SOUL.md`](workspace/SOUL.md) 是一个**通用模板**（人设叫 Master）。如果你想给自己的 Master 起名字、改口吻、加私人偏好：

```bash
# 1. 编辑成你想要的样子（改名字、改口吻、加你的私人指令）
vim workspace/SOUL.md
vim workspace/USER.md

# 2. 把本地修改对 git 隐身，避免每次 git status 都显示 modified
#    也避免不小心 commit 把你的私人人格 push 到公开仓库
git update-index --skip-worktree workspace/SOUL.md workspace/USER.md

# 3. 本地 / 服务器照常使用，nanobot 会读你这个本地版本
```

恢复跟踪（想升级模板时）：

```bash
git update-index --no-skip-worktree workspace/SOUL.md workspace/USER.md
```

`deploy.sh` 默认**排除** `workspace/SOUL.md` / `workspace/USER.md` / `workspace/memory/MEMORY.md`，所以服务器上的私人人设不会被本地推送覆盖。

---

## 7. 文档导航

| 文档 | 用途 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 系统架构、Master ↔ Sub-Agent 模型 |
| [`docs/upgrade-from-upstream.md`](docs/upgrade-from-upstream.md) | nanobot 上游升级 SOP |
| [`docs/evolution/phases/phase-fractal-rewrite.md`](docs/evolution/phases/phase-fractal-rewrite.md) | 本次重写的设计决策与历史 |
| [`workspace/SOUL.md`](workspace/SOUL.md) | Master 人格 + Fractal 决策树（可本地个性化） |
| [`workspace/skills/life_manager/SKILL.md`](workspace/skills/life_manager/SKILL.md) | 内部 sub-agent 角色模板 |
| [`workspace/skills/code_helper/SKILL.md`](workspace/skills/code_helper/SKILL.md) | 外部 sub-agent (Claude CLI) 用法 |
| [`workspace/skills/pg_archive_search/SKILL.md`](workspace/skills/pg_archive_search/SKILL.md) | 长期记忆混合检索 |
| [`legacy/README.md`](legacy/README.md) | 旧 v0.x 代码归档说明 |
| [`CLAUDE.md`](CLAUDE.md) | AI 编码行为规范（部署纪律、敏感信息管理） |

---

## 8. License

MIT
