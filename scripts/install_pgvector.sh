#!/bin/bash
# 安装 pgvector 扩展
#
# 用途：在 PostgreSQL 数据库中启用向量相似度搜索
# 前提：PostgreSQL 16+ 已安装

set -e

echo "=== 安装 pgvector 扩展 ==="
echo ""

# 检查 PostgreSQL 版本
PG_VERSION=$(psql --version | grep -oP '\d+' | head -1)
echo "检测到 PostgreSQL 版本: $PG_VERSION"

if [ "$PG_VERSION" -lt 12 ]; then
    echo "❌ pgvector 需要 PostgreSQL 12 或更高版本"
    exit 1
fi

echo ""
echo ">>> 1. 安装系统依赖..."
sudo apt-get update
sudo apt-get install -y postgresql-server-dev-$PG_VERSION build-essential git

echo ""
echo ">>> 2. 克隆 pgvector 源代码..."
cd /tmp
if [ -d "pgvector" ]; then
    rm -rf pgvector
fi
git clone --branch v0.7.0 https://github.com/pgvector/pgvector.git
cd pgvector

echo ""
echo ">>> 3. 编译并安装..."
make
sudo make install

echo ""
echo ">>> 4. 清理临时文件..."
cd /tmp
rm -rf pgvector

echo ""
echo "✅ pgvector 扩展安装成功！"
echo ""
echo "下一步："
echo "1. 连接到数据库：sudo -u postgres psql -d aios_db"
echo "2. 启用扩展：CREATE EXTENSION IF NOT EXISTS vector;"
echo "3. 运行迁移脚本：./scripts/migrate_to_pgvector.sh"
echo ""
