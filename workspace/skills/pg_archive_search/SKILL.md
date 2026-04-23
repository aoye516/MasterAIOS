---
name: pg_archive_search
description: 在 PostgreSQL `archival_memory` 表里检索过去的对话 / 知识 / 用户重要信息（向量 + 全文混合）。
metadata: {"nanobot":{"emoji":"📚","requires":{"bins":["aios"]}}}
---

# PostgreSQL Archival Memory 检索

AIOS 把"长期外部记忆"放在 PostgreSQL 的 `archival_memory` 表里（`vector(1024)` + `tsvector` 全文索引）。
`memory/MEMORY.md`/`SOUL.md`/`USER.md` 是 Dream 维护的"短期 + 人格"层；这个 skill 是用来翻"考古级"长期记忆的。

## 何时用

**用**：
- 用户问"我之前是不是说过 X"、"上次那件事怎么处理的"、"那个项目叫什么来着"
- 你需要找跨会话的事实（不在 MEMORY.md 里）
- 用户问的具体名词（项目名、人名、术语）你没把握，先查再答

**不用**：
- 简单闲聊 / 已经在 MEMORY.md 里的常识
- 需要实时数据（用 `web_search`）
- 写代码 / 跑命令（用 `code_helper` / `bash`）

## 调用方式

通过内置的 `bash` 工具调 `aios` CLI：

```bash
aios archive-search "你的查询" --limit 5
```

加 JSON 输出方便结构化解析：

```bash
aios archive-search "上次提到的服务器 IP" --limit 3 --json
```

按用户过滤（多用户场景）：

```bash
aios archive-search "考试日程" --user-id 1 --limit 5
```

## 输出格式（默认 pretty）

```
#1 id=42 created=2026-04-22T15:30:00  [score=0.6231]
  内容前 400 字符...
  metadata: {'topic': 'deployment'}

#2 ...
```

`score` 当前是 tsvector 的 `ts_rank`（越大越相关）。如果将来加入 embedding 打分，含义会变成 cosine distance（越小越相关）—— 调用前先看 `--json` 输出里的 metadata 是哪个分支。

## 三条铁律

1. **先查再答** — 用户问明显需要历史背景的问题，调一次本 skill 再开口
2. **保持原文** — 把检索到的 `content` 原文显式引用给用户，不要凭记忆改写
3. **没结果就说没** — 检索为空（"no archival memory matched"）就明确告诉用户"在我的长期记忆里没找到"，不要自己编

## 出错排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `DATABASE_URL is not set` | nanobot 进程没拿到 env | 检查 `scripts/run_nanobot.sh` 是否 source 了 `.env` |
| `connection refused` | 本地 PG 没起 | `brew services start postgresql@16` |
| `relation "archival_memory" does not exist` | schema 没初始化 | `psql aios -f scripts/init_db.sql` |
