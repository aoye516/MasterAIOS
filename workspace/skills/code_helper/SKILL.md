---
name: code_helper
description: 把"超过 30 行 / 跨多文件 / 严格按步骤跑"的复杂编码任务委托给 Claude Code（外部 agent，通过 `claude` CLI 子进程）。
metadata: {"nanobot":{"emoji":"🛠️","requires":{"bins":["aios","claude"]}}}
---

# Code Helper（外部 Claude Code 子代理）

把"我写不太动" / "用户要严格步骤化执行的代码任务"丢给 Claude Code CLI 处理。
本 skill 是 AIOS 分形架构里的**外部 sub-agent** 入口（与 nanobot 内置 subagent 是平行关系）。

## 何时用

**用**（满足任一）：
1. 用户消息明确说 `Claude Code` / `cc` / `让 Claude 帮我...` / `让它继续改...`
2. 任务需要生成 > 30 行代码
3. 任务跨多个文件
4. 任务要求 1/2/3 步严格执行
5. 复杂重构 / 调试需要循环跑测试

**不用**：
- 简单问答、概念解释、一两行命令
- 单纯查文档（用 `web_search` 或 `pg_archive_search`）
- 你自己 5 分钟内能写完的小修改

## 调用方式

通过内置 `bash` 工具调 `aios code-helper`：

```bash
aios code-helper --task <task-name> "<完整任务描述>" --json
```

**只有一个核心参数要想：`--task`。**

| 参数 | 说明 |
|---|---|
| `--task <name>` | **必填**。任务名。**同名 = 同一个 Claude Code 会话（有记忆），不同名 = 全新会话（从零）** |
| `<description>` | **必填**。完整任务描述（一段 shell-quoted 字符串） |
| `--timeout <sec>` | 可选。最长运行秒数。默认 1800（30 分钟） |
| `--json` | 推荐加上，方便你解析 `final_text` / `tool_calls` / `cost_usd` |
| `--list-tasks` | 列出所有已存在的任务工作区 |

## `--task` 命名规则

**正则：`^[a-z0-9][a-z0-9-]{0,63}$`**（小写字母 / 数字 / 连字符；不能以 `-` 开头；≤64 字符）

- 好：`hello-py`、`refactor-user-auth`、`fix-login-404`、`schedule-cli-poc`
- 坏：`Hello Py`（大写 + 空格）、`task_1`（下划线）、`-foo`（起始破折号）

## 何时同名 / 何时换名

**保持同名**（用户消息出现下面任一信号）：
- "继续 / 再 / 改 / 加 / 让它 / 刚才 / 那个 / 上次"
- 用户没切话题，还在聊同一个东西

**换新名**：
- 用户明确说"新任务" / "换一个" / "从头做个别的"
- 完全不相关的话题

**不确定 → 用旧名**。Claude Code 容忍多余上下文，但没法凭空补丢失的上下文。

每次调完后在 reply 末尾写一行 `📎 CC task: <名字>`，下一轮自己抄回来。

## 输出 JSON 字段

```json
{
  "task": "...",
  "cwd": "/Users/.../aios-cc-workspace/<task>/",
  "session_id": "0d1fa69c-...",
  "final_text": "...",                  // CC 的最终回复，拼起来给用户看
  "tool_calls": [{"name":"Write","input":{...}}, ...],  // 折叠成 "🔧 Write: hello.py" 这种进度
  "duration_ms": 12345,
  "cost_usd": 0.018,
  "error": null,
  "resumed": true                        // true = 自动续接了上一次的 session
}
```

## 完整流程示例

**Round 1**：用户「用 Claude Code 写一个打印 Hi 的 Python 脚本」

任务名定为 `hello-py`：

```bash
aios code-helper --task hello-py 'Write hello.py which prints: Hi' --json
```

看到 `tool_calls` 里有 `Write: hello.py`，`final_text` 描述完成情况，告诉用户：

> 已创建 `~/aios-cc-workspace/hello-py/hello.py`，内容是 `print("Hi")`
>
> 📎 CC task: **hello-py**

**Round 2**：用户「让它再加一行打印当前时间」

从上一条 reply 末尾抄到 `hello-py`，**继续用同一个名字**：

```bash
aios code-helper --task hello-py 'Add a second line printing datetime.now()' --json
```

JSON 里 `resumed: true` 表示自动续接了上次的 session，CC 直接 Edit 已有文件。

**Round 3**：用户「写个新的，用 Node 写一个 todo list」

切任务，换新名字：

```bash
aios code-helper --task todo-list-node 'Write a simple Node.js todo list CLI' --json
```

## 出错排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `claude CLI not found in PATH` | claude CLI 没装 | `npm install -g @anthropic-ai/claude-code` |
| `invalid task name` | task 名违反正则 | 改成 kebab-case（小写 + 短横线） |
| `error: timeout after Ns` | 任务超过 timeout | 用同名再调一次（自动续接），或增大 `--timeout` |
| `error: claude exited 1` | claude 内部失败 | 看 stderr 摘要；多半是认证或网络问题 |

## 两条铁律

1. **延续任务必须用同一 `--task` 名字**，不同 = 失忆从头 = 浪费用户时间和钱
2. **不要把整坨 JSON 扔给用户看** — 拼 `final_text` + 折叠 `tool_calls` 成"🔧 …"进度，简明回复
