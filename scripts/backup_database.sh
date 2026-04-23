#!/bin/bash
# 数据库备份脚本
# 用途：定期备份 SQLite 数据库，防止数据丢失
#
# 使用方法：
#   手动备份：./scripts/backup_database.sh
#   定时备份：添加到 crontab
#     */30 * * * * cd /claude/aios && ./scripts/backup_database.sh >> logs/backup.log 2>&1

set -e

# 配置
DB_FILE="native_ai_os.db"
BACKUP_DIR="backups"
MAX_BACKUPS=100  # 保留最近100个备份

# 检查数据库文件是否存在
if [ ! -f "$DB_FILE" ]; then
    echo "错误：数据库文件不存在: $DB_FILE"
    exit 1
fi

# 创建备份目录
mkdir -p "$BACKUP_DIR"

# 生成备份文件名（时间戳）
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/native_ai_os_${TIMESTAMP}.db"

# 执行备份（使用 sqlite3 的 .backup 命令，保证一致性）
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始备份数据库..."
sqlite3 "$DB_FILE" ".backup '$BACKUP_FILE'"

# 检查备份是否成功
if [ -f "$BACKUP_FILE" ]; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 备份成功: $BACKUP_FILE (大小: $BACKUP_SIZE)"

    # 统计备份数量
    BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/*.db 2>/dev/null | wc -l | tr -d ' ')
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 当前备份总数: $BACKUP_COUNT"

    # 清理旧备份（保留最近的 N 个）
    if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 清理旧备份..."
        ls -1t "$BACKUP_DIR"/*.db | tail -n +$((MAX_BACKUPS + 1)) | xargs rm -f
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 已清理，保留最近 $MAX_BACKUPS 个备份"
    fi
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 备份失败！"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 备份完成"
