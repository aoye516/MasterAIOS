"""Database helpers for the wellbeing sub-agent.

Two domains:
- Habits + checkins: 重复性打卡 + streak 计算
- Health logs:       数值型时序，按 metric 聚合
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from aios.pg import PgClient


HABIT_STATUSES = ("active", "paused", "archived")
SCHEDULE_PRESETS = ("daily", "weekly", "workdays", "weekends")
COMMON_METRICS = (
    "weight", "uric_acid", "blood_pressure_sys", "blood_pressure_dia",
    "heart_rate", "sleep_hours", "steps", "mood",
)


# =============================================================================
# Habits
# =============================================================================

@dataclass
class Habit:
    id: int
    user_id: int | None
    name: str
    description: str | None
    schedule: str
    target_per_period: int
    reminder_time: time | None
    status: str
    notes: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "description": self.description,
            "schedule": self.schedule,
            "target_per_period": self.target_per_period,
            "reminder_time": self.reminder_time.isoformat() if self.reminder_time else None,
            "status": self.status,
            "notes": self.notes,
        }


def _row_to_habit(row) -> Habit:
    return Habit(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        description=row["description"],
        schedule=row["schedule"],
        target_per_period=row["target_per_period"],
        reminder_time=row["reminder_time"],
        status=row["status"],
        notes=row["notes"],
    )


async def upsert_habit(
    pg: PgClient,
    *,
    user_id: int | None,
    name: str,
    description: str | None = None,
    schedule: str = "daily",
    target_per_period: int = 1,
    reminder_time: time | None = None,
    notes: str | None = None,
) -> Habit:
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO habits (user_id, name, description, schedule,
                                target_per_period, reminder_time, notes, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (user_id, name) DO UPDATE SET
                description       = COALESCE(EXCLUDED.description, habits.description),
                schedule          = EXCLUDED.schedule,
                target_per_period = EXCLUDED.target_per_period,
                reminder_time     = COALESCE(EXCLUDED.reminder_time, habits.reminder_time),
                notes             = COALESCE(EXCLUDED.notes, habits.notes),
                updated_at        = NOW()
            RETURNING *
            """,
            user_id, name, description, schedule,
            target_per_period, reminder_time, notes,
        )
        return _row_to_habit(row)


async def find_habit(pg: PgClient, *, user_id: int | None, name: str) -> Habit | None:
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM habits WHERE user_id IS NOT DISTINCT FROM $1 AND name = $2",
            user_id, name,
        )
        return _row_to_habit(row) if row else None


async def list_habits(
    pg: PgClient, *, user_id: int | None, status: str | None = "active",
) -> list[Habit]:
    async with pg.acquire() as conn:
        if status:
            rows = await conn.fetch(
                """
                SELECT * FROM habits
                WHERE user_id IS NOT DISTINCT FROM $1 AND status = $2
                ORDER BY name
                """,
                user_id, status,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM habits WHERE user_id IS NOT DISTINCT FROM $1 ORDER BY name",
                user_id,
            )
        return [_row_to_habit(r) for r in rows]


async def update_habit_status(
    pg: PgClient, *, user_id: int | None, name: str, status: str,
) -> bool:
    if status not in HABIT_STATUSES:
        raise ValueError(f"status must be one of {HABIT_STATUSES}")
    async with pg.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE habits SET status = $3, updated_at = NOW()
            WHERE user_id IS NOT DISTINCT FROM $1 AND name = $2
            """,
            user_id, name, status,
        )
        return result.endswith(" 1")


# =============================================================================
# Checkins
# =============================================================================

async def add_checkin(
    pg: PgClient,
    *,
    habit_id: int,
    user_id: int | None,
    when: datetime | None = None,
    count: int = 1,
    notes: str | None = None,
) -> int:
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO habit_checkins (habit_id, user_id, done_at, count, notes)
            VALUES ($1, $2, COALESCE($3, NOW()), $4, $5)
            RETURNING id
            """,
            habit_id, user_id, when, count, notes,
        )
        return int(row["id"])


