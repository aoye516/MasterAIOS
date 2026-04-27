# AIOS 系统架构（Fractal Nanobot Edition）

**版本**：v1.0.0-fractal
**最后更新**：2026-04-22
**维护说明**：架构变更必须同步更新本文档（参考 [`CLAUDE.md`](../CLAUDE.md) 第 2.1 节规则）

> 旧的 v0.x 自研架构已归档到 [`legacy/`](../legacy/)。本文档只描述当前在跑的 Fractal Nanobot 架构。

---

## 1. 设计原则

1. **Vendor 优先**：[HKUDS/nanobot](https://github.com/HKUDS/nanobot) 是 git submodule（`vendor/nanobot/`），不动 vendor 代码，能用配置/skill 解决就不写代码。
2. **AIOS = Workspace + Skills + 差异层**：自己只写 nanobot workspace、自定义 skills、PG 桥、Claude CLI 桥、部署。
3. **可迭代性是 SLA**：上游每周发版，AIOS 必须能在 1 小时内 rebase + 上线（详见 [`upgrade-from-upstream.md`](upgrade-from-upstream.md)）。
4. **分形协作**：Master 不是单体，能 `spawn` 内部 sub-agent（同进程）也能调外部 sub-agent（独立子进程 + 独立 LLM）。两种模式可嵌套。
5. **统一对外口径**：所有发给用户的话只能从 Master 出。子 sub-agent 只汇报数据/建议，不直接发飞书。

---

## 2. 物理拓扑

```
┌─────────────────────────────────────────────────────────────────────┐
│                            用户端                                    │
│                  飞书 App / 飞书 Web                                 │
└────────────────────────────┬────────────────────────────────────────┘
                             │ WebSocket (lark-oapi)
┌────────────────────────────▼────────────────────────────────────────┐
│                    生产服务器 (your-server-ip)                       │
│  systemd: aios.service                                              │
│  process: nanobot gateway -c workspace/config.json -w workspace/    │
│                                                                     │
│  ├─ nanobot Master Agent loop (in-process)                          │
│  ├─ subagent manager (in-process background tasks)                  │
│  ├─ exec tool → bash → aios CLI (subprocess)                        │
│  │     ├─ aios archive-search  → asyncpg → PostgreSQL               │
│  │     ├─ aios code-helper     → claude CLI (subprocess)            │
│  │     └─ aios db-ping                                              │
│  └─ Feishu channel (lark-oapi long polling)                         │
│                                                                     │
│  data:                                                              │
│  ├─ /claude/aios/workspace/sessions/*.jsonl  (nanobot sessions)     │
│  ├─ /claude/aios/workspace/memory/MEMORY.md  (Dream long-term)      │
│  └─ /claude/aios/.venv/                                             │
│                                                                     │
│  PostgreSQL 16 + pgvector (本机，db: aios)                          │
│  ├─ archival_memory (vector(1024) + tsvector)                       │
│  └─ 其他业务表                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 逻辑分层

```
┌─────────────────────────────────────────────────────────────────────┐
│ Channels                                                             │
│  nanobot 内置 Feishu channel（lark-oapi WS）                         │
│  workspace/config.json: channels.feishu.{appId,appSecret,...}       │
└────────────────────┬────────────────────────────────────────────────┘
                     │ inbound message
┌────────────────────▼────────────────────────────────────────────────┐
│ Master Agent  (= nanobot Agent loop + AIOS persona)                 │
│  • SOUL.md         决策原则 + Fractal 路由规则                       │
│  • USER.md         用户画像（Dream 自动维护）                         │
│  • MEMORY.md       长期事实（Dream 二阶段写入）                       │
│  • config.json     provider / model / 工具开关                       │
│                                                                     │
│  内置工具：read / write / edit / glob / grep / exec /                │
│           web_search / web_fetch / cron / message / spawn            │
└──────┬──────────────────────┬──────────────────────────┬────────────┘
       │ spawn(task=..)       │ exec("aios ...")         │ cron / read
       ▼                      ▼                          ▼
┌──────────────────┐  ┌─────────────────────┐  ┌──────────────────────┐
│ 内部 sub-agent    │  │ 外部 sub-agent      │  │ AIOS 工具薄层         │
│ (Spawn/LifeMgr)  │  │ (Claude Code)       │  │ workspace/skills/*    │
│ ─────────────    │  │ ─────────────       │  │ ─────────────         │
│ 同进程后台 task   │  │ 独立子进程 (claude) │  │ pg_archive_search    │
│ 同 provider/model│  │ 独立 LLM            │  │ code_helper           │
│ 拿全套内置工具    │  │ session.jsonl 续接   │  │ life_manager 模板     │
│ 不能发用户消息    │  │ 不能发用户消息       │  │                       │
└──────────────────┘  └─────────────────────┘  └──────┬───────────────┘
                                                      │
                                                      ▼
                              ┌─────────────────────────────────────┐
                              │ aios/ Python 包                     │
                              │ ─────────────────────────────────── │
                              │ aios.pg     asyncpg + pgvector      │
                              │ aios.acp    claude CLI 封装         │
                              │ aios.cli    `aios <subcommand>` 入口│
                              └────────────────┬────────────────────┘
                                               ▼
                              ┌─────────────────────────────────────┐
                              │ PostgreSQL 16 + pgvector            │
                              │ archival_memory(vector(1024) + ts)  │
                              └─────────────────────────────────────┘
```

---

## 4. 关键流程

### 4.1 用户单步问答（master 直接回）

```
用户飞书"几点了" 
  → Feishu channel
  → Master Agent loop
  → LLM 判断：单步、不需要工具
  → 直接 reply
  → Feishu outbound
```

### 4.2 多步生活管理（spawn 内部 sub-agent）

```
用户"梳理我下周的所有日程，标出冲突"
  → Master 按 SOUL.md 决策树识别为「梳理/整理 ≥3 步」
  → 调 spawn(task=<life_manager 模板 + 用户原话>, label="LifeManager: ...")
  → SubagentManager 在后台开 task，分配同 provider/model + 全套内置工具
  → sub-agent 自主：
       - exec "aios archive-search '日程' --limit 5"
       - 整理冲突
       - 输出结构化总结 + 推荐 cron 操作
  → 返回 final 给 Master
  → Master 把结果汇总成「人话」，加入 SessionContext，回飞书
```

### 4.3 编码任务（外部 sub-agent）

```
用户"帮我写个把 archival_memory 导出成 CSV 的脚本"
  → Master 识别为「写代码」 → 调 `aios code-helper --task export-csv "<desc>"`
  → aios.acp.client.delegate_to_claude() spawn:
       claude -p "<desc>" --output-format stream-json --resume <prev?>
  → 收 stream-json 增量，把 final 文本和 session_id 返回
  → Master 把代码片段 + 后续 cmd 提示写回飞书
  → 后续多轮：用同一个 task name，acp 自动 --resume 继承 session
```

### 4.4 Cron 提醒（nanobot 原生）

```
master 用 cron 工具：cron(action="add", message="提醒喝水", at="2026-04-22T15:00:00+08:00")
  → nanobot CronService 起后台调度
  → 到点把 message 注入 master 当前 session
  → master LLM 基于 SOUL.md / 上下文加工成飞书消息发出
```

---

## 5. 数据模型

### 5.1 PostgreSQL（沿用，未变）

```sql
-- archival_memory: 长期外部知识库
CREATE TABLE archival_memory (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255),
    content TEXT NOT NULL,
    embedding vector(1024),                -- BGE-M3 / Qwen embedding
    metadata JSONB DEFAULT '{}',
    tsv tsvector,                          -- 全文索引（中英文）
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON archival_memory USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON archival_memory USING gin(tsv);
```

`aios.pg.archival.search_archival(query, k=5)` 实现 vector + tsvector 的 hybrid 检索（先 vector top-N，再 tsvector rerank，详见 [`aios/pg/archival.py`](../aios/pg/archival.py)）。

### 5.2 文件状态（nanobot 自己管）

| 路径 | 用途 | 谁写 |
|---|---|---|
| `workspace/sessions/*.jsonl` | 单次对话流水 | nanobot |
| `workspace/memory/MEMORY.md` | Dream 二阶段写入的长期事实 | nanobot Dream |
| `workspace/USER.md` | 用户画像 | nanobot Dream |
| `workspace/.cache/`、`workspace/.runtime/` | 临时缓存 | nanobot |

> PG 与文件并存：PG 作为 master 显式调用的「外部知识库」（通过 `pg_archive_search` skill），文件 memory 作为 nanobot 自动维护的短期/中期会话记忆。MVP 不做强制统一。

---

## 6. 工具清单

### 6.1 nanobot 内置（master 自动获得）

| 工具 | 用途 |
|---|---|
| `read / write / edit` | workspace 内文件 IO |
| `glob / grep` | workspace 内文件查找 |
| `notebook_edit` | jupyter notebook 编辑 |
| `exec` | 在沙箱内执行命令（pathAppend 配 `${AIOS_PATH_APPEND}`） |
| `web_search / web_fetch` | DuckDuckGo / 抓页 |
| `cron` | 自然语言定时 |
| `message` | 给用户发消息（master 专用） |
| `spawn` | 起内部 sub-agent |

### 6.2 AIOS 自定义（通过 `exec` 调 `aios` CLI）

| 子命令 | 用途 |
|---|---|
| `aios db-ping` | 连通性检查 |
| `aios archive-search "<q>" [--limit N] [--json]` | 长期记忆混合检索 |
| `aios code-helper --task <name> "<desc>" [--json]` | 调外部 Claude Code |

CLI 实现见 [`aios/cli.py`](../aios/cli.py)。每个子命令有对应 `workspace/skills/<name>/SKILL.md` 教 LLM 何时/怎么用。

---

## 7. 配置约定

`workspace/config.json`：

- `agents.defaults.provider` — 默认 LLM（当前 `siliconflow` + `deepseek-ai/DeepSeek-V3.2`）
- `agents.defaults.timezone` — `Asia/Shanghai`
- `agents.defaults.unifiedSession` — `true`（所有 channel 共享一个 session）
- `channels.feishu` — appId/appSecret 走 `${ENV_VAR}` 占位
- `channels.feishu.allowFrom: ["*"]` — 不再做 ACL（依赖飞书侧群权限）
- `tools.exec.pathAppend: "${AIOS_PATH_APPEND}"` — 让子进程能找到 venv 里的 `aios` / `claude`
- `tools.exec.allowedEnvKeys` — 白名单：`DATABASE_URL / SILICONFLOW_API_KEY / ANTHROPIC_API_KEY / AIOS_HOME / PATH / VIRTUAL_ENV / PYTHONPATH ...`
- `tools.restrictToWorkspace: false` — 允许 `aios` CLI 访问 PG / 调 claude

`.env`（不入库）：`DATABASE_URL` / `SILICONFLOW_API_KEY` / `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `ANTHROPIC_API_KEY`。

---

## 8. 部署单元

```
deploy/
├── aios.service       systemd unit, ExecStart=/claude/aios/.venv/bin/nanobot gateway ...
├── server_setup.sh    一次性：装 uv / Node / claude / venv / submodule / systemd unit
└── deploy.sh          本地一键：rsync (no --delete) + remote git submodule update + restart
```

部署纪律见 [`CLAUDE.md`](../CLAUDE.md) 第 8 节（禁止 `rsync --delete`、复杂任务先确认、每次先备份 PG）。

---

## 9. 与旧架构的对应

| v0.x | v1.0-fractal |
|---|---|
| `app/agents/master.py` | nanobot Agent loop + `workspace/SOUL.md` |
| `app/agents/life_manager.py` | nanobot `spawn` + `workspace/skills/life_manager/SKILL.md` |
| `app/agents/health_center.py` | （暂未迁移，按需做成 spawn 模板） |
| `app/channels/feishu_ws.py` | nanobot 内置 feishu channel |
| `app/services/llm.py` | nanobot provider abstraction (siliconflow/...) |
| `app/services/memory.py` | nanobot Dream 二阶段 + `aios.pg.archival` 旁路 |
| `app/services/scheduler.py` | nanobot 内置 cron |
| `app/services/notification.py` | nanobot Feishu channel outbound |
| `app/services/context.py` | nanobot SessionContext (`workspace/sessions/*.jsonl`) |
| `app/api/` (FastAPI Admin) | 暂未重做（用户决策） |
| `admin-web/` | 暂未重做（用户决策） |
| `run_ws.py` | `nanobot gateway` (via `aios.service`) |

详细决策与历史见 [`docs/evolution/phases/phase-fractal-rewrite.md`](evolution/phases/phase-fractal-rewrite.md)。

---

## 10. 扩展指南

**加一个新的「内部 sub-agent 角色」**：在 `workspace/skills/<role>/SKILL.md` 写角色模板（参考 [`life_manager/SKILL.md`](../workspace/skills/life_manager/SKILL.md)），并在 `SOUL.md` 决策树补一条触发规则。Master 用 `spawn` 时把模板拼进 `task` 文本即可。

**加一个新的 AIOS 工具**：

1. 在 `aios/<module>/` 写 Python 实现
2. 在 `aios/cli.py` 注册 `argparse` 子命令
3. 在 `workspace/skills/<tool>/SKILL.md` 写一段「何时调 + 怎么调」的 markdown
4. nanobot 启动时自动 pick up 新 skill，无需重启逻辑

**加一个新的 channel**：优先看 nanobot 是否已支持（微信、Telegram、QQ 等都有）；不行再走 `vendor/nanobot` 升级或 PR 上游。**不要**在 AIOS 写 channel 实现。

---

## 11. ADR 记录

| ID | 决策 | 时间 | 链接 |
|---|---|---|---|
| ADR-001 | 采用 Fork + Vendor 模式接入 nanobot | 2026-04-22 | [phase-fractal-rewrite.md](evolution/phases/phase-fractal-rewrite.md) |
| ADR-002 | 用 `claude` CLI 的 stream-json 替代 `claude-as-acp` wrapper | 2026-04-22 | [phase-fractal-rewrite.md §4](evolution/phases/phase-fractal-rewrite.md) |
| ADR-003 | 用 nanobot 内置 cron 替代自研 schedule/reminder skill | 2026-04-22 | 本文档 §6.1 |
| ADR-004 | 暂不重做 Admin Web，用户后续按需启动 | 2026-04-22 | 本文档 §9 |
