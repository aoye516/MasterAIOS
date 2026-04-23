#!/usr/bin/env bash
# AIOS server 一次性初始化脚本
# 用法（在服务器上执行）：
#   bash /claude/aios/deploy/server_setup.sh
#
# 功能：
#   1. 装 uv（Python 包管理器）
#   2. 装 Node.js 22 + claude CLI（@anthropic-ai/claude-code）
#   3. 用 git submodule 拉 vendor/nanobot
#   4. 创建 .venv，editable 安装 vendor/nanobot 和 aios
#   5. 检查 .env 是否就绪（不会创建）
#   6. 安装 systemd unit，但不自动 start（最后由用户手动 start）
#
# 幂等：可重复跑，已存在的资源会跳过
set -euo pipefail

ROOT="${AIOS_ROOT:-/claude/aios}"
echo "==> AIOS server setup @ $ROOT"
cd "$ROOT"

# -------- 1. 系统依赖 --------
if ! command -v git >/dev/null; then
  echo "[1/6] installing git..."
  apt-get update && apt-get install -y git
else
  echo "[1/6] git ✓"
fi

if ! command -v curl >/dev/null; then
  apt-get install -y curl
fi

# -------- 2. Node.js 22 + claude CLI --------
if ! command -v node >/dev/null || [ "$(node -v | cut -dv -f2 | cut -d. -f1)" -lt 18 ]; then
  echo "[2/6] installing Node.js 22..."
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
else
  echo "[2/6] node $(node -v) ✓"
fi

if ! command -v claude >/dev/null; then
  echo "[2/6] installing @anthropic-ai/claude-code..."
  npm install -g @anthropic-ai/claude-code
else
  echo "[2/6] claude CLI $(claude --version 2>&1 | head -1) ✓"
fi

# -------- 3. uv --------
if ! command -v uv >/dev/null; then
  echo "[3/6] installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv 默认装到 ~/.local/bin
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "[3/6] uv $(uv --version) ✓"

# -------- 4. git submodule (vendor/nanobot) --------
if [ ! -f vendor/nanobot/pyproject.toml ]; then
  echo "[4/6] init submodule vendor/nanobot..."
  git submodule update --init --recursive
else
  echo "[4/6] vendor/nanobot ✓"
fi

# -------- 5. venv + editable installs --------
if [ ! -d .venv ]; then
  echo "[5/6] creating .venv (Python 3.12)..."
  uv venv .venv --python 3.12
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "[5/6] installing vendor/nanobot + aios (editable)..."
uv pip install -e vendor/nanobot
uv pip install -e .

# -------- 6. .env + systemd --------
if [ ! -f .env ]; then
  echo
  echo "❌ .env 不存在！请先在 $ROOT/.env 配好以下变量再继续："
  echo "   DATABASE_URL=postgresql://aios@localhost:5432/aios"
  echo "   SILICONFLOW_API_KEY=..."
  echo "   FEISHU_APP_ID=..."
  echo "   FEISHU_APP_SECRET=..."
  echo "   ANTHROPIC_API_KEY=...      # 可选，给 code_helper / claude CLI 用"
  echo
  exit 1
fi
echo "[6/6] .env ✓"

# 装 systemd unit（覆盖式，不会自动启动）
echo "[6/6] installing systemd unit..."
install -m 0644 deploy/aios.service /etc/systemd/system/aios.service
systemctl daemon-reload
echo "[6/6] systemd unit installed ✓ （未启动）"

# -------- 完成提示 --------
cat <<'EOF'

✅ AIOS server setup 完成。

下一步（手动执行，一条一条来）：

  systemctl enable aios            # 开机自启
  systemctl start aios             # 启动
  systemctl status aios            # 看状态
  journalctl -u aios -f            # 看实时日志

如果之前在跑 aios-ws.service，先确认旧 service 已停（不要并存）：

  systemctl status aios-ws         # 如果还在跑：
  systemctl stop aios-ws
  systemctl disable aios-ws        # 完全切到新 service 后再禁用

EOF
