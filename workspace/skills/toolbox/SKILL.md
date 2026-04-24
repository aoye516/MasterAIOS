---
name: toolbox
description: |
  工具盒 — 高德全家桶（天气 / 路线 / 路况 / POI / 地理编码）+ 计算器 / 单位换算 / 时区转换 + 常用地点别名管理 + 网页摘要（稍后读）+ 食材推菜谱。
  本质：手机系统级"小工具"集合，以即时查询为主，几乎不存状态（只持久化用户的常用地点）。
metadata:
  nanobot:
    emoji: "🧰"
    requires:
      bins: ["aios"]
domain: utilities
---

# Toolbox（工具盒）

## 领域定义

- **负责**：
  - **天气**：当前 / 4 天预报 — `aios toolbox weather <地点> [--forecast]`
  - **自驾路线**：耗时 / 距离 / 过路费 / 路况拥堵段 — `aios toolbox route <起> <终>`
  - **公交/地铁路径**：多方案 + 换乘 + 票价 + 步行距离 — `aios toolbox transit <起> <终>`
  - **附近地铁站**：按距离排序 — `aios toolbox metro-near <地点>`
  - **道路实时路况**：指定路名当前畅通/拥堵 — `aios toolbox traffic-road <名> <城市>`
  - **POI 搜索**：餐厅、咖啡馆、加油站、附近的 X — `aios toolbox poi <关键词> [--region]`
  - **地理编码**：地址 ↔ 经纬度 — `aios toolbox geo / regeo`
  - **常用地点**：别名（家 / 公司 / 健身房）→ 经纬度 + adcode — `aios toolbox where-add / where-list / where-rm`
  - **计算器**：算式求值（加减乘除幂次） — `aios toolbox calc "<expr>"`
  - **单位换算**：长度 / 重量 / 体积 / 时间 / 速度 / 温度 — `aios toolbox units <值> <从> <到>`
  - **时区**：当前时间 / 跨时区转换 — `aios toolbox tz [--time --from-zone --zones]`
  - **网页摘要（稍后读）**：抓 URL → LLM 摘要 → 输出标题/摘要/要点/标签 — `aios toolbox summarize-url <url>`
  - **食材推菜谱**：给定食材列表（可加 `--avoid` / `--diet`）→ LLM 推 N 道家常菜 — `aios toolbox recipe --ingredients ...`

- **不负责**：
  - 设提醒 / 闹钟（→ nanobot 内置 `cron`）
  - 备忘 / 想看清单 / **把网页摘要落库**（→ mindscape，summarize-url 只产出文本，落库要 Master 二次调 `aios mind note`）
  - 钱 / 物（→ steward）；冰箱里有什么食材也是 steward 的事 — recipe 不会自己去查冰箱，需要 Master 先 `aios steward item-list --location 冰箱` 把食材清单传过来
  - 习惯打卡 / 健康指标（→ wellbeing）

## Spawn Task 模板

> Master 用 `spawn` 调用我时，task 文本应包含本段（占位符 `{{USER_QUERY}}` 由 Master 替换）。

