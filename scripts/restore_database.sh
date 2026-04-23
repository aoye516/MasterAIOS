#!/bin/bash
# 数据库恢复脚本
# 用途：从备份恢复数据库
#
# 使用方法：
#   列出所有备份：./scripts/restore_database.sh list
#   恢复最新备份：./scripts/restore_database.sh latest
#   恢复指定备份：./scripts/restore_database.sh <backup_filename>

set -e

DB_FILE="native_ai_os.db"
BACKUP_DIR="backups"

# 显示使用说明
usage() {
    echo "用法："
    echo "  $0 list                    - 列出所有备份"
    echo "  $0 latest                  - 恢复最新备份"
    echo "  $0 <backup_filename>       - 恢复指定备份"
    echo ""
    echo "示例："
    echo "  $0 list"
    echo "  $0 latest"
    echo "  $0 native_ai_os_20260312_115400.db"
    exit 1
}

# 列出所有备份
list_backups() {
    echo "可用的备份文件："
    echo "----------------------------------------"
    if [ ! -d "$BACKUP_DIR" ] || [ -z "$(ls -A $BACKUP_DIR/*.db 2>/dev/null)" ]; then
        echo "没有找到备份文件"
        exit 0
    fi

    ls -lth "$BACKUP_DIR"/*.db | awk '{print $9, "  大小:", $5, "  时间:", $6, $7, $8}'
    echo "----------------------------------------"
    echo "总计: $(ls -1 $BACKUP_DIR/*.db | wc -l | tr -d ' ') 个备份"
}

# 恢复数据库
restore_backup() {
    local backup_file="$1"

    # 检查备份文件是否存在
    if [ ! -f "$backup_file" ]; then
        echo "错误：备份文件不存在: $backup_file"
        exit 1
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 准备恢复数据库..."
    echo "  备份文件: $backup_file"
    echo "  目标文件: $DB_FILE"
    echo ""

    # 确认操作
    read -p "⚠️  这将覆盖当前数据库！是否继续？ (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        echo "已取消恢复操作"
        exit 0
    fi

    # 备份当前数据库（以防万一）
    if [ -f "$DB_FILE" ]; then
        CURRENT_BACKUP="$DB_FILE.before_restore_$(date +%Y%m%d_%H%M%S)"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 备份当前数据库到: $CURRENT_BACKUP"
        cp "$DB_FILE" "$CURRENT_BACKUP"
    fi

    # 恢复数据库
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始恢复..."
    cp "$backup_file" "$DB_FILE"

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 恢复完成！"
    echo ""
    echo "数据库已恢复。建议重启应用以加载新数据："
    echo "  pkill -f run_ws.py && nohup python3 run_ws.py >> logs/app.log 2>&1 &"
}

# 主逻辑
case "${1:-}" in
    list)
        list_backups
        ;;
    latest)
        LATEST_BACKUP=$(ls -1t "$BACKUP_DIR"/*.db 2>/dev/null | head -1)
        if [ -z "$LATEST_BACKUP" ]; then
            echo "错误：没有找到备份文件"
            exit 1
        fi
        restore_backup "$LATEST_BACKUP"
        ;;
    "")
        usage
        ;;
    *)
        # 如果是文件名，尝试恢复
        if [[ "$1" == *.db ]]; then
            # 如果是相对路径，添加备份目录前缀
            if [[ ! "$1" =~ ^/ ]]; then
                BACKUP_FILE="$BACKUP_DIR/$1"
            else
                BACKUP_FILE="$1"
            fi
            restore_backup "$BACKUP_FILE"
        else
            echo "错误：未知命令: $1"
            usage
        fi
        ;;
esac
