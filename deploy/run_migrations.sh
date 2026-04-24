#!/usr/bin/env bash
# AIOS — 幂等 migration runner
#
# 跑 aios/db/migrations/*.sql 里所有 migration 文件，按文件名排序。
# 每个 migration 通过文件名记录到 schema_migrations 表，跑过的不再重跑。
#
# 用法：
#   bash deploy/run_migrations.sh                 # 默认从 .env 读 DATABASE_URL
#   DATABASE_URL=postgresql://... bash deploy/run_migrations.sh
#
# 设计原则：
#   - 幂等：每个 .sql 内部用 IF NOT EXISTS / IF EXISTS；这里只跑一次保险
#   - 顺序确定：按文件名 sort 跑，文件名前缀 NNNN- 决定顺序
#   - 失败立即中断：set -e

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -f .env ] && [ -z "${DATABASE_URL:-}" ]; then
    set -a
    # shellcheck disable=SC1091
    . .env
    set +a
fi

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set (and not found in .env)" >&2
    exit 1
fi

# psql 不识别 SQLAlchemy 风格的 +asyncpg 后缀，去掉
PSQL_URL="${DATABASE_URL/postgresql+asyncpg/postgresql}"

MIGRATIONS_DIR="aios/db/migrations"
if [ ! -d "$MIGRATIONS_DIR" ]; then
    echo "ERROR: migrations dir not found: $MIGRATIONS_DIR" >&2
    exit 1
fi

echo "→ ensuring schema_migrations table"
psql "$PSQL_URL" -v ON_ERROR_STOP=1 -q <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
SQL

# 拉已应用的 migration 列表
APPLIED=$(psql "$PSQL_URL" -tAc "SELECT filename FROM schema_migrations ORDER BY filename")

count_total=0
count_applied=0
count_skipped=0

for f in "$MIGRATIONS_DIR"/*.sql; do
    [ -e "$f" ] || continue
    count_total=$((count_total + 1))
    base="$(basename "$f")"

    if echo "$APPLIED" | grep -qx "$base"; then
        echo "  - skip   $base (already applied)"
        count_skipped=$((count_skipped + 1))
        continue
    fi

    echo "  → apply  $base"
    psql "$PSQL_URL" -v ON_ERROR_STOP=1 -q -f "$f"
    psql "$PSQL_URL" -v ON_ERROR_STOP=1 -q -c \
        "INSERT INTO schema_migrations (filename) VALUES ('$base')"
    count_applied=$((count_applied + 1))
done

echo
echo "migrations: total=$count_total applied=$count_applied skipped=$count_skipped"
