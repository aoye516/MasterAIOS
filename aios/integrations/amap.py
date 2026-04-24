"""Shared client for Gaode (Amap) Web Service API.

Used by:
- toolbox sub-agent (instant queries: route / weather / poi / geocode)
- wellbeing.commute_watch (scheduled traffic monitoring)

Reference: legacy /claude/traffic_monitor/traffic_check.py — observed only,
not modified. We use our own AMAP_API_KEY from .env, not the one in that project.

API docs: https://lbs.amap.com/api/webservice/summary
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

import aiohttp

AMAP_BASE = "https://restapi.amap.com/v3"
AMAP_BASE_V5 = "https://restapi.amap.com/v5"


class AmapError(RuntimeError):
    """Amap API returned status != "1"."""

    def __init__(self, info: str, infocode: str, payload: dict[str, Any]) -> None:
        super().__init__(f"amap error [{infocode}] {info}")
        self.info = info
        self.infocode = infocode
        self.payload = payload


@dataclass
class AmapClient:
    """Async client around Amap Web Services. Reads AMAP_API_KEY from env if not given.

    Usage:
        async with AmapClient() as amap:
            wx = await amap.weather_now("110101")  # Beijing Dongcheng
    """

    api_key: str | None = None
    timeout_s: float = 15.0

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("AMAP_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "AMAP_API_KEY not set; add it to .env (apply at https://lbs.amap.com/)"
            )
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "AmapClient":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout_s)
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("AmapClient must be used as async context manager")
        params = {"key": self.api_key, **params}
        async with self._session.get(path, params=params) as resp:
            data = await resp.json(content_type=None)
        if data.get("status") != "1":
            raise AmapError(
                info=str(data.get("info", "unknown")),
                infocode=str(data.get("infocode", "")),
                payload=data,
            )
        return data

    # ---------- Geocode ----------

    async def geocode(self, address: str, city: str | None = None) -> list[dict]:
        """Address → (longitude, latitude). Returns list of {location, formatted_address, ...}."""
        params: dict[str, Any] = {"address": address}
        if city:
            params["city"] = city
        data = await self._get(f"{AMAP_BASE}/geocode/geo", params)
        return data.get("geocodes", [])

    async def regeocode(self, location: str, *, extensions: str = "base") -> dict:
        """(longitude,latitude) → address. location format: 'lng,lat'."""
        data = await self._get(
            f"{AMAP_BASE}/geocode/regeo",
            {"location": location, "extensions": extensions},
        )
        return data.get("regeocode", {})

    # ---------- Weather ----------

    async def weather(
        self, adcode: str, *, kind: Literal["base", "all"] = "base"
    ) -> list[dict]:
        """Get weather. kind='base' = current; kind='all' = 4-day forecast.

        adcode: city code, e.g. '110101' for Beijing Dongcheng. Use city_search() to look up.
        """
        data = await self._get(
            f"{AMAP_BASE}/weather/weatherInfo",
            {"city": adcode, "extensions": kind},
        )
        if kind == "base":
            return data.get("lives", [])
        return data.get("forecasts", [])

    # ---------- Driving ----------

    async def driving_route(
        self,
        origin: str,
        destination: str,
        *,
        strategy: int = 0,
    ) -> dict:
        """Driving directions (v3 + extensions=all).

        origin / destination format: 'lng,lat'.
        strategy:
          0  = 速度优先（默认，含路况）
          2  = 距离最短
          10 = 综合（含路况）
        Returns the route object; .paths[0].duration in seconds (string),
        and per-segment congestion in .paths[0].steps[].tmcs[].

        我们用 v3 而不是 v5，因为：
        - v3 的 extensions=all 一次返回 steps[].tmcs（路况片段），格式跟
          线上 /claude/traffic_monitor 项目用的一致，已经验证过半年
        - v5 的 tmcs 字段位置和命名不稳定，多次试不出来全套数据
        """
        data = await self._get(
            f"{AMAP_BASE}/direction/driving",
            {
                "origin": origin,
                "destination": destination,
                "extensions": "all",
                "strategy": strategy,
            },
        )
        return data.get("route", {})

    # ---------- Traffic ----------

    async def traffic_status_rectangle(
        self, rectangle: str, *, level: int = 5
    ) -> dict:
        """Real-time congestion in a rectangle: 'lng1,lat1;lng2,lat2'."""
        data = await self._get(
            f"{AMAP_BASE}/traffic/status/rectangle",
            {"rectangle": rectangle, "level": level, "extensions": "all"},
        )
        return data.get("trafficinfo", {})

    async def traffic_status_road(self, name: str, city: str) -> dict:
        """Real-time congestion on a named road in a given city."""
        data = await self._get(
            f"{AMAP_BASE}/traffic/status/road",
            {"name": name, "city": city, "extensions": "all"},
        )
        return data.get("trafficinfo", {})

    # ---------- POI search (v5 'place/text') ----------

    async def poi_search(
        self,
        keywords: str,
        *,
        region: str | None = None,
        city_limit: bool = True,
        page_size: int = 10,
    ) -> list[dict]:
        """Keyword POI search."""
        params: dict[str, Any] = {
            "keywords": keywords,
            "page_size": page_size,
            "city_limit": "true" if city_limit else "false",
        }
        if region:
            params["region"] = region
        data = await self._get(f"{AMAP_BASE_V5}/place/text", params)
        return data.get("pois", [])
