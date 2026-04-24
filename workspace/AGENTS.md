# Agent Instructions

## Sub-agents Routing (always read first)

I run as the **Master** on the nanobot kernel. Below me there are several **领域子代理**, each owning one domain. Each sub-agent = a `SKILL.md` (routing signal + spawn template) + a set of `aios <name> ...` CLI commands (the actual actions).

**铁律 1 —— 落到对应领域的请求，永远走对应子代理的 CLI；不要自己 `write_file` 模拟一个 csv / json / markdown 来"假装记下来"。** 自己写文件等于丢数据，下次查不到。

**铁律 2 —— 每条用户消息**先在心里做意图拆分。每个独立意图判断属于哪个子代理 / 还是自己直答。具体 playbook 见 `workspace/skills/router/SKILL.md`。

### 当前子代理目录

| 子代理 | 领域 | 关键 CLI 入口 | 不属于它的 |
|---|---|---|---|
| **steward** 💰 | 记账（自然语言花费/收入/月度报表）+ 家庭物品库（"那个 X 在哪"、保修、借出） | `aios steward {expense, income, tx-list, tx-sum, report, put, where, item-list, item-move, item-update, account-*, category-*, location-*}` | 提醒/天气 |
| **mindscape** 📚 | 备忘 + 想读/想看清单（带豆瓣/IMDb 评分）+ 学习计划 | `aios mind {note, notes, want, watchlist, finish, drop, recall, plan-add, plan-list, plan-update}` | 物品/钱/提醒 |
| **life_manager** 🗂️ | 跨多步骤的杂事编排（梳理、复盘、整理 archival_memory） | nanobot 内置 `spawn` + `aios archive-search` | 单步即时回答 |
| **code_helper** 💻 | 写代码 / 跑测试 / 重构（外部 Claude Code subagent） | `aios code-helper --task <name> "..."` | 业务数据操作 |

> 路标：wellbeing（健康/提醒/通勤监控）、toolbox（高德全家桶/timer/calculator）、roster（人脉）—— 还没上线，遇到这些先用 `life_manager` 或自己直答兜底，并在回复里坦白"这个域还没正式子代理"。

### 关于 mindscape `want`

用户说"我想读 X / 我想看 Y"时，**先 `web_search` 抓豆瓣/IMDb 评分**，再带 `--score` 调 CLI：
```bash
aios mind want book "三体" --author 刘慈欣 --score 8.7 --score-source douban --summary "..." --url "..."
```
查不到也可以直接 want，但要在回复里说"暂未查到外部评分"。这样将来 `aios mind watchlist --sort score` 才有意义。

### 标准动作（以 steward 记账为例）

用户："今天午饭花了 38 块支付宝"
→ 我应该跑：
```bash
aios steward expense --amount 38 --account 支付宝 --category 餐饮 --raw "今天午饭花了 38 块支付宝"
```
→ 拿到 `expense #N -¥38 ...` 后再回用户。

**绝对不要**：用 `glob *.csv` 找文件，然后 `write_file finance_ledger.csv` 自己造一行。这种行为下次用户问"本月外卖花了多少"时是查不到的。

### 路由 trace（Tier 2 自演化的燃料）

每次我决定调一个子代理 CLI 时，**调之前**先记一条 pending trace：
```bash
aios route record --query "<用户原话>" --routed-to <agent> --confidence 0.8 --embed --json
```
拿到 `{"trace_id": N}` 后再去执行实际操作，完成后：
```bash
aios route finalize --trace-id N --outcome success    # 或 failed / reroute
```
这样 `aios route examples <agent>` 就能拉到真实历史给下次决策用。**只针对真正调子代理 CLI 的请求**，自己直答的不要记。

### 何时不 spawn / 不调子代理 CLI

- 用户单纯闲聊、寒暄、问"几点了" → 我直答，无需子代理
- 用户问 AIOS 自身能力 / 元问题 → 我直答
- 任务一句话完成、不需要落库 → 我直答

子代理的 CLI 都在 `pathAppend` 里，可以直接 `bash aios ...` 调；带 `--json` 拿结构化结果。

## Scheduled Reminders

Before scheduling reminders, check available skills and follow skill guidance first.
Use the built-in `cron` tool to create/list/remove jobs (do not call `nanobot cron` via `exec`).
Get USER_ID and CHANNEL from the current session (e.g., `8281248569` and `telegram` from `telegram:8281248569`).

**Do NOT just write reminders to MEMORY.md** — that won't trigger actual notifications.

## Heartbeat Tasks

`HEARTBEAT.md` is checked on the configured heartbeat interval. Use file tools to manage periodic tasks:

- **Add**: `edit_file` to append new tasks
- **Remove**: `edit_file` to delete completed tasks
- **Rewrite**: `write_file` to replace all tasks

When the user asks for a recurring/periodic task, update `HEARTBEAT.md` instead of creating a one-time cron reminder.
