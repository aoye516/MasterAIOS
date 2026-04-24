---
name: code_helper
description: 【MUST USE】写代码 / 改文件 / 跑命令 / 部署小工具的任务一律走这里 —— 委托给本机 Claude Code CLI（接的是火山 ARK coding plan，比 Master 主 API 便宜得多）。**新工作流是非阻塞的：start 派任务 → 立刻给用户反馈 → cron 每分钟 poll 一次 → 看到 [DONE]/[FAILED]/[NEEDS_CONFIRMATION] 再处理**。绝对不要再用旧的 `--task X "desc"` 同步形式（会被 nanobot 的 120s 超时杀掉）。
metadata: {"nanobot":{"emoji":"🛠️","requires":{"bins":["aios","claude"]}}}
---

# Code Helper（外部 Claude Code 子代理 · 异步工作流）

把"我写不太动" / "用户要严格步骤化执行的代码任务"丢给 Claude Code CLI 处理。
本 skill 是 AIOS 分形架构里的**外部 sub-agent** 入口，跟 nanobot 内置 subagent 平行。

> 💰 **省钱重点**：Master（你）走的是按量付费的主 API；`aios code-helper` 后端
> 接的是火山引擎 ARK 的 Claude Code coding plan（包月 / 大额度）。同样一段
> 代码，让 CC 写比你自己写便宜一个数量级。**默认应该委托，不应该自己上手。**

## ⚠️ 不要再用同步形式（重要）

旧版本是 `aios code-helper --task X "desc" --json`，会**阻塞**整个 nanobot exec。
nanobot 的 exec 工具有 **120 秒硬超时**，复杂任务（写完整应用、重构跨文件）几乎
肯定撑不到 2 分钟，结果就是 CC 被强杀、文件写一半、Master 拿不到结果还以为失败。

**新工作流不会被超时杀掉**，因为 `start` 几毫秒就返回，CC 在后台一个独立 daemon
进程里慢慢跑，poll 也是几毫秒读一个 JSON 文件。

## 何时用

**用**（满足任一就走，不要犹豫）：
1. 用户消息出现 `Claude Code` / `claude code` / `cc` / `用 cc` / `让 Claude 帮我` / `让 CC 写`
2. 任务关键词：`写脚本` / `写个程序` / `写文件` / `改文件` / `重构` / `跑测试` / `调试` / `写 demo` / `部署小工具`
3. 任务需要生成 > 30 行代码
4. 任务跨多个文件
5. 任务要求 1/2/3 步严格执行
6. 复杂重构 / 调试需要循环跑测试

**不用**：
- 简单问答、概念解释、一两行命令
- 单纯查文档（用 `web_search` 或 `pg_archive_search`）
- 子代理已经能搞定的事（记账、备忘、查路况、健康打卡 …… 那些走对应子代理）
- 你自己 5 分钟内能写完的小修改（比如改一行配置、贴一段示例代码到 chat）

## 新版标准三步

### Step 1 — `start`：派任务，毫秒级返回

```bash
aios code-helper start <task-name> "<完整任务描述>" [--timeout 1800] --json
```

返回 JSON：

```json
{
  "task": "pomodoro-tool",
  "pid": 13524,
  "cwd": "/root/aios-cc-workspace/pomodoro-tool",
  "status_path": "/root/aios-cc-workspace/pomodoro-tool/_run/status.json",
  "stdout_path": "/root/aios-cc-workspace/pomodoro-tool/_run/stdout.jsonl",
  "result_path": "/root/aios-cc-workspace/pomodoro-tool/_run/result.json",
  "started_at": 1712345678.12,
  "timeout_s": 1800
}
```

**这一步 < 1 秒**。watcher 已经在后台 spawn 了 `claude -p`，CC 开始干活了。

### Step 2 — 立刻给用户反馈 + 注册 1 分钟 cron poll

**先给用户一段反馈消息**，让他知道你已经派出去了、大概要多久、以后什么节奏给他汇报：

