#!/usr/bin/env bash
# AIOS — local nanobot launcher
# 用法：
#   bash scripts/run_nanobot.sh                      # 起 gateway（默认，含飞书 channel）
#   bash scripts/run_nanobot.sh agent -m "你好"      # 单条消息冒烟
#   bash scripts/run_nanobot.sh status               # 查状态
#
# 它做三件事：
#   1. 加载 .env 到环境变量
#   2. activate venv
#   3. 把 -c workspace/config.json -w workspace/ 默认带上

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
  echo "ERROR: .env not found at $ROOT/.env" >&2
  exit 1
fi

# 加载 .env：每行 KEY=VALUE，忽略注释与空行
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ''|\#*) continue ;;
  esac
  # 去掉 export 前缀
  line="${line#export }"
  # KEY=VALUE
  key="${line%%=*}"
  val="${line#*=}"
  # 去掉首尾空白
  key="${key// /}"
  # 去掉 VALUE 两端的引号
  val="${val%\"}"; val="${val#\"}"
  val="${val%\'}"; val="${val#\'}"
  export "$key=$val"
done < .env

# venv
if [ ! -d .venv ]; then
  echo "ERROR: .venv not found, run: uv venv .venv --python 3.12 && uv pip install -e vendor/nanobot && uv pip install -e ." >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Make `aios` and venv pythons reachable from nanobot's exec tool subprocesses,
# whose env is otherwise minimal (HOME/LANG/TERM + bash -l profile PATH).
export AIOS_HOME="$ROOT"
export AIOS_PATH_APPEND="$ROOT/.venv/bin"

CONFIG="workspace/config.json"
WORKSPACE="workspace"

CMD="${1:-gateway}"
shift || true

case "$CMD" in
  agent|gateway|serve|status|onboard|channels|plugins|provider)
    exec nanobot "$CMD" -c "$CONFIG" -w "$WORKSPACE" "$@"
    ;;
  *)
    # 透传 nanobot 子命令（不用-c/-w）
    exec nanobot "$CMD" "$@"
    ;;
esac
