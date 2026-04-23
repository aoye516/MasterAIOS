# Phase: Fractal Nanobot Rewrite (Fork + Vendor)

> 起始日期：2026-04-22
> 完成日期：2026-04-22
> 状态：已完成（v1.0.0-fractal）
> 目标：把 AIOS 从「自研 Master + Agent + Channel + Memory + Scheduler」全栈，切换到「以 nanobot-ai 为内核，AIOS 只写差异化 skills + 部署」的形态，确保上游升级可在小时级完成。

---

## 1. 背景与动机

`v0.x` 系列的 AIOS 自研了完整的 agent 框架：
- `app/agents/`：Master + LifeManager + HealthCenter
- `app/channels/`：Feishu WS / Long polling
- `app/services/`：LLM / Memory / Context / Scheduler / Notification / FileMemory
- `app/api/`：Admin REST API
- `admin-web/`：Next.js 后台

迭代过程中暴露出几个核心痛点：

1. **重复造轮子**：HKUDS/nanobot 已经实现了同样的 agent loop / 多 provider / 多 channel / Cron / MCP / subagent / Dream 两阶段记忆，且每周都在快速发版。我们自研的版本能力上落后。
2. **维护成本高**：通道、provider 适配、记忆机制、tool calling 都要自己跟主流 LLM 协议变化。
3. **Master ↔ Sub-Agent 模型不够干净**：现有 Master 直接调子 Agent 的 Python 方法，与「内部 agent + 外部 agent」的分形愿景脱节。
4. **可迭代性弱**：上游有新能力（例如 Dream 记忆、ClawHub skill 市场、SSE OpenAI 兼容 API），我们要花数天移植。

## 2. 目标架构

> 详细图见 [`fractal_nanobot_rewrite` plan 文件](../../../.cursor/plans/fractal_nanobot_rewrite_aea02292.plan.md)

核心理念：

1. **Vendor 优先**：`vendor/nanobot/` 是 git submodule，指向自有 fork（`aoye516/nanobot`），upstream 指向 `HKUDS/nanobot`。能用配置/skill 解决的事，绝不动 vendor 代码。
2. **AIOS = Workspace + Skills + 差异层**：
   - `workspace/` — nanobot 工作区（config.json + skills/）
   - `aios/` — Python 包，被 skills 调用（PG 桥接、ACP 客户端、persona）
   - `deploy/` — systemd unit + 部署脚本
   - `legacy/` — 旧 `v0.x` 代码原样备份，仅用于回顾
3. **可迭代性是 SLA**：上游每次发版，能在 1 小时内 rebase + 跑测 + 上线。

## 3. 范围

### 沿用（不动）
- PostgreSQL schema (`scripts/init_db.sql`，已对齐生产)
- `config/persona.md`（小丙人设）
- `.env`（DATABASE_URL / SILICONFLOW_API_KEY / FEISHU_*）
- `scripts/`（DB init / backup / restore 脚本）

### 改用 nanobot 原生
- Agent loop、Tool calling
- Feishu channel（lark-oapi）
- LLM provider（siliconflow / deepseek）
- Cron 调度
- Session 持久化（`workspace/sessions/*.jsonl`）
- 短期记忆 / Dream 两阶段记忆

### 新写（差异化层）
- `aios/pg/`：asyncpg + pgvector 操作薄层
- `aios/acp/`：外部代码助手客户端封装（**实际实现：直接 spawn `claude` CLI 的 stream-json 模式**，因为 `claude-as-acp` wrapper 是 OpenClaw 内部工具，路径硬编码 `/root/...` 且需 AWS Bedrock 凭证，无法在我们环境运行）
- `workspace/skills/pg_archive_search/`：现有 1024 维 archival_memory 暴露成检索工具
- `workspace/skills/code_helper/`：调 `claude` CLI 子进程的 skill
- `workspace/skills/schedule/` + `reminder/`（视 nanobot Cron 能力决定保留与否）

### 整体废弃
- 旧 `app/`、`run_ws.py`、`admin-web/`（已归档至 `legacy/`）
- 暂不重做 Web Admin（用户决策）

## 4. 关于 ACP 集成的关键调整

> 原 plan 提到通过 `claude-as-acp` wrapper spawn 外部 ACP 子进程。

实地核查 `/tmp/acp_skill/` 后发现：
- `claude-as-acp` wrapper 来自 OpenClaw 生态，路径硬编码 `/root/.openclaw/...` 与 `/root/.aws/credentials`
- 依赖 AWS Bedrock 账号 + `[claude-profile]` AWS profile
- 不能直接在 macOS 本地或我们的服务器（无 AWS）运行

**调整方案**：Code Helper skill 直接 spawn `claude` CLI（已装 `/opt/homebrew/bin/claude`），用 `--output-format stream-json` 拿增量事件，用 `--continue` / `--resume <session_id>` 续接多轮对话。能力上等价于 wrapper（task 名 ↔ session 续接），但零外部依赖。

后续如果想接入真正的 ACP 协议（用于多 ACP 客户端互通），可以装公开包 `@agentclientprotocol/claude-agent-acp@0.27.0`，但 MVP 阶段不必要。

## 5. Phase 进度

