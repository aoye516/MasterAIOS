"""Morning brief: 规则化生成"今天天气 + 穿衣建议 + 个人健康提醒"。

完全规则化，不依赖 LLM。输入是 amap.weather() 拿到的当前天气 dict，
可选个人健康标签，输出一段中文 Markdown 文本。
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# 温度分档
# ---------------------------------------------------------------------------

def dress_for_temperature(t: float) -> str:
    if t < 0:
        return "羽绒服 + 厚毛衣 + 加绒裤，戴帽子手套围巾"
    if t < 5:
        return "羽绒服 / 厚棉服 + 毛衣 + 厚裤"
    if t < 10:
        return "厚外套 / 风衣 + 毛衣 + 长裤"
    if t < 15:
        return "风衣 / 夹克 + 长袖 + 长裤"
    if t < 20:
        return "薄外套 / 衬衫 + 长袖 / 长裤"
    if t < 25:
        return "长袖衬衫 / 卫衣 + 薄长裤，凉快可换短袖"
    if t < 30:
        return "短袖 + 薄长裤 / 短裤"
    if t < 35:
        return "短袖 + 短裤，注意防晒"
    return "短袖透气 + 短裤，强烈防晒，别长时间户外"


# ---------------------------------------------------------------------------
# 天气状况附加项
# ---------------------------------------------------------------------------

_RAIN_KEYS = ("雨", "阵雨", "雷阵雨")
_SNOW_KEYS = ("雪",)
_FOG_KEYS = ("雾", "霾")
_DUST_KEYS = ("沙尘", "扬沙", "浮尘")


def weather_addons(weather_zh: str) -> list[str]:
    s = weather_zh or ""
    out: list[str] = []
    if any(k in s for k in _RAIN_KEYS):
        out.append("带伞，建议防水鞋")
    if any(k in s for k in _SNOW_KEYS):
        out.append("路滑，慢行；防滑鞋更稳")
    if any(k in s for k in _FOG_KEYS):
        out.append("能见度低，开车开雾灯；外出戴口罩")
    if any(k in s for k in _DUST_KEYS):
        out.append("沙尘天，戴口罩 + 护目镜，关好门窗")
    return out


# ---------------------------------------------------------------------------
# 风力 / 湿度
# ---------------------------------------------------------------------------

def parse_wind_level(wp: str) -> int:
    """高德 windpower 字段是字符串：'≤3' / '4-5' / '6-7' / '8' …
    取上界做粗略数字。"""
    s = (wp or "").strip().lstrip("≤<")
    if "-" in s:
        s = s.split("-")[-1]
    try:
        return int(s)
    except ValueError:
        return 0


def wind_addons(wp: str) -> list[str]:
    lvl = parse_wind_level(wp)
    if lvl >= 6:
        return [f"大风{wp}级，避免高空作业，长发扎起"]
    if lvl >= 4:
        return [f"风{wp}级，外套加件防风层"]
    return []


def humidity_addons(humidity_str: str, temperature: float) -> list[str]:
    try:
        h = float(humidity_str or 0)
    except ValueError:
        return []
    if h >= 80 and temperature >= 25:
        return ["湿度 ≥ 80%，闷热感强，注意补水"]
    if h >= 80 and temperature < 10:
        return ["湿度高 + 低温，体感更冷，多穿一件"]
    if h <= 30:
        return ["空气干燥，多喝水，护肤品别忘"]
    return []


# ---------------------------------------------------------------------------
# 个人健康标签
# ---------------------------------------------------------------------------

def health_tips(tags: list[str], weather_zh: str, temperature: float) -> list[str]:
    """根据用户标签 + 今日天气，给 1-3 条饮食 / 起居小提示。"""
    out: list[str] = []
    tags = tags or []

    if "uric_acid_high" in tags:
        if temperature >= 25:
            out.append("尿酸偏高：今天热，午餐避开海鲜啤酒火锅，多喝白水（≥ 2 L）")
        else:
            out.append("尿酸偏高：低嘌呤饮食，避海鲜/红肉/内脏/浓汤/豆浆，多喝水")

    if "hypertension" in tags:
        out.append("血压偏高：低盐少油，午晚餐前测一次")

    if "diabetes" in tags:
        out.append("血糖关注：少甜少白米面，餐后 30 分钟散步")

    if any(k in (weather_zh or "") for k in _RAIN_KEYS) and "joint_pain" in tags:
        out.append("阴雨天关节易不适，注意保暖膝盖")

    return out


# ---------------------------------------------------------------------------
# 顶层入口
# ---------------------------------------------------------------------------

def render_morning_brief(
    *,
    place_name: str,
    weather: dict[str, Any],
    user_health_tags: list[str] | None = None,
    user_name: str | None = None,
    forecast_today: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成一段早间播报。

    weather: amap.weather(adcode, kind='base') 返回列表的第 0 项 dict。
    forecast_today: 可选，amap.weather(..., kind='all').forecasts[0].casts[0]，
                    用来补"白天/夜里"温差信息。
    返回 dict: { markdown, plain, dress, addons, health_tips, raw }
    """
    user_health_tags = user_health_tags or []
    province = weather.get("province", "")
    city = weather.get("city", "")
    weather_zh = weather.get("weather", "未知")
    try:
        temperature = float(weather.get("temperature", 0))
    except (TypeError, ValueError):
        temperature = 0.0
    wind_dir = weather.get("winddirection", "")
    wind_pow = weather.get("windpower", "")
    humidity = weather.get("humidity", "")
    report_t = weather.get("reporttime", "")

    dress = dress_for_temperature(temperature)
    addons = (
        weather_addons(weather_zh)
        + wind_addons(wind_pow)
        + humidity_addons(humidity, temperature)
    )
    tips = health_tips(user_health_tags, weather_zh, temperature)

    # 早晚温差（如有 forecast）
    diurnal_line = ""
    if forecast_today:
        try:
            day_t = float(forecast_today.get("daytemp", 0))
            night_t = float(forecast_today.get("nighttemp", 0))
            if day_t and night_t:
                diff = day_t - night_t
                diurnal_line = (
                    f"\n📊 今天 白天 {day_t:.0f}° / 夜间 {night_t:.0f}°"
                    + (f"，**温差 {diff:.0f}°**，记得加件外套" if diff >= 10 else "")
                )
        except (TypeError, ValueError):
            pass

    greeting = f"早，{user_name}！" if user_name else "早！"

    md_lines: list[str] = []
    md_lines.append(f"# ☀️ {greeting}今天的小播报")
    md_lines.append("")
    md_lines.append(f"📍 **{place_name}** · {province}{city}")
    md_lines.append(
        f"🌡️ {temperature:.0f}°C {weather_zh}，{wind_dir}风 {wind_pow}级，"
        f"湿度 {humidity}%（{report_t}）"
    )
    if diurnal_line:
        md_lines.append(diurnal_line.strip())
    md_lines.append("")
    md_lines.append("👔 **穿衣建议**")
    md_lines.append(f"- {dress}")
    for a in addons:
        md_lines.append(f"- {a}")
    if tips:
        md_lines.append("")
        md_lines.append("💧 **今天对你**")
        for t in tips:
            md_lines.append(f"- {t}")

    markdown = "\n".join(md_lines)

    # plain text 版本（去掉 markdown 标记，给不支持 md 的 channel 用）
    plain_lines = [
        f"{greeting}今天的小播报",
        f"{place_name} · {province}{city}",
        f"{temperature:.0f}°C {weather_zh}，{wind_dir}风 {wind_pow}级，湿度 {humidity}%",
    ]
    if diurnal_line:
        plain_lines.append(diurnal_line.strip().replace("**", "").replace("📊 ", ""))
    plain_lines.append(f"穿衣：{dress}")
    plain_lines.extend([f"· {a}" for a in addons])
    if tips:
        plain_lines.extend([f"提醒：{t}" for t in tips])
    plain = "\n".join(plain_lines)

    return {
        "markdown": markdown,
        "plain": plain,
        "dress": dress,
        "addons": addons,
        "health_tips": tips,
        "place": place_name,
        "temperature": temperature,
        "weather": weather_zh,
    }
