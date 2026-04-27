#!/usr/bin/env bash
# AIOS — 一键切换 Master 主模型（环境变量驱动）
#
# 用法：
#   bash deploy/switch-model.sh <model-name>           # 切到指定模型
#   bash deploy/switch-model.sh --show                 # 显示当前模型
#   bash deploy/switch-model.sh --check <model-name>   # 只做 SF 探测，不切
#
# 例：
#   bash deploy/switch-model.sh deepseek-ai/DeepSeek-V4-Flash
#   bash deploy/switch-model.sh deepseek-ai/DeepSeek-V3.2
#   bash deploy/switch-model.sh Qwen/Qwen2.5-72B-Instruct
#
# 工作流：
#   1. 在远程对目标模型做一次真实 chat completion（10s timeout）
#   2. 失败 → 退出，不动配置
#   3. 成功 → sed 改 .env 里 LLM_MODEL_MAIN → systemctl restart aios → 验 active
#
# 默认从 AIOS_REMOTE 环境变量取目标主机；可被 --remote 覆盖。

set -euo pipefail

REMOTE="${AIOS_REMOTE:-}"
REMOTE_PATH="${AIOS_REMOTE_PATH:-/claude/aios}"
SERVICE="${AIOS_SERVICE:-aios}"

usage() {
  sed -n '1,/^# 默认从/p' "$0" | sed 's/^# \?//'
  exit "${1:-0}"
}

[[ $# -lt 1 ]] && usage 2

MODE="switch"
TARGET=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --show)  MODE="show"; shift ;;
    --check) MODE="check"; shift; TARGET="${1:-}"; shift ;;
    --remote) REMOTE="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    -*)      echo "unknown flag: $1" >&2; usage 2 ;;
    *)       TARGET="$1"; shift ;;
  esac
done

if [[ -z "$REMOTE" ]]; then
  echo "ERROR: AIOS_REMOTE not set, e.g. AIOS_REMOTE=root@1.2.3.4" >&2
  exit 2
fi

# ------------------------------------------------------------------
# Helper: SSH wrapper
# ------------------------------------------------------------------
remote() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE" "$@"; }

# ------------------------------------------------------------------
# show current
# ------------------------------------------------------------------
if [[ "$MODE" == "show" ]]; then
  echo "================================"
  echo "  AIOS current Master model"
  echo "================================"
  echo "  remote:    $REMOTE:$REMOTE_PATH"
  echo "  service:   $SERVICE"
  echo
  remote "grep -E '^LLM_MODEL_MAIN=' $REMOTE_PATH/.env || echo '(not set in .env)'"
  echo
  remote "grep -E 'model' $REMOTE_PATH/workspace/config.json | head -3"
  exit 0
fi

if [[ -z "$TARGET" ]]; then
  echo "ERROR: must give a model name (e.g. deepseek-ai/DeepSeek-V3.2)" >&2
  exit 2
fi

# ------------------------------------------------------------------
# Step 1: SF probe — 真实调一次 chat completion，确认模型可用
# ------------------------------------------------------------------
echo "================================"
echo "  AIOS · switch model"
echo "================================"
echo "  remote: $REMOTE:$REMOTE_PATH"
echo "  target: $TARGET"
echo
echo "[1/3] SF API 预检（10s timeout，避免切到挂掉的模型）..."

PROBE=$(remote "
KEY=\$(grep -E '^SILICONFLOW_API_KEY=' $REMOTE_PATH/.env | cut -d= -f2-)
BASE=\$(grep -E '^SILICONFLOW_BASE_URL=' $REMOTE_PATH/.env | cut -d= -f2-)
[[ -z \"\$BASE\" ]] && BASE='https://api.siliconflow.cn/v1'
curl -sS -m 10 -w '\nHTTP=%{http_code}\n' -X POST \"\$BASE/chat/completions\" \
  -H \"Authorization: Bearer \$KEY\" \
  -H 'Content-Type: application/json' \
  -d '{\"model\":\"$TARGET\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":8}' 2>&1
") || true

HTTP=$(echo "$PROBE" | grep -oE 'HTTP=[0-9]+' | tail -1 | cut -d= -f2)
BODY=$(echo "$PROBE" | grep -v '^HTTP=' | head -3)

if [[ "$HTTP" != "200" ]]; then
  echo "  ❌ 模型不可用 (HTTP=${HTTP:-timeout})"
  echo "  --- response ---"
  echo "$BODY" | head -5
  echo "  ----------------"
  echo
  echo "  常见原因：模型名拼错 / SF 还没开放 / 当前负载过高 / API key 失效"
  echo "  当前服务保持原模型不动；想强制切换，先解决 SF 那边的问题。"
  exit 1
fi

echo "  ✅ HTTP 200 — 模型可用，回复片段：$(echo "$BODY" | head -1 | head -c 120)..."

if [[ "$MODE" == "check" ]]; then
  echo
  echo "  (--check 模式，仅探测，不改配置/不重启)"
  exit 0
fi

# ------------------------------------------------------------------
# Step 2: 切 .env 的 LLM_MODEL_MAIN
# ------------------------------------------------------------------
echo "[2/3] 改 .env 里 LLM_MODEL_MAIN..."

# 注意 sed 分隔符用 |，因为 model 名里有 / —— 但要 escape | 本身（不会出现）。
ESC_TARGET=$(printf '%s' "$TARGET" | sed 's/[|&]/\\&/g')
remote "
set -e
cd $REMOTE_PATH
if grep -qE '^LLM_MODEL_MAIN=' .env; then
  sed -i \"s|^LLM_MODEL_MAIN=.*|LLM_MODEL_MAIN=$ESC_TARGET|\" .env
else
  echo 'LLM_MODEL_MAIN=$ESC_TARGET' >> .env
fi
echo '  before/after:'
grep -E '^LLM_MODEL_MAIN=' .env
"

# ------------------------------------------------------------------
# Step 3: restart + verify
# ------------------------------------------------------------------
echo "[3/3] 重启 $SERVICE 并验证..."

remote "
sudo systemctl restart $SERVICE
sleep 2
state=\$(systemctl is-active $SERVICE)
echo \"  service: \$state\"
if [[ \"\$state\" != 'active' ]]; then
  echo '  ❌ 启动失败！可能是 nanobot 配置解析错（比如环境变量没读到）'
  echo '  --- 最近日志 ---'
  journalctl -u $SERVICE -n 20 --no-pager
  exit 1
fi
"

echo
echo "✅ 完成。Master 现在跑 $TARGET"
echo
echo "下次想切回去：bash deploy/switch-model.sh <model>"
echo "查当前模型：    bash deploy/switch-model.sh --show"