### Phase 0：仓库准备 ✅
- [x] 旧代码归档：`app/` → `legacy/app/`，`run_ws.py` → `legacy/run_ws.py`，`admin-web/` → `legacy/admin-web/`，相关旧测试 → `legacy/tests/`
- [x] 新目录骨架：`vendor/`, `workspace/{,/skills,/memory}`, `aios/{pg,acp}`, `deploy/`, `tests/unit`
- [x] `.gitignore` 增加 `workspace/sessions/`、`workspace/memory/`、`vendor/nanobot/build|dist|*.egg-info`、`.venv/`
- [x] `git submodule add` `vendor/nanobot` 指向 upstream（按需 fork）
- [x] `uv venv .venv --python 3.12` + `uv pip install -e vendor/nanobot && uv pip install -e .`
- [x] `nanobot --version` 跑通
- [x] 本地 `claude -p ... --output-format stream-json` 冒烟（直接用 CLI，弃用 `claude-as-acp` wrapper）

### Phase 1：workspace 骨架 + 飞书冒烟 ✅
- [x] `workspace/config.json`（siliconflow + DeepSeek-V3.2 + feishu channel）
- [x] `workspace/SOUL.md`、`USER.md`、`memory/MEMORY.md` 三个 persona/状态文件
- [x] `scripts/run_nanobot.sh` 本地 launcher（含 `.env` 健壮加载 + venv 激活 + `AIOS_HOME`/`AIOS_PATH_APPEND` 注入）
- [x] 修了 `feishu.allowFrom: ["*"]` 后飞书 WS 通道连上
- [x] 单条消息冒烟通过（`bash scripts/run_nanobot.sh agent -m "你好"`）

### Phase 2：AIOS 自定义 skills ✅
- [x] `aios/pg/client.py`（asyncpg 薄封装 + DSN 解析）
- [x] `aios/pg/archival.py`（vector + tsvector hybrid 检索）
- [x] `aios/acp/client.py`（`claude` CLI 子进程 + stream-json + `--resume`）
- [x] `aios/cli.py` 统一入口（`archive-search` / `code-helper` / `db-ping`）
- [x] `workspace/skills/pg_archive_search/SKILL.md`
- [x] `workspace/skills/code_helper/SKILL.md`
- [x] **删除**自研 `schedule` / `reminder` skill —— 用 nanobot 内置 cron（ADR-003）
- [x] `tests/unit/` 19 个单测全绿
- [x] E2E：master 通过 `exec` 调用 `aios db-ping` 成功并人话化复述

### Phase 3：Master ↔ Sub-Agent 编排 ✅
- [x] 验证 nanobot 原生 `spawn` 工具默认注册（`vendor/nanobot/nanobot/agent/loop.py:291`）
- [x] `workspace/skills/life_manager/SKILL.md` 写「内部 sub-agent 角色模板」
- [x] `workspace/SOUL.md` 增「Fractal 决策原则」一节，定义 master ↔ 内部 sub-agent ↔ 外部 sub-agent 的边界
- [x] E2E：master 直接调 `spawn(...)` 返回 task id 成功（验证 spawn 链路通）
- [x] E2E：master 按 SOUL 决策树识别多步任务并选择 spawn LifeManager（推理路径正确）

### Phase 4：服务器一键体验 ✅
- [x] `deploy/aios.service`（systemd unit，跑 `nanobot gateway`，含 PATH/AIOS_HOME/AIOS_PATH_APPEND 注入与安全收紧）
- [x] `deploy/server_setup.sh`（一次性：apt git/curl/Node22 + npm i `claude` + 装 uv + git submodule update + venv + editable installs + 装 systemd unit，**不自动 start**）
- [x] `deploy/deploy.sh`（本地一键：rsync 无 `--delete` + 远程 `git submodule update + uv pip install -e` + `systemctl restart aios`，含 dry-run 模式）
- [x] 与旧 `aios-ws.service` 并存策略写在 setup 脚本提示里

### Phase 5：文档收尾 ✅
- [x] `docs/upgrade-from-upstream.md`（含 fork 配置、日常 SOP、应急回滚、何时打 `[AIOS-PATCH]`）
- [x] 重写 `README.md`（Fractal 视角的快速开始 + 部署 + 升级）
- [x] 重写 `docs/architecture.md`（v1.0.0-fractal，含 ADR 表）
- [x] `legacy/README.md` 标记废弃 + 旧 → 新模块对照
- [x] 本文档（phase-fractal-rewrite.md）状态更新为「已完成」

## 6. 风险记录

| 风险 | 缓解 |
|---|---|
| nanobot 原生 subagent 表达力不足 | Phase 3 验证；不够则用嵌套 Nanobot 实例方案 |
| PG 记忆 vs nanobot 文件 memory 重复 | MVP 不强行统一，PG 作为「外部知识库 skill」，文件 memory 跑短期 |
| vendor/nanobot 必须改的情况 | 在 fork commit，`[AIOS-PATCH]` 前缀，并优先 PR 上游 |
| `claude` CLI 行为升级破坏 stream-json | `aios/acp/client.py` 写断言 + 单测覆盖关键事件类型 |
| 部署回滚 | 新 `aios.service` 与旧 `aios-ws.service` 并存 |

## 7. 上游升级 SOP

详见 `docs/upgrade-from-upstream.md`（Phase 5 写）。核心命令：

```bash
cd vendor/nanobot
git fetch upstream
git rebase upstream/main
cd ../..
uv pip install -e vendor/nanobot
pytest tests/
bash deploy/deploy.sh
```

铁律：vendor 不写业务代码，要改先 PR 上游。
