"""CLI sub-commands for the wellbeing agent.

morning-brief / habit-* / log-*
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from aios.integrations.amap import AmapClient
from aios.pg import PgClient
from aios.toolbox.db import find_place
from aios.wellbeing.brief import render_morning_brief
from aios.wellbeing.db import (
    HABIT_STATUSES,
    SCHEDULE_PRESETS,
    add_checkin,
    add_health_log,
    compute_streak,
    find_habit,
    health_stats,
    list_habits,
    list_health_logs,
    list_recent_checkins,
    today_checkin_count,
    update_habit_status,
    upsert_habit,
)


# =============================================================================
# Helpers
# =============================================================================

def _emit(args: argparse.Namespace, payload: Any, pretty_lines: list[str]) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))
    else:
        for line in pretty_lines:
            print(line)


def _json_default(o: Any) -> Any:
    if isinstance(o, (date, datetime, time)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(f"unserializable: {type(o)}")


def _parse_time(s: str | None) -> time | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        return datetime.strptime(s, "%H:%M:%S").time()


def _parse_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _parse_tags(s: str | None) -> list[str]:
    return [t.strip() for t in (s or "").split(",") if t.strip()]


# =============================================================================
# ping
# =============================================================================

async def cmd_ping(args: argparse.Namespace) -> int:  # noqa: ARG001
    _emit(args, {"agent": "wellbeing", "status": "ok"}, ["wellbeing: ok"])
    return 0


# =============================================================================
# Morning brief
# =============================================================================

async def cmd_morning_brief(args: argparse.Namespace) -> int:
    """组合：查 places → 查天气 → 规则化生成播报。"""
    async with PgClient() as pg, AmapClient() as amap:
        # 1. resolve place（先查 places 别名，再 fallback geocode）
        p = await find_place(pg, user_id=args.user_id, alias=args.place)
        if p:
            place_name = p.alias
            adcode = p.adcode
            province = p.province
            city = p.city
            formatted = p.formatted_address
        else:
            geos = await amap.geocode(args.place)
            if not geos:
                print(f"ERROR: 地址解析失败：{args.place}", flush=True)
                return 1
            g = geos[0]
            place_name = args.place
            adcode = g.get("adcode")
            province = g.get("province") or ""
            city = g.get("city") or ""
            formatted = g.get("formatted_address")

        if not adcode:
            print(f"ERROR: 拿不到 adcode（{place_name}）", flush=True)
            return 1

        # 2. 当前天气
        lives = await amap.weather(adcode, kind="base")
        if not lives:
            print("ERROR: 高德 weather 没返回当前数据", flush=True)
            return 1
        live = lives[0]

        # 3. 今日预报（拿白天/夜温差）— 失败不致命
        forecast_today = None
        if not args.no_forecast:
            try:
                forecasts = await amap.weather(adcode, kind="all")
                if forecasts:
                    casts = forecasts[0].get("casts", []) or []
                    if casts:
                        forecast_today = casts[0]
            except Exception:
                forecast_today = None

        # 4. 个人健康标签
        tags = _parse_tags(args.tags)
        brief = render_morning_brief(
            place_name=place_name,
            weather=live,
            user_health_tags=tags,
            user_name=args.name,
            forecast_today=forecast_today,
        )

    payload = {
        "place": place_name,
        "formatted_address": formatted,
        "tags": tags,
        "brief": {k: v for k, v in brief.items() if k != "raw"},
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))
    elif args.format == "plain":
        print(brief["plain"])
    else:
        print(brief["markdown"])
    return 0


# =============================================================================
# Habits
# =============================================================================

async def cmd_habit_add(args: argparse.Namespace) -> int:
    rt = _parse_time(args.reminder_time)
    async with PgClient() as pg:
        h = await upsert_habit(
            pg,
            user_id=args.user_id,
            name=args.name,
            description=args.description,
            schedule=args.schedule,
            target_per_period=args.target,
            reminder_time=rt,
            notes=args.notes,
        )
    payload = h.to_dict()
    pretty = [
        f"habit #{h.id} 「{h.name}」 {h.schedule}"
        + (f"（每周期 {h.target_per_period} 次）" if h.target_per_period > 1 else "")
        + (f" · 提醒 {rt}" if rt else "")
    ]
    _emit(args, payload, pretty)
    return 0


async def cmd_habit_done(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        h = await find_habit(pg, user_id=args.user_id, name=args.name)
        if not h:
            print(f"ERROR: habit 「{args.name}」 不存在；先 `aios wellbeing habit-add` 创建", flush=True)
            return 1
        when = _parse_dt(args.when)
        ck_id = await add_checkin(
            pg, habit_id=h.id, user_id=args.user_id,
            when=when, count=args.count, notes=args.notes,
        )
        today_n = await today_checkin_count(pg, habit_id=h.id)
        streak = await compute_streak(pg, habit_id=h.id)
    payload = {
        "checkin_id": ck_id,
        "habit": h.to_dict(),
        "today_count": today_n,
        "target": h.target_per_period,
        "streak_days": streak,
    }
    progress = (
        f"{today_n}/{h.target_per_period}"
        if h.target_per_period > 1
        else "✓"
    )
    pretty = [
        f"打卡 #{ck_id} 「{h.name}」 今日 {progress}，连续 {streak} 天"
    ]
    _emit(args, payload, pretty)
    return 0


async def cmd_habit_list(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        habits = await list_habits(
            pg, user_id=args.user_id, status=args.status,
        )
        # 顺手算每个的 today/streak
        rows: list[dict[str, Any]] = []
        for h in habits:
            today_n = await today_checkin_count(pg, habit_id=h.id)
            streak = await compute_streak(pg, habit_id=h.id)
            rows.append({
                **h.to_dict(),
                "today_count": today_n,
                "streak_days": streak,
            })
    payload = {"count": len(rows), "habits": rows}
    if not rows:
        _emit(args, payload, [
            "(没有 active 习惯 — 用 `aios wellbeing habit-add <名字> [--schedule daily]` 加一个)"
        ])
        return 0
    pretty = [f"{len(rows)} 个 {args.status or 'all'} 习惯："]
    for r in rows:
        progress = (
            f"{r['today_count']}/{r['target_per_period']}"
            if r['target_per_period'] > 1
            else ("✓" if r['today_count'] else "·")
        )
        pretty.append(
            f"  - 「{r['name']}」 {r['schedule']} · 今日 {progress} · "
            f"streak {r['streak_days']} 天"
        )
    _emit(args, payload, pretty)
    return 0


async def cmd_habit_streak(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        h = await find_habit(pg, user_id=args.user_id, name=args.name)
        if not h:
            print(f"ERROR: habit 「{args.name}」 不存在", flush=True)
            return 1
        streak = await compute_streak(pg, habit_id=h.id)
        recent = await list_recent_checkins(pg, habit_id=h.id, limit=args.limit)
    payload = {
        "habit": h.to_dict(),
        "streak_days": streak,
        "recent": recent,
    }
    pretty = [
        f"「{h.name}」连续打卡 {streak} 天，最近 {len(recent)} 次："
    ]
    for r in recent:
        ts = r["done_at"].strftime("%Y-%m-%d %H:%M")
        notes = f"  -- {r['notes']}" if r["notes"] else ""
        pretty.append(f"  - {ts}  ×{r['count']}{notes}")
    _emit(args, payload, pretty)
    return 0


async def cmd_habit_pause(args: argparse.Namespace) -> int:
    return await _habit_set_status(args, "paused")


async def cmd_habit_resume(args: argparse.Namespace) -> int:
    return await _habit_set_status(args, "active")


async def cmd_habit_archive(args: argparse.Namespace) -> int:
    return await _habit_set_status(args, "archived")


async def _habit_set_status(args: argparse.Namespace, status: str) -> int:
    async with PgClient() as pg:
        ok = await update_habit_status(
            pg, user_id=args.user_id, name=args.name, status=status,
        )
    if not ok:
        print(f"ERROR: habit 「{args.name}」 不存在", flush=True)
        return 1
    _emit(
        args,
        {"name": args.name, "status": status},
        [f"「{args.name}」 → {status}"],
    )
    return 0


# =============================================================================
# Health logs
# =============================================================================

async def cmd_log(args: argparse.Namespace) -> int:
    when = _parse_dt(args.when)
    async with PgClient() as pg:
        lid = await add_health_log(
            pg, user_id=args.user_id, metric=args.metric,
            value=args.value, unit=args.unit, when=when, notes=args.notes,
        )
    payload = {
        "log_id": lid,
        "metric": args.metric,
        "value": args.value,
        "unit": args.unit,
        "recorded_at": (when or datetime.now()).isoformat(),
    }
    pretty = [
        f"log #{lid} {args.metric} = {args.value}"
        + (f" {args.unit}" if args.unit else "")
    ]
    _emit(args, payload, pretty)
    return 0


async def cmd_log_list(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        rows = await list_health_logs(
            pg, user_id=args.user_id, metric=args.metric, limit=args.limit,
        )
    payload = {"count": len(rows), "logs": rows}
    if not rows:
        _emit(args, payload,
              [f"({args.metric or '所有指标'} 暂无记录)"])
        return 0
    pretty = [f"最近 {len(rows)} 条 {args.metric or '指标'}："]
    for r in rows:
        ts = r["recorded_at"].strftime("%Y-%m-%d %H:%M")
        unit = r["unit"] or ""
        notes = f"  -- {r['notes']}" if r["notes"] else ""
        pretty.append(f"  - {ts}  {r['metric']} = {r['value']:g} {unit}{notes}")
    _emit(args, payload, pretty)
    return 0


async def cmd_log_stats(args: argparse.Namespace) -> int:
    since = None
    if args.days:
        since = datetime.now() - timedelta(days=args.days)
    async with PgClient() as pg:
        stats = await health_stats(
            pg, user_id=args.user_id, metric=args.metric, since=since,
        )
    if not stats:
        _emit(args, {"metric": args.metric, "stats": None},
              [f"({args.metric} 在指定时间窗内无数据)"])
        return 0
    payload = stats
    unit = stats["unit"] or ""
    days_lbl = f"近 {args.days} 天" if args.days else "全期"
    pretty = [
        f"{args.metric}（{days_lbl}）：n={stats['count']}，"
        f"latest {stats['latest']:g} {unit}（{stats['latest_at'].strftime('%Y-%m-%d')}）",
        f"  avg {stats['avg']:.2f}  min {stats['min']:g}  max {stats['max']:g}",
    ]
    _emit(args, payload, pretty)
    return 0


# =============================================================================
# Argparse wiring
# =============================================================================

def add_subparsers(parent_sub: argparse._SubParsersAction) -> None:
    p_root = parent_sub.add_parser(
        "wellbeing",
        help="Wellbeing agent (早间播报 + 习惯打卡 + 健康指标)",
    )
    sub = p_root.add_subparsers(dest="wellbeing_cmd", required=True)

    # ping
    p_ping = sub.add_parser("ping", help="connectivity self-check")
    p_ping.add_argument("--json", action="store_true")

    # morning-brief
    p_mb = sub.add_parser(
        "morning-brief",
        help="生成今日早间播报（天气 + 穿衣 + 个人健康提醒）",
    )
    p_mb.add_argument(
        "--place", default="家",
        help="地点别名 / 地址，默认 '家'",
    )
    p_mb.add_argument("--name", default=None, help="对你的称呼，比如 '敖烨'")
    p_mb.add_argument(
        "--tags", default=None,
        help="健康标签，逗号分隔，如 'uric_acid_high,hypertension'",
    )
    p_mb.add_argument("--no-forecast", action="store_true",
                      help="跳过 4 天预报，节省一次 amap 调用")
    p_mb.add_argument("--format", choices=["markdown", "plain"], default="markdown")
    p_mb.add_argument("--user-id", type=int, default=None)
    p_mb.add_argument("--json", action="store_true")

    # habit-add
    p_ha = sub.add_parser("habit-add", help="加一个新习惯")
    p_ha.add_argument("name", help="习惯名（如 '晨跑' / '吃药' / '喝水'）")
    p_ha.add_argument("--description", default=None)
    p_ha.add_argument("--schedule", default="daily",
                      help="daily / weekly / workdays / weekends / cron 表达式")
    p_ha.add_argument("--target", type=int, default=1,
                      help="每周期目标次数，喝水 8 杯就 8")
    p_ha.add_argument("--reminder-time", default=None, help="HH:MM，可选")
    p_ha.add_argument("--notes", default=None)
    p_ha.add_argument("--user-id", type=int, default=None)
    p_ha.add_argument("--json", action="store_true")

    # habit-done
    p_hd = sub.add_parser("habit-done", help="打卡一次")
    p_hd.add_argument("name")
    p_hd.add_argument("--count", type=int, default=1)
    p_hd.add_argument("--when", default=None, help="ISO 时间，默认 NOW")
    p_hd.add_argument("--notes", default=None)
    p_hd.add_argument("--user-id", type=int, default=None)
    p_hd.add_argument("--json", action="store_true")

    # habit-list
    p_hl = sub.add_parser("habit-list", help="列出习惯（含今日进度 + streak）")
    p_hl.add_argument("--status", default="active",
                      choices=list(HABIT_STATUSES) + [""])
    p_hl.add_argument("--user-id", type=int, default=None)
    p_hl.add_argument("--json", action="store_true")

    # habit-streak
    p_hs = sub.add_parser("habit-streak", help="单个习惯 streak + 最近打卡")
    p_hs.add_argument("name")
    p_hs.add_argument("--limit", type=int, default=14)
    p_hs.add_argument("--user-id", type=int, default=None)
    p_hs.add_argument("--json", action="store_true")

    # habit-pause / resume / archive
    for sub_name, help_text in [
        ("habit-pause", "暂停一个习惯（不影响历史 streak）"),
        ("habit-resume", "恢复 active"),
        ("habit-archive", "归档（彻底从主列表移除）"),
    ]:
        sp = sub.add_parser(sub_name, help=help_text)
        sp.add_argument("name")
        sp.add_argument("--user-id", type=int, default=None)
        sp.add_argument("--json", action="store_true")

    # log
    p_l = sub.add_parser("log", help="记一个健康指标值")
    p_l.add_argument(
        "metric",
        help="指标名（建议：weight/uric_acid/blood_pressure_sys/blood_pressure_dia/heart_rate/sleep_hours/steps/mood）",
    )
    p_l.add_argument("value", type=float)
    p_l.add_argument("--unit", default=None,
                     help="kg / umol/L / mmHg / bpm / h / step")
    p_l.add_argument("--when", default=None, help="ISO 时间，默认 NOW")
    p_l.add_argument("--notes", default=None)
    p_l.add_argument("--user-id", type=int, default=None)
    p_l.add_argument("--json", action="store_true")

    # log-list
    p_ll = sub.add_parser("log-list", help="列出健康日志")
    p_ll.add_argument("--metric", default=None, help="不传则列所有指标")
    p_ll.add_argument("--limit", type=int, default=20)
    p_ll.add_argument("--user-id", type=int, default=None)
    p_ll.add_argument("--json", action="store_true")

    # log-stats
    p_ls = sub.add_parser("log-stats", help="某指标 count/avg/min/max/最新值")
    p_ls.add_argument("metric")
    p_ls.add_argument("--days", type=int, default=None,
                      help="只统计近 N 天，不传 = 全期")
    p_ls.add_argument("--user-id", type=int, default=None)
    p_ls.add_argument("--json", action="store_true")


HANDLERS = {
    "ping": cmd_ping,
    "morning-brief": cmd_morning_brief,
    "habit-add": cmd_habit_add,
    "habit-done": cmd_habit_done,
    "habit-list": cmd_habit_list,
    "habit-streak": cmd_habit_streak,
    "habit-pause": cmd_habit_pause,
    "habit-resume": cmd_habit_resume,
    "habit-archive": cmd_habit_archive,
    "log": cmd_log,
    "log-list": cmd_log_list,
    "log-stats": cmd_log_stats,
}


async def dispatch(args: argparse.Namespace) -> int:
    cmd = getattr(args, "wellbeing_cmd")
    return await HANDLERS[cmd](args)
