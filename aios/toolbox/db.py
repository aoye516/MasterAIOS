"""Database helpers for the toolbox sub-agent.

只有一张表：`places`（常用地点别名）。其它高德查询都是实时 API，不落库。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aios.pg import PgClient


@dataclass
class Place:
    id: int
    user_id: int | None
    alias: str
    formatted_address: str | None
    longitude: float
    latitude: float
    adcode: str | None
    city: str | None
    province: str | None

    @property
    def location(self) -> str:
        """高德 API 用的 'lng,lat' 格式。"""
        return f"{self.longitude:.6f},{self.latitude:.6f}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "alias": self.alias,
            "formatted_address": self.formatted_address,
            "longitude": self.longitude,
            "latitude": self.latitude,
            "location": self.location,
            "adcode": self.adcode,
            "city": self.city,
            "province": self.province,
        }


def _row_to_place(row) -> Place:
    return Place(
        id=row["id"],
        user_id=row["user_id"],
        alias=row["alias"],
        formatted_address=row["formatted_address"],
        longitude=float(row["longitude"]),
        latitude=float(row["latitude"]),
        adcode=row["adcode"],
        city=row["city"],
        province=row["province"],
    )


async def upsert_place(
    pg: PgClient,
    *,
    user_id: int | None,
    alias: str,
    longitude: float,
    latitude: float,
    formatted_address: str | None = None,
    adcode: str | None = None,
    city: str | None = None,
    province: str | None = None,
) -> Place:
    """同 (user_id, alias) 已存在则更新坐标，否则插入。"""
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO places (user_id, alias, formatted_address, longitude, latitude,
                                adcode, city, province, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            ON CONFLICT (user_id, alias) DO UPDATE SET
                formatted_address = EXCLUDED.formatted_address,
                longitude         = EXCLUDED.longitude,
                latitude          = EXCLUDED.latitude,
                adcode            = EXCLUDED.adcode,
                city              = EXCLUDED.city,
                province          = EXCLUDED.province,
                updated_at        = NOW()
            RETURNING *
            """,
            user_id, alias, formatted_address, longitude, latitude,
            adcode, city, province,
        )
        return _row_to_place(row)


async def find_place(pg: PgClient, *, user_id: int | None, alias: str) -> Place | None:
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM places WHERE user_id IS NOT DISTINCT FROM $1 AND alias = $2",
            user_id, alias,
        )
        return _row_to_place(row) if row else None


async def list_places(pg: PgClient, *, user_id: int | None) -> list[Place]:
    async with pg.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM places WHERE user_id IS NOT DISTINCT FROM $1 ORDER BY alias",
            user_id,
        )
        return [_row_to_place(r) for r in rows]


async def delete_place(pg: PgClient, *, user_id: int | None, alias: str) -> bool:
    async with pg.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM places WHERE user_id IS NOT DISTINCT FROM $1 AND alias = $2",
            user_id, alias,
        )
        return result.endswith(" 1")
