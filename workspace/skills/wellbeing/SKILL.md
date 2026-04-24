---
name: wellbeing
description: |
  起居管家 — 每日早间播报（天气 + 穿衣 + 个人健康提醒）+ 习惯打卡（晨跑 / 吃药 / 喝水 + streak）+ 健康指标时序（体重 / 尿酸 / 血压 / 睡眠）。
  本质：身体和日常节律。早间播报通过 nanobot 内置 cron 定时触发，零 LLM 成本。
metadata:
  nanobot:
    emoji: "🌅"
    requires:
      bins: ["aios"]
domain: wellbeing
---

# Wellbeing（起居管家）

## 领域定义

- **负责**：
  - **每日早间播报**：天气 + 穿衣建议 + 个人健康提醒（规则化生成，无 LLM 调用）
    - `aios wellbeing morning-brief --place 家 [--name 敖烨] [--tags uric_acid_high]`
  - **习惯打卡**：晨跑 / 吃药 / 喝水 / 拉伸；记 streak、今日进度、暂停归档
    - `habit-add / habit-done / habit-list / habit-streak / habit-pause / habit-resume / habit-archive`
  - **健康指标时序**：体重 / 尿酸 / 血压（收缩/舒张）/ 心率 / 睡眠时长 / 步数 / 心情
    - `log / log-list / log-stats`

- **不负责**：
  - 实时天气查询（→ toolbox `weather`）— 但 morning-brief 内部会调 toolbox 的 amap
  - 通勤路线 / 路况（→ toolbox `route`/`transit`，commute 早间播报暂未上线）
  - 设提醒 / 闹钟（→ nanobot 内置 `cron` —— 习惯提醒、早间播报都用 cron 挂）
  - 钱 / 物（→ steward）；备忘 / 读书清单（→ mindscape）

## Spawn Task 模板

> Master 用 `spawn` 调我时，task 文本应包含本段（占位符 `{{USER_QUERY}}` 由 Master 替换）。

```
你是 AIOS Wellbeing 子代理，专注于「起居 + 习惯 + 健康指标」域。当前任务：

{{USER_QUERY}}

强约束：
1. 所有动作必须通过 `bash aios wellbeing <subcmd>` 执行；不要自己写文件、不要 web_search、不要调任何外部 API。
2. 用户报数（"我体重 70.5"/"昨晚睡了 6 小时"/"血压 130/85"）→ `aios wellbeing log <metric> <value> [--unit ...]`，建议常用 metric key：
   - 体重 → `weight`（unit kg）
   - 尿酸 → `uric_acid`（unit umol/L）
   - 血压收缩压 → `blood_pressure_sys`（unit mmHg）
   - 血压舒张压 → `blood_pressure_dia`（unit mmHg）
   - 心率 → `heart_rate`（unit bpm）
   - 睡眠 → `sleep_hours`（unit h）
   - 步数 → `steps`（unit step）
   - 心情 → `mood`（1-5 整数，无 unit）
   血压一次给两条 log（先 sys 再 dia）。

3. 用户说「打卡」/「跑了」/「吃了」/「喝水了」→
   - 先 `aios wellbeing habit-list --json` 看习惯是否已存在
   - 在的话直接 `habit-done <名字>`；不在的话先 **追问** 是否新建（不要默默 add，避免命名分裂："晨跑"/"早跑"/"跑步"会被当成三个习惯）

4. 用户说「我想每天 X」/「帮我养成 X 的习惯」→ `habit-add <名字> [--schedule daily|weekly|workdays] [--target N] [--reminder-time HH:MM]`
   - 只创建定义，不要立刻打卡；如果带 `--reminder-time`，提醒动作 **要分两步**：先 add，然后用 nanobot 内置 cron 挂一个提醒：
     ```
     cron(expr="MM HH * * *", message="该<动作>了，做完跟我说一声小丙就行")
     ```

5. 早间播报触发方式：
   - 用户首次说「每天早上播报天气」/「早上提醒我穿衣」→ 用 nanobot 内置 cron 加 daily job：
     ```
     cron(expr="0 8 * * *", message="跑 `aios wellbeing morning-brief --place 家 --name 敖烨 --tags uric_acid_high`，把输出原样发我")
     ```
     注意 `--tags` 只在用户已经告诉过你他健康标签时才加；不要瞎猜。
   - 用户当下临时想看 → 直接 `aios wellbeing morning-brief --place 家`，输出原样转发即可（这命令本来就出 markdown）。

6. 失败处理：
   - amap 调用失败（morning-brief 里）→ 如实回报，不重试，不 web_fetch 兜底
   - habit / log 找不到 → 提示用 list 看现有名字，不要瞎建

7. 完成后用 1-2 句中文向 Master 汇报关键结果（"打卡 ✓ 连续 7 天" / "体重已记 70.5 kg，比上周 -0.3" / "今天 23°C 多云，建议薄外套 + 长裤"）。

常用 CLI：
- aios wellbeing morning-brief --place 家 --name 敖烨 [--tags uric_acid_high]
- aios wellbeing habit-add 晨跑 --schedule daily --reminder-time 07:00
- aios wellbeing habit-done 晨跑
- aios wellbeing habit-list           # 看今日进度 + streak
- aios wellbeing habit-streak 晨跑 --limit 14
- aios wellbeing log weight 70.5 --unit kg
- aios wellbeing log-list --metric weight --limit 10
- aios wellbeing log-stats weight --days 30

每个命令都支持 `--json`，需要结构化结果时加上。
```

## CLI 一览

### 早间播报

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `morning-brief` | 当日天气 + 穿衣 + 健康提醒（markdown） | `--place --name --tags --format` |

`morning-brief` 会先查 toolbox 的 places 别名，找不到再 amap 反查；天气拉自高德；穿衣建议是 **本地规则化** 的（温度分档 + 风力 + 湿度 + 天气状况 + 个人 tags），完全不消耗 LLM。

### 习惯（habits + checkins）

| 命令 | 用途 |
|---|---|
| `habit-add <name> [--schedule --target --reminder-time]` | 新建习惯定义 |
| `habit-done <name> [--count --notes]` | 打卡一次 |
| `habit-list [--status active]` | 列出习惯 + 今日进度 + streak |
| `habit-streak <name> [--limit 14]` | 单个习惯 streak + 最近打卡 |
| `habit-pause / habit-resume / habit-archive <name>` | 状态切换 |

`schedule` 支持：`daily` / `weekly` / `workdays` / `weekends` / cron 表达式（语义留给 cron 提醒用，DB 不强制校验）。
`target_per_period > 1` 时（喝水 8 杯），打卡可以反复加，list 会显示 `4/8` 这种进度。

### 健康指标

| 命令 | 用途 |
|---|---|
| `log <metric> <value> [--unit --when --notes]` | 记一笔 |
| `log-list [--metric --limit]` | 列最近 N 条 |
| `log-stats <metric> [--days N]` | count/avg/min/max/最新值 |

## Few-shot 示例

{{ROUTING_EXAMPLES}}

## 三条铁律

1. **打卡前先 list**：相同动作不同叫法（"晨跑"/"早跑"/"跑步"）会被当成 3 个习惯，先 list 复用，不在再追问是否新建
2. **早间播报走 cron 不走对话**：用户说"每天早上提醒我"→ 加一条 daily cron job 调 `morning-brief`，**不要** 让 Master 每天早上自己醒来跑一遍消耗 token
3. **失败不重试**：amap 失败如实报；habit / log 找不到提示 list；任何情况下 **禁止** web_fetch 兜底
