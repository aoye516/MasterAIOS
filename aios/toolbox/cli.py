"""CLI sub-commands for the toolbox agent.

高德全家桶：weather / route / transit / metro-near / traffic-road / poi / geo / regeo
常用地点：where-add / where-list / where-rm
Mini-tools：calc / units / tz / summarize-url / recipe
"""

from __future__ import annotations

import argparse
import ast
import json
import operator as _op
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from aios.integrations.amap import AmapClient, AmapError
from aios.integrations.url_fetch import fetch_text
from aios.llm import chat as llm_chat
from aios.pg import PgClient
from aios.toolbox.db import (
    Place,
    delete_place,
    find_place,
    list_places,
    upsert_place,
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
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(f"unserializable: {type(o)}")


async def _resolve_place(
    pg: PgClient, amap: AmapClient, *, user_id: int | None, place: str,
) -> dict[str, Any]:
    """把用户输入的「place」（可能是别名 or 地址）解析成 {location, adcode, name}.

    优先级：
      1. places 表里已有的别名 → 直接用
      2. 否则当地址用，调 amap.geocode → 返回首条
    """
    p = await find_place(pg, user_id=user_id, alias=place)
    if p:
        return {
            "name": p.alias,
            "location": p.location,
            "longitude": p.longitude,
            "latitude": p.latitude,
            "adcode": p.adcode,
            "city": p.city,
            "formatted_address": p.formatted_address,
            "source": "places",
        }
    geos = await amap.geocode(place)
    if not geos:
        raise ValueError(f"地址解析失败：{place!r}")
    g = geos[0]
    return {
        "name": place,
        "location": g.get("location"),
        "longitude": float(g["location"].split(",")[0]),
        "latitude": float(g["location"].split(",")[1]),
        "adcode": g.get("adcode"),
        "city": g.get("city") or g.get("province"),
        "formatted_address": g.get("formatted_address"),
        "source": "geocode",
    }


# =============================================================================
# ping
# =============================================================================

async def cmd_ping(args: argparse.Namespace) -> int:  # noqa: ARG001
    _emit(args, {"agent": "toolbox", "status": "ok"}, ["toolbox: ok"])
    return 0


# =============================================================================
# Weather
# =============================================================================

async def cmd_weather(args: argparse.Namespace) -> int:
    async with PgClient() as pg, AmapClient() as amap:
        info = await _resolve_place(pg, amap, user_id=args.user_id, place=args.place)
        if not info.get("adcode"):
            print(f"WARN: 拿不到 adcode（{info['name']}），可能是不规范地址", flush=True)
            return 1
        kind = "all" if args.forecast else "base"
        rows = await amap.weather(info["adcode"], kind=kind)
        payload = {"place": info, "kind": kind, "weather": rows}
        if kind == "base" and rows:
            r = rows[0]
            pretty = [
                f"{info['name']} 现在天气：{r.get('weather')}，{r.get('temperature')}°C，"
                f"{r.get('winddirection')}风{r.get('windpower')}级，湿度 {r.get('humidity')}% "
                f"（{r.get('reporttime')}）"
            ]
        elif kind == "all" and rows:
            casts = rows[0].get("casts", [])
            pretty = [f"{info['name']} 未来 {len(casts)} 天："]
            for c in casts:
                pretty.append(
                    f"  {c.get('date')} ({c.get('week')})  日 {c.get('dayweather')} "
                    f"{c.get('daytemp')}° / 夜 {c.get('nightweather')} {c.get('nighttemp')}°"
                )
        else:
            pretty = ["(no weather data)"]
        _emit(args, payload, pretty)
    return 0


# =============================================================================
# Driving route
# =============================================================================

def _seconds_to_human(s: int | float) -> str:
    s = int(s)
    h, rem = divmod(s, 3600)
    m, _sec = divmod(rem, 60)
    if h:
        return f"{h}h{m}min"
    return f"{m}min"


async def cmd_route(args: argparse.Namespace) -> int:
    async with PgClient() as pg, AmapClient() as amap:
        o = await _resolve_place(pg, amap, user_id=args.user_id, place=args.origin)
        d = await _resolve_place(pg, amap, user_id=args.user_id, place=args.destination)
        route = await amap.driving_route(o["location"], d["location"])
        paths = route.get("paths", [])
        if not paths:
            _emit(args, {"origin": o, "destination": d, "route": route}, ["(no route)"])
            return 1
        p = paths[0]
        # v3 + extensions=all: duration / distance / tolls 都直接在 path 上（字符串）
        dur_s = int(float(p.get("duration", 0)))
        dist_m = int(float(p.get("distance", 0)))
        tolls = float(p.get("tolls", 0) or 0)
        traffic_lights = p.get("traffic_lights", "?")

        # 路况片段在 steps[].tmcs[]
        # tmc 字段：status (畅通/缓行/拥堵/严重拥堵/未知) + distance
        congested_dist_m = 0
        total_dist_m = 0
        congested_roads: list[str] = []
        for step in p.get("steps", []) or []:
            road = (step.get("road") or "").strip()
            for tmc in step.get("tmcs", []) or []:
                tdist = int(float(tmc.get("distance", 0)))
                status = str(tmc.get("status", ""))
                total_dist_m += tdist
                if status in ("拥堵", "严重拥堵"):
                    congested_dist_m += tdist
                    if road and road not in congested_roads:
                        congested_roads.append(road)

        congestion_ratio = (
            congested_dist_m / total_dist_m if total_dist_m > 0 else 0.0
        )
        if total_dist_m == 0:
            verdict = "未知"
        elif congestion_ratio < 0.05:
            verdict = "基本畅通"
        elif congestion_ratio < 0.20:
            verdict = "局部缓行"
        elif congestion_ratio < 0.40:
            verdict = "拥堵"
        else:
            verdict = "严重拥堵"

        payload = {
            "origin": o,
            "destination": d,
            "duration_s": dur_s,
            "duration_human": _seconds_to_human(dur_s),
            "distance_m": dist_m,
            "tolls_yuan": tolls,
            "traffic_lights": traffic_lights,
            "congested_distance_m": congested_dist_m,
            "total_traffic_distance_m": total_dist_m,
            "congestion_ratio": round(congestion_ratio, 3),
            "congestion_verdict": verdict,
            "congested_roads": congested_roads[:10],
        }
        pretty = [
            f"{o['name']} → {d['name']}",
            f"  全程 {dist_m / 1000:.1f} km，预计 {_seconds_to_human(dur_s)}，"
            f"过路费 {tolls:.0f} 元，红绿灯 {traffic_lights} 个",
        ]
        if total_dist_m > 0:
            pretty.append(
                f"  路况：{verdict}（拥堵 {congested_dist_m / 1000:.1f} km / "
                f"{total_dist_m / 1000:.1f} km，{congestion_ratio * 100:.0f}%）"
            )
            if congested_roads:
                pretty.append(f"  拥堵路段：{', '.join(congested_roads[:5])}")
        else:
            pretty.append("  路况：未拿到路况数据")
        _emit(args, payload, pretty)
    return 0


# =============================================================================
# Traffic on a named road
# =============================================================================

async def cmd_traffic_road(args: argparse.Namespace) -> int:
    async with AmapClient() as amap:
        try:
            info = await amap.traffic_status_road(name=args.name, city=args.city)
        except AmapError as e:
            payload = {
                "road": args.name,
                "city": args.city,
                "error": e.info,
                "infocode": e.infocode,
                "hint": (
                    "高德的 traffic-road 接口只支持规范市政道路名（如『中关村大街』『长安街』），"
                    "不支持俗称（『三环』）和高速（『京藏高速』）。"
                    "整条通勤路线的路况建议用 `aios toolbox route <起> <终>`，"
                    "它会按 steps 累计拥堵段。"
                ),
            }
            pretty = [
                f"{args.city} · {args.name}：查询失败 [{e.infocode}] {e.info}",
                f"  提示：{payload['hint']}",
            ]
            _emit(args, payload, pretty)
            return 2
        payload = {"road": args.name, "city": args.city, "info": info}
        eval_ = info.get("evaluation", {}) if info else {}
        desc = info.get("description", "(无描述)") if info else "(无数据)"
        pretty = [
            f"{args.city} · {args.name}",
            f"  {desc}",
            f"  畅通 {eval_.get('expedite', '?')} | "
            f"缓行 {eval_.get('congested', '?')} | "
            f"拥堵 {eval_.get('blocked', '?')}（{eval_.get('status', '?')}）",
        ]
        _emit(args, payload, pretty)
    return 0


# =============================================================================
# Transit (公交 + 地铁综合规划)
# =============================================================================

# 高德 transit segments 里 status / type 的常见值
_VEHICLE_TYPE_ZH = {
    "BUS": "公交",
    "SUBWAY": "地铁",
    "METRO_RAIL": "地铁",
    "RAILWAY": "城际铁路",
    "TAXI": "出租车",
}


def _summarize_transit(transit: dict) -> dict:
    """从一个 transit 方案里抽出关键摘要。"""
    cost_yuan = float(transit.get("cost", 0) or 0)
    duration_s = int(float(transit.get("duration", 0) or 0))
    walking_m = int(float(transit.get("walking_distance", 0) or 0))
    distance_m = int(float(transit.get("distance", 0) or 0))

    # 走过的"线"序列（地铁优先标号，公交标线名）
    lines: list[str] = []
    transfers = 0
    for seg in transit.get("segments", []) or []:
        bus = seg.get("bus") or {}
        for line in bus.get("buslines", []) or []:
            name = line.get("name") or ""
            vtype = line.get("type") or ""
            label = name or _VEHICLE_TYPE_ZH.get(vtype, vtype) or "未知线路"
            if label and (not lines or lines[-1] != label):
                lines.append(label)
                if len(lines) > 1:
                    transfers += 1
        # 城际/动车段（amap 每段都会返一个 railway 占位，name=None 时是空，跳过）
        railway = seg.get("railway") or {}
        rname = railway.get("name")
        if rname:
            if not lines or lines[-1] != rname:
                lines.append(rname)
                if len(lines) > 1:
                    transfers += 1

    return {
        "duration_s": duration_s,
        "duration_human": _seconds_to_human(duration_s),
        "cost_yuan": cost_yuan,
        "walking_distance_m": walking_m,
        "distance_m": distance_m,
        "transfers": transfers,
        "lines": lines,
    }


async def cmd_transit(args: argparse.Namespace) -> int:
    async with PgClient() as pg, AmapClient() as amap:
        o = await _resolve_place(pg, amap, user_id=args.user_id, place=args.origin)
        d = await _resolve_place(pg, amap, user_id=args.user_id, place=args.destination)
        # 城市优先用 origin 的 city，缺省北京
        city = o.get("city") or d.get("city") or args.city or "北京"
        cityd = d.get("city") if (d.get("city") and d.get("city") != city) else None
        try:
            route = await amap.transit_route(
                o["location"], d["location"],
                city=city, cityd=cityd,
                strategy=args.strategy,
            )
        except AmapError as e:
            payload = {"origin": o, "destination": d,
                       "error": e.info, "infocode": e.infocode}
            _emit(args, payload, [f"公交规划失败 [{e.infocode}] {e.info}"])
            return 2

        transits = route.get("transits", []) or []
        if not transits:
            _emit(args,
                  {"origin": o, "destination": d, "transits": []},
                  ["未找到公交/地铁方案（可能距离过近，建议步行或自驾）"])
            return 1
        summaries = [_summarize_transit(t) for t in transits[: args.top]]
        payload = {
            "origin": o,
            "destination": d,
            "city": city,
            "strategy": args.strategy,
            "count": len(summaries),
            "transits": summaries,
        }
        pretty = [f"{o['name']} → {d['name']}（{city}），{len(summaries)} 个方案："]
        for i, s in enumerate(summaries, 1):
            line_chain = " → ".join(s["lines"]) if s["lines"] else "(全程步行)"
            pretty.append(
                f"  方案 {i}：{s['duration_human']}，"
                f"{s['cost_yuan']:.1f} 元，"
                f"换乘 {s['transfers']} 次，"
                f"步行 {s['walking_distance_m']} m"
            )
            pretty.append(f"           路线：{line_chain}")
        _emit(args, payload, pretty)
    return 0


# =============================================================================
# Metro stations near
# =============================================================================

async def cmd_metro_near(args: argparse.Namespace) -> int:
    async with PgClient() as pg, AmapClient() as amap:
        info = await _resolve_place(pg, amap, user_id=args.user_id, place=args.place)
        try:
            pois = await amap.poi_around(
                info["location"],
                types="150500",  # 高德 POI type: 地铁站
                radius=args.radius,
                page_size=args.limit,
            )
        except AmapError as e:
            payload = {"place": info, "error": e.info, "infocode": e.infocode}
            _emit(args, payload, [f"地铁站查询失败 [{e.infocode}] {e.info}"])
            return 2
        payload = {"place": info, "radius_m": args.radius, "stations": pois}
        if not pois:
            _emit(args, payload,
                  [f"{info['name']} {args.radius}m 内没有地铁站"])
            return 0
        pretty = [f"{info['name']} {args.radius}m 内的地铁站（{len(pois)}）："]
        for p in pois:
            dist = p.get("distance", "?")
            pretty.append(
                f"  - {p.get('name')}  约 {dist} m  "
                f"({p.get('address', '')})"
            )
        _emit(args, payload, pretty)
    return 0


# =============================================================================
# POI
# =============================================================================

async def cmd_poi(args: argparse.Namespace) -> int:
    async with AmapClient() as amap:
        pois = await amap.poi_search(
            keywords=args.keywords,
            region=args.region,
            page_size=args.limit,
        )
        payload = {"keywords": args.keywords, "region": args.region, "pois": pois}
        if not pois:
            _emit(args, payload, ["(no pois)"])
            return 0
        pretty = [f"{args.keywords}（{args.region or '不限地区'}）找到 {len(pois)} 条："]
        for p in pois:
            pretty.append(
                f"  - {p.get('name')}  [{p.get('type', '')}]  "
                f"{p.get('address', '')}  ({p.get('location', '')})"
            )
        _emit(args, payload, pretty)
    return 0


# =============================================================================
# Geocode / Regeocode
# =============================================================================

async def cmd_geo(args: argparse.Namespace) -> int:
    async with AmapClient() as amap:
        rows = await amap.geocode(args.address, city=args.city)
        payload = {"address": args.address, "city": args.city, "results": rows}
        if not rows:
            _emit(args, payload, ["(no match)"])
            return 1
        pretty = [f"{args.address} → {len(rows)} 条："]
        for g in rows[:5]:
            pretty.append(
                f"  - {g.get('formatted_address')}  ({g.get('location')})  "
                f"adcode={g.get('adcode')}"
            )
        _emit(args, payload, pretty)
    return 0


async def cmd_regeo(args: argparse.Namespace) -> int:
    async with AmapClient() as amap:
        info = await amap.regeocode(args.location)
        addr = info.get("formatted_address") if info else None
        payload = {"location": args.location, "info": info}
        pretty = [f"{args.location} → {addr or '(no match)'}"]
        _emit(args, payload, pretty)
    return 0


# =============================================================================
# Places (where-*)
# =============================================================================

async def cmd_where_add(args: argparse.Namespace) -> int:
    """根据地址 geocode 出经纬度后，落库为别名。"""
    async with PgClient() as pg, AmapClient() as amap:
        rows = await amap.geocode(args.address, city=args.city)
        if not rows:
            print(f"WARN: 地址解析失败：{args.address}", flush=True)
            return 1
        g = rows[0]
        lng_str, lat_str = g["location"].split(",")
        place = await upsert_place(
            pg,
            user_id=args.user_id,
            alias=args.alias,
            longitude=float(lng_str),
            latitude=float(lat_str),
            formatted_address=g.get("formatted_address"),
            adcode=g.get("adcode"),
            city=g.get("city") if g.get("city") else None,
            province=g.get("province") if g.get("province") else None,
        )
    payload = place.to_dict()
    pretty = [
        f"已记录别名「{place.alias}」 → {place.formatted_address} "
        f"({place.location}, adcode={place.adcode})",
    ]
    _emit(args, payload, pretty)
    return 0


async def cmd_where_list(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        places = await list_places(pg, user_id=args.user_id)
    payload = [p.to_dict() for p in places]
    if not places:
        _emit(args, payload, ["(尚无常用地点 — 用 `aios toolbox where-add <别名> <地址>` 录入)"])
        return 0
    pretty = [f"共 {len(places)} 个常用地点："]
    for p in places:
        pretty.append(f"  - {p.alias}：{p.formatted_address or p.location}")
    _emit(args, payload, pretty)
    return 0


async def cmd_where_rm(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        ok = await delete_place(pg, user_id=args.user_id, alias=args.alias)
    if not ok:
        print(f"WARN: 别名「{args.alias}」不存在", flush=True)
        return 1
    _emit(args, {"alias": args.alias, "deleted": True}, [f"已删除「{args.alias}」"])
    return 0


# =============================================================================
# Calc — safe eval
# =============================================================================

_ALLOWED_BIN = {
    ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul, ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv, ast.Mod: _op.mod, ast.Pow: _op.pow,
}
_ALLOWED_UN = {ast.UAdd: _op.pos, ast.USub: _op.neg}


def _safe_eval(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"unsupported constant: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op = _ALLOWED_BIN.get(type(node.op))
        if op is None:
            raise ValueError(f"unsupported op: {type(node.op).__name__}")
        return op(_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_UN.get(type(node.op))
        if op is None:
            raise ValueError(f"unsupported unary op: {type(node.op).__name__}")
        return op(_safe_eval(node.operand))
    raise ValueError(f"unsupported node: {type(node).__name__}")


async def cmd_calc(args: argparse.Namespace) -> int:
    expr = args.expression
    try:
        tree = ast.parse(expr, mode="eval")
        result = _safe_eval(tree)
    except Exception as e:
        print(f"ERROR: 表达式不合法 ({e})", flush=True)
        return 1
    payload = {"expression": expr, "result": result}
    _emit(args, payload, [f"{expr} = {result}"])
    return 0


# =============================================================================
# Units — common conversions
# =============================================================================
# 表达：所有单位先换算成基准（SI），再换算到目标
_LENGTH = {"m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001,
           "ft": 0.3048, "in": 0.0254, "mi": 1609.344, "yd": 0.9144}
_WEIGHT = {"kg": 1.0, "g": 0.001, "mg": 1e-6, "t": 1000.0,
           "lb": 0.45359237, "oz": 0.0283495}
_VOLUME = {"l": 1.0, "ml": 0.001, "m3": 1000.0, "gal": 3.785411784,
           "cup": 0.24, "floz": 0.0295735}
_TIME = {"s": 1.0, "min": 60.0, "h": 3600.0, "d": 86400.0}
_SPEED = {"ms": 1.0, "kmh": 1000 / 3600, "mph": 1609.344 / 3600, "kn": 1852 / 3600}

_UNIT_TABLES = {
    "length": _LENGTH, "weight": _WEIGHT, "mass": _WEIGHT,
    "volume": _VOLUME, "time": _TIME, "speed": _SPEED,
}


def _detect_kind(unit: str) -> str | None:
    u = unit.lower()
    for kind, table in _UNIT_TABLES.items():
        if u in table:
            return kind
    if u in {"c", "f", "k"}:
        return "temperature"
    return None


def _convert_temperature(value: float, src: str, dst: str) -> float:
    src, dst = src.lower(), dst.lower()
    # 先转 K
    k = {"c": value + 273.15, "f": (value - 32) * 5 / 9 + 273.15, "k": value}[src]
    return {"c": k - 273.15, "f": (k - 273.15) * 9 / 5 + 32, "k": k}[dst]


async def cmd_units(args: argparse.Namespace) -> int:
    src = args.from_unit
    dst = args.to_unit
    kind_src = _detect_kind(src)
    kind_dst = _detect_kind(dst)
    if not kind_src or not kind_dst or kind_src != kind_dst:
        print(f"ERROR: 不支持或类型不一致 {src} → {dst}", flush=True)
        return 1
    if kind_src == "temperature":
        result = _convert_temperature(args.value, src, dst)
    else:
        table = _UNIT_TABLES[kind_src]
        result = args.value * table[src.lower()] / table[dst.lower()]
    payload = {"value": args.value, "from": src, "to": dst, "result": result, "kind": kind_src}
    _emit(args, payload, [f"{args.value} {src} = {result:.6g} {dst}"])
    return 0


# =============================================================================
# Timezone — current time / convert across zones
# =============================================================================

DEFAULT_ZONES = ["Asia/Shanghai", "America/New_York", "America/Los_Angeles",
                 "Europe/London", "Europe/Berlin", "Asia/Tokyo", "UTC"]


async def cmd_tz(args: argparse.Namespace) -> int:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    zones = args.zones if args.zones else DEFAULT_ZONES
    try:
        if args.time:
            base_zone = ZoneInfo(args.from_zone or "Asia/Shanghai")
            base = datetime.fromisoformat(args.time).replace(tzinfo=base_zone)
        else:
            base = datetime.now(ZoneInfo("UTC"))

        results = {}
        for z in zones:
            try:
                local = base.astimezone(ZoneInfo(z))
                results[z] = local.isoformat()
            except ZoneInfoNotFoundError:
                results[z] = None
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        return 1

    payload = {
        "base_iso": base.isoformat(),
        "base_zone": str(base.tzinfo),
        "zones": results,
    }
    pretty = [f"基准 {payload['base_zone']}：{base.isoformat()}"]
    for z, iso in results.items():
        if iso:
            pretty.append(f"  {z:24s}  {iso}")
        else:
            pretty.append(f"  {z:24s}  (unknown timezone)")
    _emit(args, payload, pretty)
    return 0


# =============================================================================
# summarize-url — fetch + LLM summary, optional auto-save to mindscape
# =============================================================================

_SUMMARIZE_SYS = (
    "你是一个稍后读助手。读用户给你的网页文本，用中文输出 JSON："
    '{"title": "...", "summary": "200 字以内的摘要", '
    '"highlights": ["要点1", "要点2", "要点3"], '
    '"tags": ["标签1", "标签2"]}。'
    "summary 写人能直接读懂的中文，不要复述原文段落，提炼立场和事实。"
    "tags 2-4 个，小写中文短词。只输出 JSON，不要任何额外文字。"
)


async def cmd_summarize_url(args: argparse.Namespace) -> int:
    try:
        page = await fetch_text(args.url, max_chars=args.max_chars)
    except Exception as e:
        print(f"ERROR: fetch failed — {e}", flush=True)
        return 1

    if page.status >= 400:
        print(f"ERROR: HTTP {page.status} for {page.final_url}", flush=True)
        return 1

    if not page.text.strip():
        print(f"ERROR: empty body after extract — {page.final_url}", flush=True)
        return 1

    user_msg = (
        f"网页标题：{page.title or '(无)'}\n"
        f"网页 URL：{page.final_url}\n\n"
        f"正文（可能有截断）：\n{page.text}"
    )
    try:
        raw = await llm_chat(
            [
                {"role": "system", "content": _SUMMARIZE_SYS},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        print(f"ERROR: LLM call failed — {e}", flush=True)
        return 1

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Some models include ```json fences despite response_format; strip them.
        cleaned = raw.strip().lstrip("`").lstrip("json").strip().rstrip("`").strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            print(f"ERROR: LLM returned non-JSON: {raw[:200]}", flush=True)
            return 1

    payload = {
        "url": page.url,
        "final_url": page.final_url,
        "fetched_status": page.status,
        "truncated": page.truncated,
        "title": parsed.get("title") or page.title,
        "summary": parsed.get("summary", ""),
        "highlights": parsed.get("highlights", []),
        "tags": parsed.get("tags", []),
    }

    pretty = [
        f"📄 {payload['title']}",
        f"🔗 {payload['final_url']}",
        "",
        f"摘要：{payload['summary']}",
    ]
    if payload["highlights"]:
        pretty.append("")
        pretty.append("要点：")
        for h in payload["highlights"]:
            pretty.append(f"  · {h}")
    if payload["tags"]:
        pretty.append("")
        pretty.append("标签：" + " / ".join(payload["tags"]))
    pretty.append("")
    pretty.append(
        "↳ 可以让 mindscape 落库：aios mind note "
        f'"{(payload["summary"] or "")[:60]}..." '
        f"（手动加 --tags 和 source）"
    )

    _emit(args, payload, pretty)
    return 0


# =============================================================================
# recipe — 给定食材推荐菜谱（跨子代理协同：Master 负责先调 steward 拿食材）
# =============================================================================

_RECIPE_SYS = (
    "你是一个家庭菜谱助手。根据用户给的现有食材推荐 N 道家常菜。"
    "中文输出 JSON：{\"dishes\": [{\"name\": \"...\", \"need_extra\": [\"...\"], "
    "\"steps\": [\"步骤1\", \"步骤2\", ...], \"tags\": [\"快手/家常/汤/...\"]}, ...]}。"
    "原则："
    "(1) 优先用上 ingredients 里的东西；"
    "(2) need_extra 列额外要补的常见调料/辅料，不要把油盐酱醋这种默认都有的列上；"
    "(3) steps 写 3-6 步可执行的；"
    "(4) **严格忌口（铁律，违反就重选）**：avoid 列表里的食材**永远不能出现**在 name / need_extra / steps 任意位置；"
    "类别词扩展：'红肉' 指 猪肉/牛肉/羊肉；'海鲜' 指 虾/蟹/鱼/贝/鱿鱼；'内脏' 指 肝/腰/肠/心；"
    "'豆制品' 指 豆腐/豆浆/豆干。"
    "(5) 如果 ingredients 里包含被 avoid 的，**忽略**那些食材，用剩下的做菜，宁可少推一道也不要破例。"
    "只输出 JSON，不要任何额外文字。"
)


# avoid 类别词扩展（跟 _RECIPE_SYS 里的语义保持一致），用于服务端 post-filter
_AVOID_EXPAND = {
    "红肉": ["猪肉", "牛肉", "羊肉"],
    "海鲜": ["虾", "蟹", "鱼", "贝", "鱿鱼", "蛤", "蛎", "螺", "海鲜"],
    "内脏": ["肝", "腰", "肠", "心", "肚", "脑", "肺"],
    "豆制品": ["豆腐", "豆浆", "豆干", "腐竹", "豆皮"],
}


def _expand_avoid(avoid: list[str]) -> list[str]:
    out: list[str] = []
    for w in avoid:
        out.append(w)
        out.extend(_AVOID_EXPAND.get(w, []))
    return list(dict.fromkeys(out))


def _dish_violates_avoid(dish: dict[str, Any], avoid_words: list[str]) -> str | None:
    """返回触发的 avoid 词；没违反返回 None。"""
    blob = " ".join([
        str(dish.get("name", "")),
        " ".join(dish.get("need_extra") or []),
        " ".join(dish.get("steps") or []),
    ])
    for w in avoid_words:
        if w and w in blob:
            return w
    return None


async def _call_recipe_llm(user_msg: str, *, attempts: int = 2) -> dict:
    """LLM 偶尔会吐 malformed JSON（漏逗号/截断）。失败重试一次。"""
    last_err: Exception | None = None
    for _ in range(attempts):
        raw = await llm_chat(
            [
                {"role": "system", "content": _RECIPE_SYS},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.6,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            last_err = e
            cleaned = raw.strip().lstrip("`").lstrip("json").strip().rstrip("`").strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as e2:
                last_err = e2
                continue
    raise RuntimeError(f"LLM 连续返回非法 JSON：{last_err}")


async def cmd_recipe(args: argparse.Namespace) -> int:
    ingredients = [s.strip() for s in args.ingredients.split(",") if s.strip()]
    if not ingredients:
        print("ERROR: --ingredients 至少给一个", flush=True)
        return 1
    avoid = [s.strip() for s in (args.avoid or "").split(",") if s.strip()]
    diet = [s.strip() for s in (args.diet or "").split(",") if s.strip()]
    avoid_expanded = _expand_avoid(avoid)

    # 把被 avoid 命中的 ingredients 自动剔除（"鸡蛋,虾仁" + avoid 海鲜 → "鸡蛋"）
    ingredients_kept = [
        ing for ing in ingredients
        if not any(w in ing for w in avoid_expanded)
    ]
    if avoid_expanded and len(ingredients_kept) < len(ingredients):
        dropped = [ing for ing in ingredients if ing not in ingredients_kept]
        print(f"WARN: avoid 命中食材 {dropped}，已自动剔除", flush=True)
    if not ingredients_kept:
        print("ERROR: avoid 把所有食材都过滤掉了", flush=True)
        return 1

    user_msg_parts = [
        f"现有食材：{', '.join(ingredients_kept)}",
        f"想要 {args.count} 道菜。",
    ]
    if avoid_expanded:
        user_msg_parts.append(
            f"严格避开（含同类）：{', '.join(avoid_expanded)}。"
            "这些词不能出现在任何菜名 / 配料 / 步骤里。"
        )
    if diet:
        user_msg_parts.append(f"忌口标签：{', '.join(diet)}（按低嘌呤/低钠/低糖/素食的常识过滤）")
    if args.style:
        user_msg_parts.append(f"风格偏好：{args.style}")

    user_msg = "\n".join(user_msg_parts)

    try:
        parsed = await _call_recipe_llm(user_msg)
    except Exception as e:
        print(f"ERROR: LLM call failed — {e}", flush=True)
        return 1

    dishes = parsed.get("dishes", [])

    # post-filter：剔掉违反 avoid 的菜
    if avoid_expanded:
        clean_dishes: list[dict] = []
        violations: list[tuple[str, str]] = []
        for d in dishes:
            v = _dish_violates_avoid(d, avoid_expanded)
            if v:
                violations.append((d.get("name", "(无名)"), v))
            else:
                clean_dishes.append(d)
        # 如果全被过滤掉了，重试一次（带更强的提示）
        if not clean_dishes and violations:
            retry_msg = user_msg + (
                "\n\n上一轮你给出的菜全部违反了 avoid 规则（"
                + "，".join([f"《{n}》含'{w}'" for n, w in violations])
                + "）。这次必须只用我列出的食材，绝对不要引入 avoid 词。"
            )
            try:
                parsed = await _call_recipe_llm(retry_msg)
            except Exception as e:
                print(f"ERROR: LLM retry failed — {e}", flush=True)
                return 1
            dishes = parsed.get("dishes", [])
            clean_dishes = [d for d in dishes if not _dish_violates_avoid(d, avoid_expanded)]
        dishes = clean_dishes

    payload = {
        "ingredients": ingredients,
        "ingredients_used": ingredients_kept,
        "avoid": avoid,
        "avoid_expanded": avoid_expanded,
        "diet": diet,
        "style": args.style,
        "count": args.count,
        "dishes": dishes,
    }

    pretty = [
        f"用 {', '.join(ingredients)}" + (f"（避开 {', '.join(avoid)}）" if avoid else "")
        + (f"，忌口 {', '.join(diet)}" if diet else "") + f"，给你 {len(dishes)} 道菜：",
        "",
    ]
    for i, d in enumerate(dishes, 1):
        name = d.get("name", "(无名菜)")
        tags = d.get("tags", [])
        tags_s = f"  [{' / '.join(tags)}]" if tags else ""
        pretty.append(f"{i}. {name}{tags_s}")
        extra = d.get("need_extra") or []
        if extra:
            pretty.append(f"   还需要：{', '.join(extra)}")
        steps = d.get("steps") or []
        for j, s in enumerate(steps, 1):
            pretty.append(f"   {j}) {s}")
        pretty.append("")

    _emit(args, payload, pretty)
    return 0


# =============================================================================
# Argparse wiring
# =============================================================================

def add_subparsers(parent_sub: argparse._SubParsersAction) -> None:
    p_root = parent_sub.add_parser(
        "toolbox", help="Toolbox agent (高德全家桶 + 计算器/单位/时区 + 常用地点)"
    )
    sub = p_root.add_subparsers(dest="toolbox_cmd", required=True)

    # ping
    p_ping = sub.add_parser("ping", help="connectivity self-check")
    p_ping.add_argument("--json", action="store_true")

    # weather
    p_w = sub.add_parser("weather", help="天气（当前 / 4 天预报）")
    p_w.add_argument("place", help="别名（家/公司）或地址（北京东城）")
    p_w.add_argument("--forecast", action="store_true", help="拿 4 天预报，不传只拿当前")
    p_w.add_argument("--user-id", type=int, default=None)
    p_w.add_argument("--json", action="store_true")

    # route
    p_r = sub.add_parser("route", help="自驾路线（带耗时和路况）")
    p_r.add_argument("origin", help="起点 别名 / 地址")
    p_r.add_argument("destination", help="终点 别名 / 地址")
    p_r.add_argument("--user-id", type=int, default=None)
    p_r.add_argument("--json", action="store_true")

    # transit
    p_t = sub.add_parser("transit", help="公交 / 地铁综合路径规划（多方案 + 换乘 + 票价）")
    p_t.add_argument("origin", help="起点 别名 / 地址")
    p_t.add_argument("destination", help="终点 别名 / 地址")
    p_t.add_argument("--city", default=None,
                     help="城市，缺省自动取起点 city（最终缺省北京）")
    p_t.add_argument("--strategy", type=int, default=0,
                     help="0最快 1最便宜 2最少换乘 3最少步行 5不乘地铁")
    p_t.add_argument("--top", type=int, default=3, help="返回前 N 个方案")
    p_t.add_argument("--user-id", type=int, default=None)
    p_t.add_argument("--json", action="store_true")

    # metro-near
    p_mn = sub.add_parser("metro-near", help="附近的地铁站（按距离排序）")
    p_mn.add_argument("place", help="中心点 别名 / 地址")
    p_mn.add_argument("--radius", type=int, default=1000, help="搜索半径（米），默认 1000")
    p_mn.add_argument("--limit", type=int, default=10)
    p_mn.add_argument("--user-id", type=int, default=None)
    p_mn.add_argument("--json", action="store_true")

    # traffic-road
    p_tr = sub.add_parser("traffic-road", help="指定道路实时路况")
    p_tr.add_argument("name", help="道路名（如 中关村大街）")
    p_tr.add_argument("city", help="城市（如 北京）")
    p_tr.add_argument("--json", action="store_true")

    # poi
    p_p = sub.add_parser("poi", help="POI 搜索（餐厅/咖啡馆/加油站…）")
    p_p.add_argument("keywords")
    p_p.add_argument("--region", default=None, help="城市/区域，如 北京 / 朝阳区")
    p_p.add_argument("--limit", type=int, default=10)
    p_p.add_argument("--json", action="store_true")

    # geo / regeo
    p_g = sub.add_parser("geo", help="地址 → 经纬度 + adcode")
    p_g.add_argument("address")
    p_g.add_argument("--city", default=None)
    p_g.add_argument("--json", action="store_true")

    p_rg = sub.add_parser("regeo", help="经纬度 → 地址")
    p_rg.add_argument("location", help="lng,lat 格式，如 116.481488,39.990464")
    p_rg.add_argument("--json", action="store_true")

    # where-add / where-list / where-rm
    p_wa = sub.add_parser("where-add", help="录入常用地点别名（家/公司/健身房）")
    p_wa.add_argument("alias")
    p_wa.add_argument("address")
    p_wa.add_argument("--city", default=None, help="可选，提高 geocode 准确度")
    p_wa.add_argument("--user-id", type=int, default=None)
    p_wa.add_argument("--json", action="store_true")

    p_wl = sub.add_parser("where-list", help="列出常用地点")
    p_wl.add_argument("--user-id", type=int, default=None)
    p_wl.add_argument("--json", action="store_true")

    p_wr = sub.add_parser("where-rm", help="删除常用地点")
    p_wr.add_argument("alias")
    p_wr.add_argument("--user-id", type=int, default=None)
    p_wr.add_argument("--json", action="store_true")

    # calc
    p_c = sub.add_parser("calc", help="算式（仅 + - * / ** %% // 和数字）")
    p_c.add_argument("expression")
    p_c.add_argument("--json", action="store_true")

    # units
    p_u = sub.add_parser("units", help="单位换算（长度/重量/体积/时间/速度/温度）")
    p_u.add_argument("value", type=float)
    p_u.add_argument("from_unit", metavar="from")
    p_u.add_argument("to_unit", metavar="to")
    p_u.add_argument("--json", action="store_true")

    # tz
    p_tz = sub.add_parser("tz", help="时区当前时间 / 转换")
    p_tz.add_argument("--time", default=None,
                      help="ISO 时间（默认当前 UTC），例：2026-04-25T09:00:00")
    p_tz.add_argument("--from-zone", dest="from_zone", default=None,
                      help="解释 --time 的源时区，默认 Asia/Shanghai")
    p_tz.add_argument("--zones", nargs="*", default=None,
                      help="目标时区列表，默认 SH/NY/LA/LON/BER/TYO/UTC")
    p_tz.add_argument("--json", action="store_true")

    # summarize-url
    p_sm = sub.add_parser(
        "summarize-url",
        help="抓网页 + LLM 摘要（中文），可手动转给 mind note 落库做'稍后读'",
    )
    p_sm.add_argument("url", help="完整 URL，含 http(s)://")
    p_sm.add_argument("--max-chars", type=int, default=12000,
                      help="正文截断长度（默认 12000，太大浪费 token）")
    p_sm.add_argument("--json", action="store_true")

    # recipe
    p_rp = sub.add_parser(
        "recipe",
        help="根据食材推荐 N 道菜（跨子代理：Master 先调 steward 拿冰箱食材再传给我）",
    )
    p_rp.add_argument("--ingredients", required=True,
                      help='逗号分隔的食材，如 "鸡蛋,西红柿,土豆"')
    p_rp.add_argument("--avoid", default=None,
                      help='逗号分隔的要避开的食材，如 "海鲜,内脏"')
    p_rp.add_argument("--diet", default=None,
                      help='逗号分隔的忌口标签，如 "低嘌呤,低钠"（来自 USER.md）')
    p_rp.add_argument("--style", default=None,
                      help="风格偏好，如 '快手' / '清淡' / '川菜'")
    p_rp.add_argument("--count", type=int, default=3, help="返回菜品数（默认 3）")
    p_rp.add_argument("--json", action="store_true")


HANDLERS = {
    "ping": cmd_ping,
    "weather": cmd_weather,
    "route": cmd_route,
    "transit": cmd_transit,
    "metro-near": cmd_metro_near,
    "traffic-road": cmd_traffic_road,
    "poi": cmd_poi,
    "geo": cmd_geo,
    "regeo": cmd_regeo,
    "where-add": cmd_where_add,
    "where-list": cmd_where_list,
    "where-rm": cmd_where_rm,
    "calc": cmd_calc,
    "units": cmd_units,
    "tz": cmd_tz,
    "summarize-url": cmd_summarize_url,
    "recipe": cmd_recipe,
}


async def dispatch(args: argparse.Namespace) -> int:
    cmd = getattr(args, "toolbox_cmd")
    return await HANDLERS[cmd](args)