> 📤 已派给 Claude Code 处理：task=`pomodoro-tool`（pid 13524）
> 工作目录在服务器 `~/aios-cc-workspace/pomodoro-tool/`
> 估计 3-8 分钟，我每分钟会跟你同步一次进度，完成 / 失败 / 需要你确认时会立刻告诉你。

**然后立即用 `cron` 工具注册一个每分钟一次的 poll 回调**：

```text
cron schedule:
  expression:  */1 * * * *
  message:     【CC poll · pomodoro-tool】运行 `aios code-helper poll pomodoro-tool`，
               把输出原样转给用户。
               • 如果输出含 [DONE] → 转给用户 + 调用 cron remove 取消这条 cron
               • 如果输出含 [FAILED] / [CANCELLED] → 转给用户 + cron remove
               • 如果输出含 [NEEDS_CONFIRMATION] → 把 CC 在问的问题转给用户，cron 保留
                 等用户回复后用 `aios code-helper start pomodoro-tool "<用户的回复>"` 续接
               • 如果输出含 [RUNNING] → 转给用户作为进度同步，cron 保留
```

> 具体 cron 工具的参数语法看 nanobot 的 `cron` skill；上面是语义描述，不是字面命令。

### Step 3 — cron 触发时 poll，按标记决定

每次 cron 触发，你只要：

```bash
aios code-helper poll <task-name>
```

就会拿到一段**人 / Master 双友好的进度摘要**，例如运行中：

```
🔄 [RUNNING] task=pomodoro-tool  status=running  elapsed=92s
📁 已写文件 (4): app.py, requirements.txt, start.sh, index.html
🔧 工具调用 7 次
   · Write: index.html  (1s ago)
   · Bash: pip install Flask  (12s ago)
   · Edit: app.py  (35s ago)
💬 CC 最新反馈:
   正在创建前端模板，包含番茄钟主界面...
```

完成时：

```
✅ [DONE] task=pomodoro-tool  status=done  elapsed=312s
   duration=298500ms  cost=$0.1820
📁 已写文件 (6): app.py, requirements.txt, start.sh, index.html, README.md, .gitignore
🔧 工具调用 18 次
   · Bash: bash start.sh  (3s ago)
   · Write: README.md  (24s ago)
💬 CC 总结:
   Flask 番茄钟应用已完成 ……
📎 CC task: pomodoro-tool  (续接同一名字即可继续)
--- final ---
... 完整 final_text ...
```

需要确认时（CC 在 final_text 里问了问题）：

```
❓ [NEEDS_CONFIRMATION] task=pomodoro-tool  status=running  elapsed=180s
📁 已写文件 (3): ...
💬 CC 最新反馈:
   端口 5006 似乎被另一个服务占用，要换成 5007 吗？
⚠️  CC 在等你确认 — 把上面问题转给用户，等用户回复后用 `aios code-helper start pomodoro-tool "<用户的回复>"` 续接
```

失败时：

```
❌ [FAILED] task=pomodoro-tool  status=failed  elapsed=1800s
⚠️  error: runner timeout after 1800s
📁 已写文件 (5): ...
🔧 工具调用 23 次
```

> **看到 `[DONE]` / `[FAILED]` / `[CANCELLED]` 就 cron remove 那条回调**，否则
> 它会一直 poll 一个已完成任务，浪费每分钟一次的执行。

## 子命令速查

