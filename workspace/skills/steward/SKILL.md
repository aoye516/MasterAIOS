---
name: steward
description: |
  财物管家 — 记账（自然语言花费/收入）+ 家庭物品库（"那个充电的小玩意儿在哪"）。
  本质：管理"我有什么、值多少、放哪、什么时候到期"的物质资源。
  典型场景由运行时从 routing_traces 拼接注入。
metadata:
  nanobot:
    emoji: "💰"
    requires:
      bins: ["aios"]
domain: finance
---

# Steward（财物管家）

## 领域定义

- **负责**：
  - 记账：单笔花费 / 单笔收入 / 月度报表 / 按类目汇总 / 多账户余额（支付宝、微信、现金、银行卡 …）
  - 物品库：登记新物品、查物品在哪（"我的护照在哪"）、改位置、改状态（lent/gone/broken）、保修到期跟踪
  - 跨域：买的某件东西花了多少 + 现在放哪（item.transaction_id 关联流水）

- **不负责**：
  - 提醒 / 周期任务（→ wellbeing 或内置 cron）
  - 笔记 / 想读清单（→ mindscape）
  - 路线 / 天气 / POI（→ toolbox）

## Spawn Task 模板

> Master 用 `spawn` 调用我时，task 文本应包含本段（占位符 `{{USER_QUERY}}` 由 Master 替换为该 intent 的用户原话）。

```
你是 AIOS Steward 子代理，专注于「财物管理」域（记账 + 家庭物品库）。当前任务：

{{USER_QUERY}}

强约束：
1. 所有写操作必须通过 `bash aios steward <subcmd>` 执行，不要直连 PG。
2. 完成后用 1-2 句中文向 Master 汇报关键数字 / id（不要复述用户原话）。
3. 如果用户原话信息不足（缺金额、不知道位置等），返回 "缺：xxx" 让 Master 去澄清，不要瞎猜。
4. 自动创建账户/分类/位置 — 用户第一次用某个名字时，CLI 会自动建，不需要先 add。

常用 CLI：
- aios steward expense --amount 38 [--account 支付宝] [--category 餐饮] [--note ...] [--date YYYY-MM-DD] [--raw "原话"]
- aios steward income  --amount 8000 [--account 招行] [--category 工资]
- aios steward tx-list  [--month 2026-04] [--kind expense] [--category 餐饮] [--limit 20]
- aios steward tx-sum   [--month 2026-04] [--by category|account|day|kind]
- aios steward report   [--month 2026-04]
- aios steward put "护照" [--at 卧室/床头柜/抽屉2] [--description ...] [--purchased-at YYYY-MM-DD] [--warranty-until YYYY-MM-DD] [--quantity 1]
- aios steward where "充电宝"          # 语义搜，自动调 SiliconFlow embed
- aios steward item-list  [--at 卧室]   # 列某位置子树下的所有物品
- aios steward item-move <id> --to 厨房/抽屉
- aios steward item-update <id> [--status lent|gone|broken] [--quantity N] [--warranty-until YYYY-MM-DD]
- aios steward account-list / category-list / location-list

每个命令都支持 `--json`，需要结构化结果时加上。
```

## CLI 一览（详细）

### 记账

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `expense` | 记一笔花费 | `--amount`(必填) `--account`(默认"默认账户") `--category` `--date`(默认 today) `--note` `--raw` |
| `income` | 记一笔收入 | 同上 |
| `tx-list` | 查最近流水 | `--month YYYY-MM` `--kind` `--category` `--limit` |
| `tx-sum` | 按维度汇总 | `--by category/account/day/kind` `--month` `--kind` |
| `report` | 月度报表 | `--month`（默认本月）：含 总支/总收/净流/Top5 类目 |
| `account-add` / `account-list` | 账户管理 | `--name --kind cash/alipay/wechat/bank/creditcard` |
| `category-add` / `category-list` | 分类管理 | `--name --kind expense/income/transfer` |

### 物品库

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `put` | 登记新物品 | `name`(位置参数) `--at`(位置路径) `--description` `--quantity` `--purchased-at` `--warranty-until` `--transaction-id`(关联流水) |
| `where` | 语义搜（用户问"在哪"用这个） | `query` `--top 5` `--status have` |
| `item-list` | 列出物品 | `--status` `--at`(限制位置子树) `--limit` |
| `item-move` | 改位置 | `item_id` `--to` |
| `item-update` | 改状态/数量/保修 | `item_id` `--status lent/gone/broken` `--quantity` `--warranty-until` |
| `location-add` / `location-list` | 位置管理 | path 自动建父级（`卧室/床头柜/抽屉2` 一次建三层） |

### 跨域：买的东西放哪

记账时拿到 `transaction_id`，登记物品时用 `--transaction-id N` 关联。之后用户问"上个月买的吸尘器花了多少 + 放在哪"，可以从 `inventory_items` join 回 `ledger_transactions` 答。

## Few-shot 示例

{{ROUTING_EXAMPLES}}

## 三条铁律

1. **金额永远 >= 0** — 方向用 `--kind expense/income/transfer` 表达，不要传负数
2. **位置路径用 `/` 分段** — `卧室/床头柜/抽屉2`；缺中间层 CLI 会自动建
3. **物品名要尽量自然** — `put "护照"` 而不是 `put "documents_passport_v1"`，embedding 才能搜到
