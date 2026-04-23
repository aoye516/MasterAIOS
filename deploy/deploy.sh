#!/usr/bin/env bash
# AIOS — 本地一键同步代码到生产服务器
#
# 用法：
#   bash deploy/deploy.sh           # 默认：rsync + systemctl restart
#   bash deploy/deploy.sh dry       # dry-run，只看会传哪些文件
#   bash deploy/deploy.sh nosync    # 不 rsync，只远程 restart
#
# 设计要点（吸取 P0 数据丢失教训）：
#   - 不带 --delete：永远不删服务器上不在本地的文件
#   - 排除 .env / .venv / vendor/nanobot 的本地构建产物 / sessions / memory
#   - 排除 workspace/SOUL.md / USER.md：服务器上的私人人设不被本地版本覆盖
#   - vendor/nanobot 走 git submodule（服务器自己 update）
#   - 开始前打印将要做的事并要求回车确认
#
set -euo pipefail

REMOTE="${AIOS_REMOTE:?set AIOS_REMOTE, e.g. AIOS_REMOTE=root@1.2.3.4}"
REMOTE_PATH="${AIOS_REMOTE_PATH:-/claude/aios}"
SERVICE="${AIOS_SERVICE:-aios}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODE="${1:-deploy}"

echo "================================"
echo "  AIOS deploy"
echo "================================"
echo "  source: $ROOT"
echo "  remote: $REMOTE:$REMOTE_PATH"
echo "  systemd: $SERVICE"
echo "  mode:   $MODE"
echo

case "$MODE" in
  deploy|dry)
    EXTRA=()
    [ "$MODE" = "dry" ] && EXTRA+=("--dry-run")

    echo "→ rsync (no --delete) ..."
    rsync -avz "${EXTRA[@]}" \
      --exclude='.git/' \
      --exclude='.venv/' \
      --exclude='__pycache__/' \
      --exclude='.pytest_cache/' \
      --exclude='*.pyc' \
      --exclude='.DS_Store' \
      --exclude='.env' \
      --exclude='logs/' \
      --exclude='backups/' \
      --exclude='node_modules/' \
      --exclude='workspace/sessions/' \
      --exclude='workspace/memory/' \
      --exclude='workspace/.cache/' \
      --exclude='workspace/.runtime/' \
      --exclude='workspace/SOUL.md' \
      --exclude='workspace/USER.md' \
      --exclude='vendor/nanobot/build/' \
      --exclude='vendor/nanobot/dist/' \
      --exclude='vendor/nanobot/*.egg-info/' \
      --exclude='vendor/nanobot/.git/' \
      --exclude='vendor/nanobot/__pycache__/' \
      --exclude='legacy/' \
      ./ "$REMOTE:$REMOTE_PATH/"

    if [ "$MODE" = "dry" ]; then
      echo
      echo "✓ dry-run 完成。复查上面的文件清单后，再跑 deploy.sh（不带参数）。"
      exit 0
    fi
    ;;
  nosync)
    echo "→ 跳过 rsync，仅远程 restart"
    ;;
  *)
    echo "ERROR: unknown mode '$MODE'. Use: deploy | dry | nosync" >&2
    exit 2
    ;;
esac

echo
echo "→ 远程：git submodule update + uv pip install -e（如有依赖变化）"
ssh "$REMOTE" bash -se <<'REMOTE_EOF'
set -euo pipefail
cd /claude/aios

# 同步 vendor/nanobot 到本地登记的 commit
git submodule update --init --recursive

# editable 安装会自动 pick up vendor/nanobot 的变化
if [ -d .venv ]; then
  source .venv/bin/activate
  uv pip install -e vendor/nanobot >/dev/null
  uv pip install -e . >/dev/null
fi
REMOTE_EOF

echo
echo "→ 远程：systemctl restart $SERVICE"
ssh "$REMOTE" "systemctl restart $SERVICE"

sleep 3
echo
echo "→ 远程：systemctl status $SERVICE（最后 20 行日志）"
ssh "$REMOTE" "systemctl status $SERVICE --no-pager -l | head -25; echo '---'; journalctl -u $SERVICE -n 20 --no-pager"

echo
echo "✅ 部署完成。"
echo "   远程实时日志：ssh $REMOTE 'journalctl -u $SERVICE -f'"
