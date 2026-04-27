# AIOS — Native AI OS (Fractal Nanobot Edition)

**语言 / Language**: [English](README.md) · **简体中文**

> 个人 AI 操作系统。以 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 为内核，通过飞书提供统一对话入口，背后跑「**Master + 内部子代理 + 外部子代理**」的分形协作架构。

![Status](https://img.shields.io/badge/version-1.0.0--fractal-blue)
![Python](https://img.shields.io/badge/python-3.12-green)
![Kernel](https://img.shields.io/badge/kernel-nanobot--ai-purple)

---

## 0. 你只需要看这一段

- **入口**：飞书 → nanobot Master Agent。
- **决策树**：单步问题 Master 直接答；按领域落库的生活管理（钱 / 知识 / 工具 / 起居 / …）→ spawn 一个**内部子代理**；大块编码任务 → 调一个**外部子代理**（`aios code-helper` → `claude` CLI 子进程；同样的模式可复用 Codex / Cursor CLI / 任何其他编码 agent）。
- **内核**：[`vendor/nanobot/`](vendor/nanobot/) 是 git submodule（Fork + Vendor 模式）。上游升级见 [`docs/upgrade-from-upstream.md`](docs/upgrade-from-upstream.md)。
- **AIOS 自己写的**：`workspace/`（nanobot 配置 + skills + 各子代理的路由示例）+ `aios/`（CLI + 子代理 Python 包 + PG 桥 + 外部 agent 桥）+ `deploy/`（systemd unit + 脚本）。

旧的 v0.x（自研 Master + Agents + Channels）已归档到 [`legacy/`](legacy/)，仅作历史参考。

---

## 1. 架构总览

```
┌────────────────────────────────────────────────────────────────────────┐
│                       飞书 / WebSocket / Webhook                       │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │ lark-oapi
                               ▼
┌────────────────────────────────────────────────────────────────────────┐
│              nanobot Master Agent  (Master = SOUL.md)                  │
│  ────────────────────────────────────────────────────────────────────  │
│  • 接收每条用户消息，做意图拆分，每个意图独立路由                        │
│  • 维护 SessionContext（短期对话 + Dream 长期记忆），唯一对外回话出口     │
│  • Tier 1 路由 = LLM 读各子代理 SKILL.md description                    │
│  • Tier 2 路由 = 自演化的 routing_traces，按 agent 拉历史成功 query 注入  │
└──┬──────────────────────┬──────────────────────────────┬───────────────┘
   │ spawn (同进程)        │ exec → bash aios <name> ... │ 内置工具
   ▼                      ▼                              ▼
┌──────────────────────┐  ┌────────────────────────────┐ ┌────────────────┐
│ 内部子代理            │  │ 外部子代理                  │ │ cron / read /  │
│ (一个领域一次 spawn)  │  │ (独立子进程 + 独立 LLM 会话) │ │ web_search /   │
│                      │  │                             │ │ message / …    │
│ • steward 💰         │  │ • code_helper 🛠️           │ └────────────────┘
│   记账 + 物品库       │  │   → claude CLI              │
│ • mindscape 📚       │  │   (stream-json 子进程)      │
│   备忘 + 想读想看 +   │  │                             │
│   学习计划            │  │ 模式可复用：把 codex / cursor│
│ • toolbox 🧰         │  │ / aider 等做成同形态的桥     │
│   高德全家桶 + mini  │  │ 即可挂上去                   │
│ • wellbeing 🌅       │  └────────────────────────────┘
│   早间播报 + 习惯 +   │
│   健康指标            │
│ • life_manager 🗂️   │
│   跨域杂事编排        │
└──────────┬───────────┘
           │ 所有写操作都走 `bash aios <name> ...`
           ▼
┌────────────────────────────────────────────────────────────────────────┐
│   PostgreSQL 16 + pgvector                                             │
│   archival_memory (vector(1024) + tsvector)  +  各子代理业务表          │
│   由 deploy/run_migrations.sh 跑 aios/db/migrations/*.sql 落库           │
└────────────────────────────────────────────────────────────────────────┘
```

完整架构推理：[`docs/architecture.md`](docs/architecture.md)。
子代理契约（每个内部子代理必须遵守的"四件套"）：[`docs/agent-contract.md`](docs/agent-contract.md)。

---

## 2. 仓库结构

```
AIOS/
├── vendor/nanobot/             # git submodule → HKUDS/nanobot（或你自己 fork）
├── workspace/                  # nanobot workspace
│   ├── config.json             # provider / channel / 工具开关
│   ├── SOUL.md                 # Master 人设（本地可改）
│   ├── USER.md                 # 用户画像（Dream 自动维护）
│   ├── AGENTS.md               # 常驻 system prompt：子代理目录 + playbook
│   ├── memory/MEMORY.md        # 长期记忆种子
│   ├── skills/                 # 一个角色 / 技能一个目录
│   │   ├── pg_archive_search/  # archival_memory 向量 + 全文混合检索
│   │   ├── router/             # Tier 1 / Tier 2 路由 playbook（Master 必读）
│   │   ├── code_helper/        # 外部子代理：Claude Code via CLI
│   │   ├── life_manager/       # 内部子代理：跨域杂事编排
│   │   ├── steward/            # 内部子代理：钱 + 物品
│   │   ├── mindscape/          # 内部子代理：知识
│   │   ├── toolbox/            # 内部子代理：工具盒（高德 + mini-tools）
│   │   └── wellbeing/          # 内部子代理：早间播报 + 习惯 + 健康
│   └── agents/                 # 每个子代理的路由记忆（seed + eval）
│       ├── steward/{seed_examples,routing_eval}.jsonl
│       ├── mindscape/...
│       ├── toolbox/...
│       └── wellbeing/...
├── aios/                       # AIOS 自己的 Python 包
│   ├── cli.py                  # 唯一 CLI 入口：`aios <subcmd> ...`
│   ├── pg/                     # asyncpg + pgvector 桥
│   ├── embed.py                # SiliconFlow embedding 共用 helper（1024 维）
│   ├── route/                  # routing_traces 读写 + Tier 2 examples 拉取
│   ├── scaffold/               # `aios scaffold-agent <name>` 脚手架
│   ├── integrations/           # 外部 client 共享层（如 amap.py）
│   ├── acp/                    # 外部编码代理桥（目前：claude CLI）
│   ├── steward/                # 子代理包：db.py + cli.py
│   ├── mindscape/              #   同上
│   ├── toolbox/                #   同上（含 places 别名 db）
│   ├── wellbeing/              #   同上（含规则化穿衣建议 brief.py）
│   └── db/migrations/          # 0001-routing.sql, 0002-steward.sql, ...
├── deploy/                     # 服务器部署
│   ├── aios.service            # systemd unit
│   ├── server_setup.sh         # 一次性 bootstrap
│   ├── run_migrations.sh       # 幂等 migration runner
│   └── deploy.sh               # 本地一键 rsync + 远程重启
├── scripts/                    # DB 初始化 / 备份 / 本地启动 / 数据回填
├── tests/                      # AIOS 单元测试
├── legacy/                     # 归档的 v0.x（app/, run_ws.py, admin-web/）
├── docs/
│   ├── architecture.md
│   ├── agent-contract.md       # ← 加子代理前必读
│   ├── upgrade-from-upstream.md
│   └── evolution/
└── pyproject.toml
```

---

## 3. 子代理目录（当前已上线）

| 子代理 | 类型 | 领域 | 关键 CLI |
|---|---|---|---|
| **steward** 💰 | 内部 | 个人记账 + 家庭物品库（自然语言记账、"那个 X 在哪"、保修 / 借出跟踪） | `aios steward {expense, income, tx-list, tx-sum, report, put, where, item-list, item-move, item-update, account-*, category-*, location-*}` |
| **mindscape** 📚 | 内部 | 备忘 + 想读 / 想看清单（可选评分查询）+ 学习计划 | `aios mind {note, notes, want, watchlist, finish, drop, recall, plan-add, plan-list, plan-update}` |
| **toolbox** 🧰 | 内部 | 高德全家桶（天气 / 自驾路线 / 公交地铁 / 附近地铁 / 路况 / POI / 地理编码）+ 常用地点别名 + 计算器 / 单位换算 / 时区 | `aios toolbox {weather, route, transit, metro-near, traffic-road, poi, geo, regeo, where-add, where-list, where-rm, calc, units, tz}` |
| **wellbeing** 🌅 | 内部 | 每日早间播报（天气 + 穿衣 + 个人健康提醒）+ 习惯打卡（streak）+ 健康指标时序 | `aios wellbeing {morning-brief, habit-add, habit-done, habit-list, habit-streak, habit-pause, habit-resume, habit-archive, log, log-list, log-stats}` |
| **life_manager** 🗂️ | 内部 | 跨步骤杂事编排：复盘 / 整理 / 维护 `archival_memory` | nanobot 内置 `spawn` + `aios archive-search` |
| **code_helper** 🛠️ | **外部** | > 30 行 / 跨多文件 / 严格按步骤执行的编码任务 — 委托给 Claude Code 子进程（同模式可复用 Codex / Cursor CLI） | `aios code-helper --task <name> "<description>"` |

每个内部子代理都遵守 [`docs/agent-contract.md`](docs/agent-contract.md) 定义的**四件套契约**：

1. `workspace/skills/<name>/SKILL.md` — 领域定义 + spawn 模板 + few-shot 占位
2. `workspace/agents/<name>/{seed_examples,routing_eval}.jsonl` — 路由种子 + 评估集
3. `aios/<name>/` Python 包 + `aios <name> ...` CLI 子命令
4. `aios/db/migrations/NNNN-<name>.sql` — 幂等 schema migration

---

## 4. 本地快速开始

### 4.1 前置依赖

- macOS / Linux
- Python 3.12（用 [uv](https://docs.astral.sh/uv/) 管）
- Node.js 18+（`claude` CLI 需要，仅用 `code_helper` 时才需）
- PostgreSQL 16 + [pgvector](https://github.com/pgvector/pgvector)（库名 `aios`，见 [`scripts/init_db.sql`](scripts/init_db.sql)）
- `.env`（不入库）：
  ```
  DATABASE_URL=postgresql://<user>@localhost:5432/aios
  SILICONFLOW_API_KEY=sk-...                 # embedding 用（BAAI/bge-large-zh-v1.5）
  FEISHU_APP_ID=cli_...
  FEISHU_APP_SECRET=...
  ANTHROPIC_API_KEY=...                      # 可选：code_helper 需要
  AMAP_API_KEY=...                           # 可选：toolbox + wellbeing morning-brief 需要
  ```

### 4.2 安装

```bash
git clone <this-repo> AIOS && cd AIOS
git submodule update --init --recursive

curl -LsSf https://astral.sh/uv/install.sh | sh   # 没装 uv 的话

uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e vendor/nanobot
uv pip install -e .

# 可选：装 claude CLI 给 code_helper 用
npm install -g @anthropic-ai/claude-code

# 跑全部 DB migration（幂等，靠 schema_migrations 表跟踪）
bash deploy/run_migrations.sh
```

### 4.3 跑起来

```bash
# 一次性 smoke 测试
bash scripts/run_nanobot.sh agent -m "hello"

# 长进程网关（接飞书）
bash scripts/run_nanobot.sh gateway

# 直接命令行用 AIOS 工具
aios db-ping
aios archive-search "nanobot 集成" --embed --limit 5
aios code-helper --task hello "say hi in one line"

# 试试每个内部子代理
aios steward --help        # | aios mind --help | aios toolbox --help | aios wellbeing --help
aios wellbeing morning-brief --place "北京市朝阳区" --format plain
```

### 4.4 测试

```bash
pytest tests/
```

---

## 5. 服务器部署

### 5.1 第一次 bootstrap（在服务器上跑）

```bash
ssh root@<server>
cd /claude/aios
bash deploy/server_setup.sh
systemctl enable aios && systemctl start aios && journalctl -u aios -f
```

### 5.2 日常更新（在本地跑）

```bash
# 1. 先 push GitHub —— GitHub 是 source of truth
git push origin <branch>

# 2. dry-run 看会传哪些文件
AIOS_REMOTE=root@<server> bash deploy/deploy.sh dry

# 3. 正式部署：rsync (无 --delete) + 远端 submodule update + systemctl restart
AIOS_REMOTE=root@<server> bash deploy/deploy.sh

# 4. 在服务器上跑新增的 migration（如果有）
ssh root@<server> "cd /claude/aios && bash deploy/run_migrations.sh"
```

`deploy.sh` 排除了 `.env`、`workspace/SOUL.md`、`workspace/USER.md`、`workspace/memory/`、`workspace/sessions/`、`vendor/nanobot/.git/` 等 — 服务器上的私人人设、运行时产物、DB 内容永远不被本地推送覆盖。

详细约定（包含"自动 ssh 部署生产前必须明确确认"）见 [`CLAUDE.md`](CLAUDE.md) §8。

### 5.x 切换 Master 主模型（一行命令）

`workspace/config.json` 里 `agents.defaults.model` 写的是 `${LLM_MODEL_MAIN}`，nanobot 启动时从服务器 `.env` 读环境变量。配合 `deploy/switch-model.sh` 实现一键切换 + 预检：

```bash
# 看当前生效的模型
AIOS_REMOTE=root@<server> bash deploy/switch-model.sh --show

# 只探测某模型在 SF 是否可用（不切、不重启）
AIOS_REMOTE=root@<server> bash deploy/switch-model.sh --check deepseek-ai/DeepSeek-V4-Flash

# 真切：先 SF 探测 → 改 .env → systemctl restart aios → 验 active
AIOS_REMOTE=root@<server> bash deploy/switch-model.sh deepseek-ai/DeepSeek-V3.2
```

脚本会先对目标模型发一次真实 `chat/completions` 请求（10 s 超时）；**只有 HTTP 200 才会改配置**——避免切到拼错或 SF 还没开放的模型导致 Master 整个停摆。重启完会立刻 `systemctl is-active` 验证，不 active 直接 dump 最近 20 行日志。

---

## 6. 升级 nanobot 上游

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

完整 SOP（fork 配置、回滚、何时打 `[AIOS-PATCH]` tag）：[`docs/upgrade-from-upstream.md`](docs/upgrade-from-upstream.md)。

---

## 7. 新增一个内部子代理

> 完整规范：[`docs/agent-contract.md`](docs/agent-contract.md)。下面是 happy path —— 推荐**直接照抄 `wellbeing`** 作为完整范例。

### 7.1 生成骨架

```bash
aios scaffold-agent <name> --domain <tag> --emoji 🤖
# 例
aios scaffold-agent roster --domain contacts --emoji 👥
```

自动产出契约要求的**四件套**：

```
workspace/skills/roster/SKILL.md             # 路由 description + spawn 模板
workspace/agents/roster/seed_examples.jsonl  # 冷启动路由种子（空）
workspace/agents/roster/routing_eval.jsonl   # 评估集（空）
aios/roster/__init__.py
aios/roster/cli.py                           # 子命令骨架（含 ping）
aios/db/migrations/00NN-roster.sql           # 空 schema（编号自动 +1）
```

### 7.2 填业务逻辑

参考 [`aios/wellbeing/`](aios/wellbeing/) + [`workspace/skills/wellbeing/SKILL.md`](workspace/skills/wellbeing/SKILL.md) 这套完整实现。大致：

1. **Schema** —— 写 `aios/db/migrations/00NN-<name>.sql`。永远幂等（`CREATE TABLE IF NOT EXISTS`），表名前缀 = 子代理名，复用 `users(id)` / `VECTOR(1024)` / `tsvector` 模式。
2. **DB helpers** —— `aios/<name>/db.py` 把 `PgClient` 包成业务级 async 函数。
3. **CLI** —— `aios/<name>/cli.py` 里 `add_subparsers()` 注册每个子命令 + 一个 `dispatch()` async dispatcher。每个命令支持 `--json`。代码全在子包里，`aios/cli.py` 主文件只放一行接线（见步骤 5）。
4. **Skill** —— 把 `workspace/skills/<name>/SKILL.md` 写满：
   - frontmatter `description` 一两句话回答"这个代理是 / 不是干啥的"
   - `## 领域定义` 列 3-5 个具体场景 + 2-3 条边界（"不负责"）
   - `## Spawn Task 模板` 是一段固定 prompt，Master 会贴到 `spawn(task=...)` 里
   - `## CLI 一览` 文档化每个子命令
   - `{{ROUTING_EXAMPLES}}` 占位符运行时由 `aios route examples <name>` 注入
5. **接到主 CLI** —— [`aios/cli.py`](aios/cli.py) 加三行：
   ```python
   from aios.<name> import cli as <name>_cli
   ...
   <name>_cli.add_subparsers(sub)               # _build_parser() 里
   ...
   "<name>": <name>_cli.dispatch,                # handlers 字典里
   ```
6. **路由记忆** —— `seed_examples.jsonl` 至少 10 行、`routing_eval.jsonl` 至少 30 行。格式：
   ```jsonl
   {"query": "我体重 70.5", "routed_to": "wellbeing", "rationale": "log weight"}
   ```
7. **教 Master 知道它存在** —— [`workspace/AGENTS.md`](workspace/AGENTS.md) 的子代理表格加一行，让 Master 第一条消息就认识它（不要等 Tier 2 examples 慢慢攒出来）。

### 7.3 测试 + 上线 + 部署

```bash
# 本地
uv run python -m aios.cli <name> ping --json
bash deploy/run_migrations.sh
uv run python -m aios.cli <name> <subcmd> ...     # 挑几个命令冒个烟

# git
git add . && git commit -m "feat(<name>): ..." && git push

# 服务器
AIOS_REMOTE=root@<server> bash deploy/deploy.sh
ssh root@<server> "cd /claude/aios && bash deploy/run_migrations.sh && systemctl restart aios"
```

用几天后跑 `aios route eval --agent <name>` 看 Master 实际路由准确率。如果 seed 显然偏，去改那个 JSONL 和 SKILL.md 的 `## 领域定义`（这俩是 Tier 1 路由唯一可调的把手）。

---

## 8. 接入另一个外部编码 agent（Codex / Cursor CLI / …）

内部子代理共享 Master 的 LLM、session 和进程。当你需要一个**完全隔离**的代理 — 自己的 LLM provider、自己的子进程、自己的 session log — 走**外部子代理**模式。AIOS 已经在生产里接了一个（`code_helper` → `claude` CLI），同样的套路适用于任何 CLI 形态的编码 agent。

### 8.1 模式（3 层）

```
┌──────────────────────────────────────────────────────────────────────┐
│ workspace/skills/<bridge>/SKILL.md                                  │
│   • 何时用（Master 决策规则）                                         │
│   • CLI 调用约定：`aios <bridge> --task <name> "<desc>"`              │
│   • 任务名延续规则（同名 = 同一个外部 session）                        │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼────────────────────────────────────┐
│ aios/cli.py 子命令 → aios/<bridge>/                                  │
│   delegate_to_<external_cli>(task, description, timeout) -> Result   │
│   • 启动外部 CLI 用 stream-json（或它支持的任何 NDJSON 格式）增量解析   │
│   • 按任务名持久化 session ID 到 <workspace>/<bridge>/...             │
│   • 返回 final_text + tool_calls + cost_usd + error                  │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼────────────────────────────────────┐
│ 外部编码代理（独立 LLM、独立进程）                                     │
│   claude / codex / cursor-cli / aider / ...                          │
└──────────────────────────────────────────────────────────────────────┘
```

两条不能让步的红线：

- **`--task <name>` 是 sticky 的**。同名 → 续上外部 session（保留多轮记忆）；不同名 → 全新 session。Master 必须从 SKILL.md 学会这条规则，并且在每次回复末尾输出 `📎 task: <name>`，下一轮自己抄回来。
- **Master 永远负责渲染结果**。桥返回结构化 JSON；SKILL.md 教 Master 把 `tool_calls` 折叠成单行进度（`🔧 Write hello.py`）+ 把 `final_text` 呈现给用户 —— 永远不要把原始 JSON 直接丢给用户。

### 8.2 参考实现：`code_helper` → Claude Code

| 层 | 文件 | 内容 |
|---|---|---|
| Skill（何时 + 怎么用） | [`workspace/skills/code_helper/SKILL.md`](workspace/skills/code_helper/SKILL.md) | 决策规则、任务名正则、JSON 输出 schema、错误对照表 |
| CLI 子命令 | [`aios/cli.py`](aios/cli.py) (`_cmd_code_helper`) | argparse + `delegate_to_claude` 调用 |
| 桥包 | [`aios/acp/`](aios/acp/) | `client.py` 启动 `claude --output-format stream-json --resume <session_id>`，按 NDJSON 增量解析 |
| 任务级状态 | `~/aios-cc-workspace/<task>/` | 每个任务一个子目录，里面存 `session_id` 给 `--resume` 用 |

### 8.3 三步加一个新桥

例：把 **Codex CLI** 接成第二个外部编码 agent：

1. **照抄桥包**到 `aios/codex/`，shape 跟 `aios/acp/` 一致：
   - `client.py` 暴露 `delegate_to_codex(task, description, timeout) -> CodexResult`
   - 用 Codex 提供的任何流式格式（NDJSON / SSE / 仅最终输出）；公共 dataclass 跟 `CodeHelperResult` 保持一致，这样 SKILL.md 模板能最小修改照抄。
   - 按任务名持久化 session 续连。
2. **注册 CLI 子命令**到 `aios/cli.py`：
   ```python
   p_codex = sub.add_parser("codex", help="Delegate a coding task to codex CLI")
   p_codex.add_argument("--task")
   p_codex.add_argument("description", nargs="?")
   p_codex.add_argument("--timeout", type=int, default=None)
   p_codex.add_argument("--json", action="store_true")
   ...
   "codex": _cmd_codex,
   ```
3. **写 skill**：`workspace/skills/codex/SKILL.md` —— 照抄 `code_helper/SKILL.md`，编辑：
   - 何时选 Codex 而不是 Claude Code（模型偏好、成本、能力差异）
   - CLI 调用约定完全一致：`aios codex --task <name> "<desc>" --json`
   - 把 Codex 加到 `workspace/AGENTS.md` 的子代理目录，让 Master 第一条消息就看见

完事 —— Master 从 Tier 1（SKILL description）开始路由，自动收集 Tier 2 examples。同样的菜谱适用于 Cursor CLI、aider、Continue 或任何支持 session 续连的 CLI 编码 agent。

---

## 9. 个性化你的 Master

仓库自带的 [`workspace/SOUL.md`](workspace/SOUL.md) 是**通用模板**（人设叫 "Master"）。要给你自己的 Master 起名字、改语气或加私人偏好：

```bash
# 1. 随便改（改名、改语气、加私人指令都行）
vim workspace/SOUL.md
vim workspace/USER.md
vim workspace/memory/MEMORY.md

# 2. 让 git 忽略你的本地修改 —— `git status` 永远干净，永远不会误把私人人设推到公网仓库
git update-index --skip-worktree workspace/SOUL.md workspace/USER.md workspace/memory/MEMORY.md

# 3. 本地 / 服务器照常用 —— nanobot 读你的本地版本
```

恢复跟踪（比如想拉模板更新时）：

```bash
git update-index --no-skip-worktree workspace/SOUL.md workspace/USER.md
```

`deploy.sh` 已经**排除**了 `workspace/SOUL.md` / `workspace/USER.md` / `workspace/memory/MEMORY.md`，服务器上的私人人设永远不会被本地推送覆盖。

---

## 10. 文档地图

| 文档 | 用途 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 系统架构、Master ↔ 子代理模型、数据布局 |
| [`docs/agent-contract.md`](docs/agent-contract.md) | **加内部子代理前必读** —— 四件套契约、路由分层、脚手架流程 |
| [`docs/upgrade-from-upstream.md`](docs/upgrade-from-upstream.md) | nanobot 上游升级 SOP（fork、rebase、回滚） |
| [`docs/evolution/phases/phase-fractal-rewrite.md`](docs/evolution/phases/phase-fractal-rewrite.md) | 这次重写的设计决策与历史 |
| [`workspace/AGENTS.md`](workspace/AGENTS.md) | Master 常驻 system prompt —— 子代理目录 + per-agent playbook |
| [`workspace/SOUL.md`](workspace/SOUL.md) | Master 人设（本地可改） |
| [`workspace/skills/router/SKILL.md`](workspace/skills/router/SKILL.md) | Tier 1 / Tier 2 路由 playbook |
| [`workspace/skills/code_helper/SKILL.md`](workspace/skills/code_helper/SKILL.md) | 外部子代理参考（Claude CLI）—— 加 Codex / Cursor CLI 时照抄这份 |
| [`workspace/skills/wellbeing/SKILL.md`](workspace/skills/wellbeing/SKILL.md) | 内部子代理参考（最近一个建的）—— 起新的领域子代理时照抄这份 |
| [`workspace/skills/pg_archive_search/SKILL.md`](workspace/skills/pg_archive_search/SKILL.md) | 长期记忆混合检索 |
| [`legacy/README.md`](legacy/README.md) | 归档的 v0.x 代码说明 |
| [`CLAUDE.md`](CLAUDE.md) | AI 编码约定（部署纪律、密钥处理） |

---

## 11. License

MIT
