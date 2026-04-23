# 上游升级 SOP — vendor/nanobot

> AIOS 通过 git submodule 把 [`HKUDS/nanobot`](https://github.com/HKUDS/nanobot) 拉到 `vendor/nanobot/`。
> 本文档描述如何在 1 小时内完成「拉上游 → rebase → 测试 → 上线」。

---

## 0. 前提与铁律

- `vendor/nanobot` 是 git submodule，根目录 `.gitmodules` 登记。
- AIOS 只在 fork（建议命名 `<your-account>/nanobot`）里 commit，不直接改 upstream。
- 任何对 vendor/nanobot 的 patch **必须**在 commit message 加 `[AIOS-PATCH]` 前缀，方便 rebase 时识别。
- 万不得已的 patch 优先 PR 回 [HKUDS/nanobot](https://github.com/HKUDS/nanobot)，被合并后删本地 patch。
- 没有 patch 的话，rebase 等价于 fast-forward，零风险。

---

## 1. 第一次：把 fork 配上

只需在 fork 仓库初始化一次。

```bash
cd vendor/nanobot

# 当前 origin 指向 upstream（HKUDS/nanobot）
git remote -v

# 改成自己的 fork 当 origin，upstream 单独保留
git remote rename origin upstream
git remote add origin git@github.com:<your-account>/nanobot.git
git fetch origin
git checkout -B main origin/main || git checkout main

# 同步 AIOS 主仓库的 .gitmodules，让 url 指向 fork
cd ../..
# 编辑 .gitmodules：把 url 改成 git@github.com:<your-account>/nanobot.git
git config -f .gitmodules submodule.vendor/nanobot.url git@github.com:<your-account>/nanobot.git
git add .gitmodules
git commit -m "chore(vendor): point nanobot submodule to fork"
```

> 没 fork 也能跑（直接用 upstream）。一旦需要 `[AIOS-PATCH]` 就必须 fork。

---

## 2. 日常升级 SOP（≈ 30 min）

```bash
cd ~/Claude/AIOS

# Step 1: 看上游有什么新变化（不影响本地）
cd vendor/nanobot
git fetch upstream
git log --oneline HEAD..upstream/main          # 上游领先了什么
git log --oneline upstream/main..HEAD          # 我们本地有什么 patch

# Step 2: rebase（fast-forward 或处理冲突）
git checkout main
git rebase upstream/main
# 冲突：解决后 git add . && git rebase --continue

# Step 3: 推到自己 fork（可选，但建议）
git push origin main --force-with-lease

# Step 4: 回到主仓库登记 submodule 新指针
cd ../..
git add vendor/nanobot
git commit -m "chore(vendor): bump nanobot to $(cd vendor/nanobot && git rev-parse --short HEAD)"
```

### Step 5：本地装新版本 + 跑测

```bash
source .venv/bin/activate
uv pip install -e vendor/nanobot
uv pip install -e .

# AIOS 自定义层的回归
pytest tests/

# 端到端冒烟
bash scripts/run_nanobot.sh agent -m "你好，帮我跑 \`aios db-ping\`"
```

冒烟通过判定：
- `nanobot --version` 升级到目标版本
- `aios db-ping` 仍能连上 PG
- 飞书一句话回环正常（用 gateway）

### Step 6：上线

```bash
git push origin main
bash deploy/deploy.sh
```

deploy 脚本会自动 ssh 到生产服务器跑 `git submodule update` + `uv pip install -e vendor/nanobot` + `systemctl restart aios`。

---

## 3. 应急回滚

`vendor/nanobot` 的指针保存在主仓库的 git 历史里，回滚等于 git revert。

```bash
# 找到上一次稳定的 vendor 指针
git log --oneline -- vendor/nanobot

# 回滚 commit
git revert <bad-commit-sha>
git push origin main

# 服务器 redeploy
bash deploy/deploy.sh
```

如果服务器已挂，直接 ssh 上去：

```bash
ssh root@<your-server>
cd /claude/aios
git log --oneline -- vendor/nanobot       # 找上一个稳定指针
git checkout <good-vendor-sha> -- vendor/nanobot
cd vendor/nanobot && git submodule update --init --recursive
cd /claude/aios
source .venv/bin/activate
uv pip install -e vendor/nanobot
systemctl restart aios
```

---

## 4. 何时需要写 `[AIOS-PATCH]`

90% 的需求都能用 `workspace/skills/` 或 `workspace/config.json` 解决，不动 vendor。

只有以下情况才考虑 patch：

| 场景 | 处理 |
|---|---|
| 修 nanobot 本身的 bug | fork patch + 立即 PR 上游 |
| 加一个 nanobot 没有的核心 hook | 先开 issue 讨论，再决定 fork 还是绕路 |
| 临时关掉/补丁某个不稳定 feature | fork patch，commit 时标 `[AIOS-PATCH][TEMP]` |

每个 `[AIOS-PATCH]` commit 必须在 `docs/evolution/knowledge/aios-patches.md`（按需创建）里登记：
- 为什么打
- 是否已 PR 上游 / PR URL
- 上游合并后什么时候删

---

## 5. 自动化（可选）

`scripts/upgrade_nanobot.sh` 可以把 Step 1 ~ 4 串起来：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cd vendor/nanobot
git fetch upstream
echo "→ upstream 领先："; git log --oneline HEAD..upstream/main
read -rp "继续 rebase？[y/N] " ans
[ "$ans" = "y" ] || exit 0
git rebase upstream/main
cd ../..
git add vendor/nanobot
git commit -m "chore(vendor): bump nanobot to $(cd vendor/nanobot && git rev-parse --short HEAD)"
source .venv/bin/activate
uv pip install -e vendor/nanobot
pytest tests/
echo "✓ 本地通过。下一步：bash deploy/deploy.sh"
```

> 当前未默认提供，需要时再加。

---

## 6. 检查清单

每次升级完成后核对：

- [ ] `git submodule status` 显示新 commit
- [ ] `nanobot --version` 是新版本号
- [ ] `pytest tests/` 全绿
- [ ] 本地 gateway 能起，飞书回环正常
- [ ] 服务器 `journalctl -u aios -n 50` 无 ERROR
- [ ] 如有新 `[AIOS-PATCH]`，已登记 + 已发 PR