| 命令 | 用途 |
|---|---|
| `aios code-helper start <task> "<prompt>" [--timeout SEC] [--json]` | 派任务，毫秒级返回 |
| `aios code-helper poll <task>` | 友好进度摘要 + `[DONE]/[FAILED]/[NEEDS_CONFIRMATION]/[RUNNING]` 标记。**主要工作命令** |
| `aios code-helper status <task> [--json]` | 原始 status.json（debug 用，poll 已经够看） |
| `aios code-helper result <task> [--json]` | 完成后的完整 result.json（含完整 final_text） |
| `aios code-helper logs <task> [--tail 50]` | 看原始 stream-json 日志（debug 用） |
| `aios code-helper wait <task> [--timeout 60]` | 阻塞等到 done 或 timeout，**不要在 nanobot exec 里用这个**（会撞 120s 超时） |
| `aios code-helper cancel <task>` | SIGTERM 杀掉 watcher |
| `aios code-helper list [--running]` | 列出所有任务（或只列还在跑的） |

## `<task-name>` 命名规则

正则 `^[a-z0-9][a-z0-9-]{0,63}$`（小写字母 / 数字 / 短横线；不能以 `-` 开头；≤64 字符）。

- 好：`hello-py`、`refactor-user-auth`、`fix-login-404`、`pomodoro-tool`
- 坏：`Hello Py`（大写 + 空格）、`task_1`（下划线）、`-foo`（起始破折号）

## 何时用旧名续接 / 何时换新名

**保持同名**（用户消息出现下面任一信号）：
- "继续 / 再 / 改 / 加 / 让它 / 刚才 / 那个 / 上次"
- 用户回复了 `[NEEDS_CONFIRMATION]` 弹出来的问题
- 用户没切话题，还在聊同一个东西

**换新名**：
- 用户明确说"新任务" / "换一个" / "从头做个别的"
- 完全不相关的话题

**不确定 → 用旧名**。Claude Code 容忍多余上下文，但没法凭空补丢失的上下文。

每次 `start` 后，在反馈消息末尾写一行 `📎 CC task: <名字>`，下一轮自己抄回来。

## 安全边界（wrapper 自动注入，你不用管）

`aios code-helper` 在 spawn `claude` 子进程时已经自动套了三层约束，你 / 用户 / CC 都改不了：

1. **cwd 隔离**：每个 task 的 cwd = `~/aios-cc-workspace/<task>/`，物理上跟
   `/claude/aios` 主项目分开。CC 默认只能写这个目录。
2. **System prompt 注入**（`--append-system-prompt`）：CC 自带 prompt 末尾
   追加一段硬约束 —— 不准越界、不准动 `/claude/aios`、不准 `sudo` /
   `systemctl` / `psql` AIOS 数据库、不准 `rm -rf` / `git push --force`
   外部目录、不准读 `~/.claude/` `~/.aws/` `.env` 等机密。
3. **Workspace 级 `CLAUDE.md`**：`~/aios-cc-workspace/CLAUDE.md` 写了同一份
   边界约定，CC 启动时会自动从 cwd 往上找到它，跟 #2 互为兜底。

**Permission**：默认 `--permission-mode bypassPermissions`，CC 不会卡在
"是否允许 Edit hello.py?" 之类的确认上 —— headless 模式下那种确认会让任务
直接停在原地。`IS_SANDBOX=1` 让 root 用户也能用这个模式。

> ⚠️ **不要**在 description 里要求 CC 干越界的事（比如"修改 /claude/aios/aios/cli.py
> 然后重启 systemd 服务"）。CC 会拒绝，浪费一轮调用。AIOS 自己的代码改动
> 由用户在本地编辑后通过 `bash deploy/deploy.sh` 发版，**不该走 code_helper**。

## 完整流程示例

**Round 1**：用户「用 cc 帮我开发一个番茄钟小工具，部署在服务器上让我能打开，
不要影响在跑的服务」

```bash
aios code-helper start pomodoro-tool "用 Flask 写一个简单的番茄钟 Web 应用，部署在 ~/aios-cc-workspace/pomodoro-tool/。
要求：
1. 监听 5xxx 端口（先用 netstat / ss 探测一下哪个端口空闲，避免冲突 5000/5005/5006/5432/8000/22 之类常用端口和 AIOS 在用的端口）
2. 主页有 25min 工作 + 5min 休息切换、开始/暂停/重置按钮、当前剩余时间
3. 写一个 start.sh，里面 venv + pip install + nohup 后台启动，输出 PID 到 pomodoro.pid
4. 写一个 stop.sh，根据 pomodoro.pid kill
5. README.md 说明怎么开启 / 怎么访问 / 怎么停
6. 不要 sudo、不要碰 systemd、不要动其他端口" --timeout 1800 --json
```