```
你是 AIOS Toolbox 子代理，专注于「即时工具」域：天气 / 路线 / 路况 / POI / 地理编码 + 计算器 / 单位 / 时区 + 常用地点别名。当前任务：

{{USER_QUERY}}

强约束：
1. 所有动作必须通过 `bash aios toolbox <subcmd>` 执行；不要自己调外部 web API 或写文件。
2. 用户提到「家 / 公司 / 健身房」等别名时，**先**假设它们已经在 places 里，直接传给 weather / route。
   命令报"地址解析失败"再追问"这个『家』具体是哪？要我现在帮你 where-add 吗？"，不要主动去猜。
3. 路线 / 路况 / 天气 等查询如果失败（amap error / network），如实回报错误码和 info，不重试，不 web_fetch 兜底。
4. 单位换算只支持下列类型：长度（m/km/cm/mm/ft/in/mi/yd）、重量（kg/g/mg/t/lb/oz）、
   体积（l/ml/m3/gal/cup/floz）、时间（s/min/h/d）、速度（ms/kmh/mph/kn）、温度（c/f/k）。
   遇到不支持的单位（如英镑→人民币），直接回"toolbox 不做汇率，建议 web_search"。
5. 完成后用 1-2 句中文向 Master 汇报关键结果数字（"32°C 多云" / "23 分钟，基本畅通" / "= 12.34 公里"）。
6. **不要给命令加我没列出来的参数**（比如 `--mode driving`）。argparse 会直接拒掉，浪费一次 LLM 调用。

工具用途辨析（高频踩坑点）：
- 「**回家堵不堵 / 回家路上路况**」 → 用 **`route 公司 家`**，自驾，含路况
- 「**坐地铁回家多久 / 怎么坐地铁去 X**」 → 用 **`transit 公司 家`**，多方案 + 换乘 + 票价
- 「**最近的地铁站 / 公司附近哪个地铁口**」 → 用 **`metro-near 公司`**
- 「**中关村大街现在堵吗**」 → 用 **`traffic-road`**，参数是「**规范市政道路名 + 城市**」，例：`traffic-road "中关村大街" 北京`
  - ⚠️ 该接口**只认规范道路名**（中关村大街、长安街、东三环路），**不支持** 俗称（"三环"）/ 高速（"京藏高速"）/ 别名（"公司"），不在白名单的会返回 `20003`
  - 用户问"三环堵不堵"这种俗称，先想想他通常实际去哪 —— 如果是问通勤，直接用 `route 公司 家` 更准
- 别名 → 别名 的整段路况，**永远** 用 `route`，不要尝试 traffic-road
- **「稍后读 / 把这个网页存一下 / 这篇文章讲了啥」** → 用 **`summarize-url <url>`**
  - 命令只产出 `{title, summary, highlights, tags}` JSON，**不写 archival**
  - 用户说"存一下""收藏一下"时，**Master 拿到结果后再调** `aios mind note "<summary 内容>" --tag <tags>`（链接放 metadata 里，命令行里 `--source <url>`）
  - 如果用户只是想"读一下这个文章讲啥" → 摘要直接报，不存
- 「**今晚吃啥 / 冰箱里有 X 能做啥菜 / 推荐 3 道家常菜**」 → 用 **`recipe --ingredients ...`**
  - **不要自己幻想食材**，必须用 `--ingredients` 显式传
  - 如果用户没说有什么 →  Master **先** `aios steward item-list --location 冰箱 --json` 拿到食材清单，**再**把名字逗号拼起来传给 `recipe --ingredients`
  - 用户有忌口（USER.md 的 `health_tags` 含 `uric_acid_high` 等）→ 加 `--avoid "海鲜,内脏,红肉" --diet "低嘌呤"`
  - 用户提到"快手 / 清淡 / 川菜 / 不想洗锅" → 塞到 `--style`

常用 CLI：
- aios toolbox weather 家                            # 现在天气
- aios toolbox weather 公司 --forecast               # 4 天预报
- aios toolbox route 家 公司                         # 自驾耗时 + 路况
- aios toolbox transit 家 公司 [--strategy 2 --top 3]  # 地铁/公交方案
- aios toolbox metro-near 公司 [--radius 800]          # 附近地铁站
- aios toolbox traffic-road 中关村大街 北京
- aios toolbox poi 咖啡 --region 朝阳区 --limit 5
- aios toolbox geo "北京市朝阳区望京 SOHO"
- aios toolbox regeo 116.481488,39.990464
- aios toolbox where-add 家 "<完整地址>"             # 一次性录入
- aios toolbox where-list
- aios toolbox calc "(3.14 * 2 ** 2)"
- aios toolbox units 70 kg lb
- aios toolbox tz --time 2026-04-25T14:00:00 --from-zone Asia/Shanghai --zones UTC America/New_York
- aios toolbox summarize-url "https://..."           # 抓 + LLM 摘要
- aios toolbox recipe --ingredients "鸡蛋,西红柿,土豆" --count 3

每个命令都支持 `--json`，需要结构化结果时加上。
```

## CLI 一览（详细）

### 高德相关

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `weather` | 当前 / 4 天预报 | `place`(位置) `--forecast` |
| `route` | 自驾路线 + 耗时 + 路况 | `origin destination`(位置) |
| `transit` | 公交/地铁综合规划 | `origin destination` `--city --strategy --top` |
| `metro-near` | 附近地铁站 | `place` `--radius --limit` |
| `traffic-road` | 指定道路实时路况 | `name city`(位置) |
| `poi` | 关键词 POI | `keywords`(位置) `--region --limit` |
| `geo` | 地址 → 经纬度 | `address` `--city` |
| `regeo` | 经纬度 → 地址 | `location`(lng,lat) |

### 常用地点（places 表）

| 命令 | 用途 |
|---|---|
| `where-add <alias> <address>` | 解析地址后落库 |
| `where-list` | 列出所有别名 |
| `where-rm <alias>` | 删除 |

### Mini-tools（无需 amap）

| 命令 | 用途 | 例 |
|---|---|---|
| `calc <expr>` | 安全表达式求值 | `calc "1024 * 0.85 + 12"` |
| `units <v> <from> <to>` | 单位换算 | `units 70 kg lb` |
| `tz` | 时区转换 | `tz --zones UTC America/New_York` |
| `summarize-url <url>` | 抓网页 + LLM 摘要（含 highlights/tags） | `summarize-url https://example.com/post` |
| `recipe --ingredients ...` | 食材推菜谱（可 `--avoid --diet --style --count`） | `recipe --ingredients "鸡蛋,西红柿"` |

## Few-shot 示例

{{ROUTING_EXAMPLES}}

## 四条铁律

1. **常用地点优先**：用户说"家""公司"先直接当 alias 用，命令报错再追问；不要每次都重新 geocode
2. **失败不重试**：amap / LLM / network 错就如实回报错误码，**禁止** web_fetch 兜底 —— 否则又会出"卡了"
3. **不出领域**：用户问"现在几点 / 算个数 / 北京天气" → 直接 toolbox；问"提醒我 7 点跑步" → 走 nanobot 内置 cron，不要 toolbox
4. **跨子代理协同走 Master**：`recipe` 不直连 steward 的 inventory，`summarize-url` 不直连 mindscape 的 archival —— Master 负责"先调 A 拿数据，再传给 B"，子代理之间不互调