async def today_checkin_count(
    pg: PgClient, *, habit_id: int, today: date | None = None,
) -> int:
    """今天已经打卡几次（按 user 当地日期…暂用 server local）。"""
    today = today or date.today()
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(count), 0) AS n
            FROM habit_checkins
            WHERE habit_id = $1
              AND done_at::date = $2
            """,
            habit_id, today,
        )
        return int(row["n"] or 0)


async def compute_streak(
    pg: PgClient, *, habit_id: int, today: date | None = None,
) -> int:
    """计算 daily 习惯的连续打卡天数（从 today 往前）。

    注意：这里只关心"那天是否打过卡（≥1 次）"，不区分 target_per_period。
    weekly / cron 习惯目前用同样口径，但语义可能略有偏差，先这样。
    """
    today = today or date.today()
    async with pg.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT done_at::date AS d
            FROM habit_checkins
            WHERE habit_id = $1
              AND done_at::date <= $2
            ORDER BY d DESC
            LIMIT 365
            """,
            habit_id, today,
        )
    days = [r["d"] for r in rows]
    if not days:
        return 0
    streak = 0
    cursor = today
    for d in days:
        if d == cursor:
            streak += 1
            cursor = cursor - timedelta(days=1)
        elif d == cursor + timedelta(days=1):
            # 重复同一天，跳过
            continue
        else:
            break
    return streak


async def list_recent_checkins(
    pg: PgClient,
    *,
    habit_id: int,
    limit: int = 30,
) -> list[dict[str, Any]]:
    async with pg.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, done_at, count, notes
            FROM habit_checkins
            WHERE habit_id = $1
            ORDER BY done_at DESC
            LIMIT $2
            """,
            habit_id, limit,
        )
        return [
            {
                "id": r["id"],
                "done_at": r["done_at"],
                "count": r["count"],
                "notes": r["notes"],
            }
            for r in rows
        ]


# =============================================================================
# Health logs
# =============================================================================

async def add_health_log(
    pg: PgClient,
    *,
    user_id: int | None,
    metric: str,
    value: float | Decimal,
    unit: str | None = None,
    when: datetime | None = None,
    notes: str | None = None,
) -> int:
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO health_logs (user_id, metric, value, unit, recorded_at, notes)
            VALUES ($1, $2, $3, $4, COALESCE($5, NOW()), $6)
            RETURNING id
            """,
            user_id, metric, Decimal(str(value)), unit, when, notes,
        )
        return int(row["id"])


async def list_health_logs(
    pg: PgClient,
    *,
    user_id: int | None,
    metric: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    where = ["user_id IS NOT DISTINCT FROM $1"]
    args: list[Any] = [user_id]
    if metric:
        args.append(metric)
        where.append(f"metric = ${len(args)}")
    if since:
        args.append(since)
        where.append(f"recorded_at >= ${len(args)}")
    if until:
        args.append(until)
        where.append(f"recorded_at <= ${len(args)}")
    sql = f"""
        SELECT id, metric, value, unit, recorded_at, notes
        FROM health_logs
        WHERE {' AND '.join(where)}
        ORDER BY recorded_at DESC
        LIMIT {int(limit)}
    """
    async with pg.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [
            {
                "id": r["id"],
                "metric": r["metric"],
                "value": float(r["value"]),
                "unit": r["unit"],
                "recorded_at": r["recorded_at"],
                "notes": r["notes"],
            }
            for r in rows
        ]


async def health_stats(
    pg: PgClient,
    *,
    user_id: int | None,
    metric: str,
    since: datetime | None = None,
) -> dict[str, Any] | None:
    """对某个 metric 在时间窗内做 count/avg/min/max/最新值。"""
    where = ["user_id IS NOT DISTINCT FROM $1", "metric = $2"]
    args: list[Any] = [user_id, metric]
    if since:
        args.append(since)
        where.append(f"recorded_at >= ${len(args)}")
    sql = f"""
        WITH base AS (
            SELECT value, unit, recorded_at
            FROM health_logs
            WHERE {' AND '.join(where)}
        )
        SELECT
            COUNT(*)::bigint                                AS n,
            AVG(value)::numeric                             AS avg_v,
            MIN(value)                                      AS min_v,
            MAX(value)                                      AS max_v,
            (SELECT value       FROM base
                ORDER BY recorded_at DESC LIMIT 1)          AS latest_v,
            (SELECT recorded_at FROM base
                ORDER BY recorded_at DESC LIMIT 1)          AS latest_at,
            (SELECT unit        FROM base
                ORDER BY recorded_at DESC LIMIT 1)          AS latest_unit
        FROM base
    """
    async with pg.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if not row or row["n"] == 0:
        return None
    return {
        "metric": metric,
        "count": int(row["n"]),
        "avg": float(row["avg_v"]) if row["avg_v"] is not None else None,
        "min": float(row["min_v"]) if row["min_v"] is not None else None,
        "max": float(row["max_v"]) if row["max_v"] is not None else None,
        "latest": float(row["latest_v"]) if row["latest_v"] is not None else None,
        "latest_at": row["latest_at"],
        "unit": row["latest_unit"],
    }