立即给用户反馈：

> 📤 已派给 Claude Code 处理 task=`pomodoro-tool`（pid 13524）
> 工作目录：`~/aios-cc-workspace/pomodoro-tool/`
> 预计 3-8 分钟。我每分钟会跟你同步一次进度，完成 / 失败 / 需要你确认时会立刻告诉你。
>
> 📎 CC task: **pomodoro-tool**

注册 cron `*/1 * * * *` 回调（见 Step 2）。

**Round 2**（一分钟后 cron 触发）：

```bash
aios code-helper poll pomodoro-tool
```

输出 `[RUNNING]`，把摘要原样转给用户：

> 🔄 进度（pomodoro-tool · 92s）
>
> 已写：app.py、requirements.txt
> 当前在做：Bash `pip install Flask`
> CC 反馈：正在搭后端框架 ……

**Round N**（第 N 分钟，cron 又触发）：

```bash
aios code-helper poll pomodoro-tool
```

输出 `[DONE]`，转给用户 + 调 cron remove 那条回调：

> ✅ pomodoro-tool 完成（耗时 5min12s，$0.18）
>
> 文件：app.py、start.sh、stop.sh、templates/index.html、requirements.txt、README.md
> 启动：`bash ~/aios-cc-workspace/pomodoro-tool/start.sh`
> 访问：http://你的服务器:5077（CC 探测到的空闲端口）
> 停止：`bash ~/aios-cc-workspace/pomodoro-tool/stop.sh`
>
> 📎 CC task: **pomodoro-tool**（要继续改就用同一个名字 `aios code-helper start pomodoro-tool "..."`）

## 如果用户没等到 cron 又来催"咋样了"

不要重新 start！直接手动 poll 一次：

```bash
aios code-helper poll <task-name>
```

把输出转给用户。

## 出错排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `task already running` | 上一次 start 还在跑 | `poll` 看进度，或 `cancel` 后重启 |
| `claude CLI not in PATH` | claude CLI 没装 | `npm install -g @anthropic-ai/claude-code` |
| `invalid task name` | task 名违反正则 | 改成 kebab-case（小写 + 短横线） |
| poll 一直 `[RUNNING]` 几分钟没新进展 | CC 卡在某个工具 / 网络慢 | 看 `aios code-helper logs <task> --tail 30` 找原因；必要时 `cancel` 后用更细的 prompt 重启 |
| `[FAILED] runner timeout after 1800s` | 超过 30min 默认上限 | 重启同名 task（自动续接）+ 加 `--timeout 3600`；或者拆成更小任务 |
| `[FAILED] claude exited 1` | claude 内部失败 | `aios code-helper logs <task> --tail 100` 看 stderr；多半是认证 / 网络 |
| CC 在 final_text 说"我无法 / 不允许 ..." | 撞到了上面"安全边界"硬约束 | 改任务描述绕开（让 CC 在 cwd 内写脚本而不是直接改 AIOS 源码） |

## 四条铁律

1. **永远用 `start` + cron poll**，不要再用旧的同步 `--task X "desc"` 形式
2. **start 之后立刻给用户反馈**：派出去了 / 估计多久 / 会按节奏汇报
3. **延续任务必须用同一 `<task-name>`**，不同 = 失忆从头 = 浪费用户时间和钱
4. **写代码 / 改文件 / 跑脚本默认走这里** — 自己写一段超过 5-10 行的代码塞回复，
   就是在烧主 API 的钱；除非用户明确说"你直接给我贴代码"，否则委托 CC
