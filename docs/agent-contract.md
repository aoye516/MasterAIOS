# AIOS Sub-Agent Contract

> 版本：v1（Sprint 0）  
> 适用：所有跑在 nanobot Master 之下的"子代理"（steward / mindscape / wellbeing / toolbox / roster …）

## 1. 子代理在 nanobot 里到底是什么

**关键事实**：nanobot 的 `spawn` 工具不是按 "agent name" 路由的 —— 它启动一个**通用 subagent**，subagent 的"身份"完全由 Master 在 `task` prompt 里的角色描述 + 工具/skill 注入决定。

所以 AIOS 里所谓"子代理"不是 nanobot 配置层的 agent，而是一个由四件套组成的**约定**：

```
workspace/skills/<name>/SKILL.md          ← 路由 description + spawn 模板
workspace/agents/<name>/seed_examples.jsonl ← Tier 2 路由冷启动种子
aios <name> ...                            ← CLI 子命令（实际工具实现）
aios/db/migrations/<name>.sql              ← 数据 schema
```

Master 通过 SKILL.md 的 description 决定路由，通过 spawn task 模板赋予角色，通过 bash 工具调 `aios <name> ...` 完成动作。

---

## 2. 四件套规范

### 2.1 `workspace/skills/<name>/SKILL.md`

**frontmatter 字段**：

```yaml
---
name: <agent-name>                     # kebab-case，跟 CLI 子命令同名
description: |
  <两三句话写"领域定义 + 边界"，不写 examples>
  典型场景由运行时从 routing_traces 拼接注入（占位符 {{ROUTING_EXAMPLES}}）
metadata:
  nanobot:
    emoji: "🏷️"                        # 显示用
    requires:
      bins: ["aios"]                   # 需要的可执行
domain: <短领域标签>                   # finance / knowledge / wellbeing / tools / contacts
---
```

**正文必须包含的章节**：

```markdown
# <Agent Name>

## 领域定义
<这个子代理负责什么 / 不负责什么>

## Spawn Task 模板
> Master 用 spawn 调用本子代理时，task 文本应该包含本段内容。
（一段固定的角色边界 prompt，让 spawn 出来的 subagent 知道自己是这个领域的专家）

## CLI 一览
- `aios <name> <subcmd> ...`：每个子命令一句话说明
- 强约定：所有副作用动作（写库 / 调外部 API）必须通过 CLI，禁止 spawn subagent 自己直连 PG

## Few-shot 示例（占位）
{{ROUTING_EXAMPLES}}    # 运行时由 aios route examples <name> --top 8 填充
```

### 2.2 `workspace/agents/<name>/seed_examples.jsonl`

**冷启动种子**：每行一个 JSON，10 条左右。Sprint 0 阶段手写，跑一周后自动被 routing_traces 接管。

```jsonl
{"query": "今天午饭花了 38 块支付宝", "expected": "steward", "note": "记账场景"}
{"query": "我护照在哪", "expected": "steward", "note": "物品查询"}
{"query": "上个月外卖花了多少", "expected": "steward", "note": "聚合查询"}
```

字段：
- `query` — 用户原话
- `expected` — 应该路由到的子代理名
- `note` — 写给人看的标签（不进 prompt）

`aios route examples` 在 `routing_traces` 行数 < 50 时自动 fallback 到 seed。

### 2.3 `aios <name> ...` CLI 子命令

**强约定**：

1. **每个写操作走 CLI** —— spawn 出来的 subagent 不能直连 PG，必须 `bash aios <name> <action> ...`，这样才能在一处统一记日志、采 routing trace、做事务边界
2. **每个 CLI 都支持 `--json`** —— 让 LLM 拿到结构化输出
3. **错误走非零退出码** —— LLM 能从 stderr 拿到失败原因
4. **代码放 `aios/<name>/`** 子包 —— 命令实现 + 业务逻辑 + 单测，不要散落在 `aios/cli.py` 主文件里

**注册方式**：在 `aios/cli.py` 的 `_build_parser()` 加一个 sub-parser，handler 函数从 `aios.<name>.cli` import。

### 2.4 `aios/db/migrations/<name>.sql`

**强约定**：

1. **每个子代理一个 .sql 文件**，幂等（`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`）
2. **表名前缀** = 子代理名（避免命名冲突）—— 例：`steward_accounts`、`mindscape_watchlist`
3. **复用既有基础设施**：用户主键引用 `users(id)`、向量字段统一用 `VECTOR(1024)`、tsvector 字段统一叫 `content_tsvector`
4. **migration 顺序**：脚手架生成的 migration 编号自动 + 1（`0001-routing.sql` / `0002-steward.sql` / ...）

