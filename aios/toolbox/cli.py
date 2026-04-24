"""CLI sub-commands for the toolbox agent.

高德全家桶：weather / route / traffic-road / poi / geo / regeo
常用地点：where-add / where-list / where-rm
Mini-tools：calc / units / tz
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
    p_c = sub.add_parser("calc", help="算式（仅 + - * / ** % // 和数字）")
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


HANDLERS = {
    "ping": cmd_ping,
    "weather": cmd_weather,
    "route": cmd_route,
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
}


async def dispatch(args: argparse.Namespace) -> int:
    cmd = getattr(args, "toolbox_cmd")
    return await HANDLERS[cmd](args)
