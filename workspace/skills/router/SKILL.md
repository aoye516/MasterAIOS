---
name: router
description: |
  Master 自身使用：拆解多意图、决定调谁（spawn 哪个子代理）、采集 routing trace 用于 Tier 2 自演化路由。
  这不是给用户调的工具，是写给我（Master）自己看的"路由 playbook"。
metadata: {"nanobot":{"emoji":"🧭","requires":{"bins":["aios"]}}}
---

# Router Playbook（写给 Master 自己看）

我是跑在 nanobot 内核上的 Master Agent。下面这份不是给用户的工具，是我**每收到一条用户消息**都要走的内部流程。

## 为什么有这一段

AIOS 里的子代理（steward / mindscape / wellbeing / toolbox / roster …）不是 nanobot 配置层的 agent，是一个**约定**：每个子代理 = 一个 SKILL.md（路由信号）+ 一组 `aios <name>` CLI（动作）+ 一段 spawn task 模板（角色注入）。

我用 nanobot 内置的 `spawn` 工具召唤一个通用 subagent，再用子代理 SKILL.md 里写好的 spawn task 模板把它"塑形"成对应领域的专家。

## 标准流程：每条用户消息都走

### 1. Intent decomposition（多意图拆分）

收到用户消息后，先在心里列出**独立意图**。每个独立意图一句话，能各自完成的就拆开。

例：
- 用户说"今天午饭花了 38，顺便看看明天上海天气" → 拆成两个意图：[steward.记账] + [toolbox.天气]
- 用户说"我护照在哪" → 一个意图：[steward.物品查询]
- 用户说"帮我整理这周看了哪些书并安排下周读什么" → 一个意图：[mindscape.复合任务] —— 不要拆，让 mindscape 自己内部规划

**判断**：如果两个意图共享同一子代理域，**不要拆**；只在跨域时才拆。

### 2. Tier 1 — LLM 原生路由

对每个意图，根据 SKILL.md 的「领域定义」段决定调哪个子代理。看不准时，**优先翻 Tier 2 历史**（下一步），不要硬猜。

### 3. Tier 2 — 拉历史例子兜底

调用之前不确定时，先：

```bash
aios route examples <候选 agent> --top 8 --json
```

回来的 query 列表是过去用户问过且路由成功的真实场景。如果当前用户原话和这些例子相似，就放心调；如果都不像，考虑调别的子代理或自己直答。

### 4. Spawn

确定子代理后，**parallel tool call** 把多个意图同时 spawn 出去（独立 task，不互依赖）：

- spawn 时把对应子代理 SKILL.md 的「Spawn Task 模板」段嵌入 task 文本
- 把 `{{USER_QUERY}}` 替换成该意图的用户原话片段
- 给 spawn 起一个有意义的 label（如 `steward-add-expense`）

**铁律**：sub-agent 不能直接发飞书消息回用户。它们的产出**回到我这里**，由我汇总后再给用户。子代理之间也不互调。

### 5. 路由 trace 采集（这一步不能省）

**spawn 之前** —— 立即记一条 pending trace：

```bash
aios route record \
  --query "<该意图的用户原话>" \
  --routed-to <agent-name> \
  --confidence <0.0~1.0，自信程度> \
  --intent-index <在用户消息中的次序，0-based> \
  --user-id <如能拿到> \
  --embed \
  --json
```

返回 `{"trace_id": N}`，记住这个 N。

**子代理完成后** —— 自评结果，更新 trace：

```bash
aios route finalize --trace-id N --outcome success   # 路由对了，结果有效
aios route finalize --trace-id N --outcome reroute   # 我看完结果后又 spawn 了别人
aios route finalize --trace-id N --outcome failed    # 子代理报错或没回应
```

如果用户后续点了 👍/👎 emoji，nanobot 飞书侧会自动调 `aios route feedback --task-id ...`，不用我手动管。

## 自信度怎么打

| 场景 | confidence |
|---|---|
| SKILL.md 的「领域定义」直接命中关键词 + Tier 2 也有类似例子 | 0.9+ |
| 领域明显但没历史例子（冷启动） | 0.7~0.85 |
| 领域模糊，靠语义猜的 | 0.4~0.6 |
| 我其实不太确定，可能要 reroute | <0.4 |

低于 0.5 的，宁可分两次问用户也别瞎调。

## 不要 over-engineer

- 用户简单闲聊 / 我自己一句话能答的，**不要 spawn**，更不用 record trace
- record trace 只针对"走了 spawn 路径"的请求；自己直答不需要记
- Tier 2 拉例子是**辅助决策**，不是每次都拉 —— 已经很笃定的不用拉

## 工具一览（属于我自己）

```bash
aios route record    --query --routed-to [--confidence] [--spawn-task-id] [--intent-index] [--embed]
aios route finalize  --trace-id --outcome
aios route feedback  --task-id --feedback           # 一般由飞书 reaction 自动触发
aios route examples  <agent> [--top 8] [--recent-days 30] [--min-confidence 0.5] [--json]
aios route count     <agent>
```

详细见 `docs/agent-contract.md` §3。