跑 migration 用 `bash deploy/run_migrations.sh`（Sprint 0 一并提供）。

---

## 3. 路由层契约（Tier 1 + Tier 2）

### 3.1 Tier 1 — LLM 原生

Master 启动时拉所有 `workspace/skills/*/SKILL.md`，把 frontmatter 的 `description` + 正文的"领域定义"段拼成 system prompt。
LLM 自己看用户消息决定 spawn 哪个角色。

### 3.2 Tier 2 — 自演化 routing_traces

**采集**（Master 每轮自动）：

每次 spawn 后写一条 trace：
```sql
INSERT INTO routing_traces (
    query, query_embedding, routed_to, confidence, outcome, created_at
) VALUES (...);
```

`outcome` 字段在子代理完成后由 Master LLM-as-judge 自评：
- `success` — 路由对了，子代理给出有效回应
- `reroute` — 路由错了，Master 接到子代理结果后又 spawn 了另一个
- `failed` — 子代理没能完成

`user_feedback` 字段从飞书 reaction emoji 异步回灌（👍/👎）。

**回流**（每个子代理被路由前）：

CLI: `aios route examples <agent-name> --top 8 --recent-days 30 --min-confidence 0.7`

返回 8 条 outcome=success 且高 confidence 的近期 query 文本，Master 在拼接子代理 description 时把这些填入 `{{ROUTING_EXAMPLES}}` 占位符。

**冷启动 fallback**：如果该子代理 `routing_traces` 行数 < 50，CLI 自动从 `seed_examples.jsonl` 取 8 条。

---

## 4. Master Prompt 升级（Sprint 0 一并落地）

Master 的 system prompt 里加四段固定指令：

1. **Intent decomposition** —— 收到用户消息后，先列出独立意图（一句话一个），每个独立处理
2. **Parallel spawn** —— 多个意图可以一次 parallel tool call 同时 spawn 多个子代理
3. **No subagent-to-subagent** —— 子代理结果只回到 Master 汇总，子代理之间不互调
4. **Trace recording** —— 每完成一轮，调 `bash aios route record --query "..." --routed-to "<agent>" --outcome <s/r/f> --confidence <0..1>` 记一条 trace

---

## 5. 评估契约

每个子代理 `workspace/agents/<name>/routing_eval.jsonl` 存 30-50 条 (用户原话, 期望路由) 标注。

`aios route eval [--agent <name>]` 跑全量评估：
- 用每条 query 的 embedding 做 Tier 3 ANN 召回（top-1）
- 跟 expected 对比算准确率
- 输出每个 agent 的 precision / recall / 混淆矩阵

每次改 SKILL.md description 或 seed_examples.jsonl 都跑一遍 eval，看准确率是涨是跌。

---

## 6. 新建一个子代理的标准流程

```bash
# 1. 脚手架（Sprint 0 提供）
aios scaffold-agent steward --domain finance --emoji 💰

# 自动生成：
#   workspace/skills/steward/SKILL.md            （骨架）
#   workspace/agents/steward/seed_examples.jsonl （空数组）
#   workspace/agents/steward/routing_eval.jsonl  （空数组）
#   aios/steward/__init__.py
#   aios/steward/cli.py                          （子命令骨架）
#   aios/db/migrations/00NN-steward.sql          （空 schema）

# 2. 填业务逻辑
#    - 写 SQL 表 (migration)
#    - 写 CLI 子命令 (cli.py)
#    - 写 SKILL.md 的"领域定义" + "Spawn Task 模板"
#    - 至少 10 条 seed_examples + 30 条 routing_eval

# 3. 本地最小验证
uv run pytest aios/steward/
uv run python -m aios <name> --help

# 4. push + 部署
git add . && git commit -m "feat(steward): ..." && git push
ssh root@aios "cd /claude/aios && git pull && bash deploy/run_migrations.sh && systemctl restart aios.service"

# 5. smoke test
ssh root@aios "aios route eval --agent steward"  # 看冷启动准确率
# 飞书发一条典型 query，看是否被路由到 steward
```

---

## 7. 工作流强约定

1. **本地写 → push GitHub → 服务器 git pull → migration → 重启** —— GitHub 是 source of truth
2. **migration 永远幂等可重跑** —— 服务器跑过 N 次也不出错
3. **CLI 永远 backward-compatible** —— 老子命令不能删，只能加 `--deprecated` 标记
4. **routing_traces 表是只追加** —— 不删旧数据，靠 `created_at` 过滤窗口
5. **每个 PR commit message 写"为什么"不写"做了啥"** —— diff 说做了啥
