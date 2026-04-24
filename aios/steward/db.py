"""Database helpers for the steward sub-agent (ledger + inventory).

Conventions:
    - amount is always stored non-negative; sign comes from `kind`
    - quantities default to 1 unless explicitly overridden
    - inventory_locations.path is materialized "卧室/床头柜/抽屉2"
    - get_or_create helpers auto-create accounts/categories/locations on first use
      so the user never has to predeclare anything
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from aios.pg import PgClient


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


# =============================================================================
# Accounts
# =============================================================================

DEFAULT_ACCOUNT_NAME = "默认账户"


async def get_or_create_account(
    pg: PgClient, *, user_id: int | None, name: str, kind: str = "cash"
) -> int:
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM ledger_accounts WHERE user_id IS NOT DISTINCT FROM $1 AND name = $2",
            user_id, name,
        )
        if row:
            return row["id"]
        return await conn.fetchval(
            "INSERT INTO ledger_accounts (user_id, name, kind) "
            "VALUES ($1, $2, $3) RETURNING id",
            user_id, name, kind,
        )


async def list_accounts(pg: PgClient, *, user_id: int | None = None) -> list[dict]:
    async with pg.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, kind, currency, archived, created_at "
            "FROM ledger_accounts "
            "WHERE ($1::int IS NULL OR user_id = $1) "
            "ORDER BY archived, name",
            user_id,
        )
    return [dict(r) for r in rows]


# =============================================================================
# Categories
# =============================================================================

DEFAULT_CATEGORY = {"expense": "其他", "income": "其他", "transfer": "转账"}


async def get_or_create_category(
    pg: PgClient,
    *,
    user_id: int | None,
    name: str,
    kind: str = "expense",
) -> int:
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM ledger_categories "
            "WHERE user_id IS NOT DISTINCT FROM $1 AND name = $2",
            user_id, name,
        )
        if row:
            return row["id"]
        return await conn.fetchval(
            "INSERT INTO ledger_categories (user_id, name, kind) "
            "VALUES ($1, $2, $3) RETURNING id",
            user_id, name, kind,
        )


async def list_categories(
    pg: PgClient, *, user_id: int | None = None, kind: str | None = None
) -> list[dict]:
    async with pg.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, kind, parent_id, created_at "
            "FROM ledger_categories "
            "WHERE ($1::int IS NULL OR user_id = $1) "
            "  AND ($2::text IS NULL OR kind = $2) "
            "ORDER BY kind, name",
            user_id, kind,
        )
    return [dict(r) for r in rows]


# =============================================================================
# Transactions
# =============================================================================

@dataclass
class TxInput:
    amount: Decimal
    kind: str = "expense"
    account: str = DEFAULT_ACCOUNT_NAME
    category: str | None = None
    happened_at: date | None = None
    note: str | None = None
    raw_text: str | None = None
    metadata: dict | None = None


async def add_transaction(
    pg: PgClient, *, user_id: int | None, tx: TxInput
) -> int:
    if tx.amount < 0:
        raise ValueError("amount must be non-negative; sign is encoded by kind")
    if tx.kind not in ("expense", "income", "transfer"):
        raise ValueError(f"kind must be expense/income/transfer, got {tx.kind!r}")

    cat_name = tx.category or DEFAULT_CATEGORY.get(tx.kind, "其他")

    account_id = await get_or_create_account(
        pg, user_id=user_id, name=tx.account
    )
    category_id = await get_or_create_category(
        pg, user_id=user_id, name=cat_name, kind=tx.kind
    )

    import json as _json
    metadata_arg = _json.dumps(tx.metadata) if tx.metadata is not None else None

    async with pg.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO ledger_transactions "
            "(user_id, account_id, category_id, amount, kind, happened_at, note, raw_text, metadata) "
            "VALUES ($1, $2, $3, $4, $5, COALESCE($6, CURRENT_DATE), $7, $8, $9::jsonb) "
            "RETURNING id",
            user_id, account_id, category_id, tx.amount, tx.kind,
            tx.happened_at, tx.note, tx.raw_text, metadata_arg,
        )


async def list_transactions(
    pg: PgClient,
    *,
    user_id: int | None = None,
    kind: str | None = None,
    since: date | None = None,
    until: date | None = None,
    category: str | None = None,
    limit: int = 50,
) -> list[dict]:
    async with pg.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id, t.amount, t.kind, t.happened_at, t.note, t.raw_text,
                   a.name AS account, c.name AS category, t.created_at
            FROM ledger_transactions t
            LEFT JOIN ledger_accounts   a ON a.id = t.account_id
            LEFT JOIN ledger_categories c ON c.id = t.category_id
            WHERE ($1::int  IS NULL OR t.user_id = $1)
              AND ($2::text IS NULL OR t.kind = $2)
              AND ($3::date IS NULL OR t.happened_at >= $3)
              AND ($4::date IS NULL OR t.happened_at <= $4)
              AND ($5::text IS NULL OR c.name = $5)
            ORDER BY t.happened_at DESC, t.id DESC
            LIMIT $6
            """,
            user_id, kind, since, until, category, limit,
        )
    return [dict(r) for r in rows]


async def sum_transactions(
    pg: PgClient,
    *,
    user_id: int | None = None,
    kind: str = "expense",
    since: date | None = None,
    until: date | None = None,
    by: str = "category",
) -> list[dict]:
    """Group totals. by ∈ {category, account, day, kind}."""
    group_clauses = {
        "category": "c.name",
        "account": "a.name",
        "day": "t.happened_at",
        "kind": "t.kind",
    }
    if by not in group_clauses:
        raise ValueError(f"by must be one of {sorted(group_clauses)}")
    grp = group_clauses[by]

    sql = f"""
        SELECT {grp} AS bucket,
               SUM(t.amount) AS total,
               COUNT(*) AS n
        FROM ledger_transactions t
        LEFT JOIN ledger_accounts   a ON a.id = t.account_id
        LEFT JOIN ledger_categories c ON c.id = t.category_id
        WHERE ($1::int  IS NULL OR t.user_id = $1)
          AND ($2::text IS NULL OR t.kind = $2)
          AND ($3::date IS NULL OR t.happened_at >= $3)
          AND ($4::date IS NULL OR t.happened_at <= $4)
        GROUP BY {grp}
        ORDER BY total DESC NULLS LAST
    """
    async with pg.acquire() as conn:
        rows = await conn.fetch(sql, user_id, kind, since, until)
    return [{"bucket": r["bucket"], "total": r["total"], "count": r["n"]} for r in rows]


# =============================================================================
# Inventory locations
# =============================================================================


def _normalize_path(path: str) -> str:
    return "/".join(seg.strip() for seg in path.strip("/").split("/") if seg.strip())


async def get_or_create_location(
    pg: PgClient, *, user_id: int | None, path: str
) -> int:
    """Create the full chain for a path like '卧室/床头柜/抽屉2', return the leaf id."""
    norm = _normalize_path(path)
    if not norm:
        raise ValueError("location path cannot be empty")

    parts = norm.split("/")
    parent_id: int | None = None
    accum = ""
    leaf_id: int | None = None
    async with pg.acquire() as conn:
        for seg in parts:
            accum = f"{accum}/{seg}" if accum else seg
            row = await conn.fetchrow(
                "SELECT id FROM inventory_locations "
                "WHERE user_id IS NOT DISTINCT FROM $1 AND path = $2",
                user_id, accum,
            )
            if row:
                leaf_id = row["id"]
            else:
                leaf_id = await conn.fetchval(
                    "INSERT INTO inventory_locations (user_id, parent_id, name, path) "
                    "VALUES ($1, $2, $3, $4) RETURNING id",
                    user_id, parent_id, seg, accum,
                )
            parent_id = leaf_id
    assert leaf_id is not None
    return leaf_id


async def list_locations(pg: PgClient, *, user_id: int | None = None) -> list[dict]:
    async with pg.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, parent_id, name, path, description "
            "FROM inventory_locations "
            "WHERE ($1::int IS NULL OR user_id = $1) "
            "ORDER BY path",
            user_id,
        )
    return [dict(r) for r in rows]


# =============================================================================
# Inventory items
# =============================================================================

@dataclass
class ItemInput:
    name: str
    location_path: str | None = None
    description: str | None = None
    quantity: int = 1
    purchased_at: date | None = None
    warranty_until: date | None = None
    transaction_id: int | None = None
    status: str = "have"
    metadata: dict | None = None
    embedding: list[float] | None = None


async def add_item(
    pg: PgClient, *, user_id: int | None, item: ItemInput
) -> int:
    location_id: int | None = None
    if item.location_path:
        location_id = await get_or_create_location(
            pg, user_id=user_id, path=item.location_path
        )

    args: list[Any] = [
        user_id, item.name, location_id, item.description, item.quantity,
        item.purchased_at, item.warranty_until, item.transaction_id, item.status,
    ]
    embed_clause = "NULL"
    if item.embedding is not None:
        if len(item.embedding) != 1024:
            raise ValueError(f"embedding must be 1024-d, got {len(item.embedding)}")
        args.append(_vector_literal(item.embedding))
        embed_clause = f"${len(args)}::vector"

    import json as _json
    args.append(_json.dumps(item.metadata) if item.metadata is not None else None)
    metadata_idx = len(args)

    sql = f"""
        INSERT INTO inventory_items
            (user_id, name, location_id, description, quantity,
             purchased_at, warranty_until, transaction_id, status,
             embedding, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, {embed_clause}, ${metadata_idx}::jsonb)
        RETURNING id
    """
    async with pg.acquire() as conn:
        return await conn.fetchval(sql, *args)


async def find_items_semantic(
    pg: PgClient,
    *,
    user_id: int | None,
    query_embedding: list[float],
    top: int = 5,
    status_filter: str | None = "have",
) -> list[dict]:
    """Vector recall over inventory_items.embedding (cosine distance)."""
    if len(query_embedding) != 1024:
        raise ValueError(f"query_embedding must be 1024-d, got {len(query_embedding)}")
    vec = _vector_literal(query_embedding)

    args: list[Any] = [vec]
    where = ["embedding IS NOT NULL"]
    if user_id is not None:
        args.append(user_id)
        where.append(f"i.user_id = ${len(args)}")
    if status_filter is not None:
        args.append(status_filter)
        where.append(f"i.status = ${len(args)}")

    sql = f"""
        SELECT i.id, i.name, i.description, i.quantity, i.status,
               i.purchased_at, i.warranty_until,
               l.path AS location,
               i.embedding <=> $1::vector AS distance
        FROM inventory_items i
        LEFT JOIN inventory_locations l ON l.id = i.location_id
        WHERE {' AND '.join(where)}
        ORDER BY i.embedding <=> $1::vector
        LIMIT {int(top)}
    """
    async with pg.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def list_items(
    pg: PgClient,
    *,
    user_id: int | None = None,
    status: str | None = "have",
    location_path: str | None = None,
    limit: int = 50,
) -> list[dict]:
    args: list[Any] = []
    where: list[str] = []
    if user_id is not None:
        args.append(user_id)
        where.append(f"i.user_id = ${len(args)}")
    if status is not None:
        args.append(status)
        where.append(f"i.status = ${len(args)}")
    if location_path:
        args.append(_normalize_path(location_path))
        # match this location and any descendant location
        where.append(f"(l.path = ${len(args)} OR l.path LIKE ${len(args)} || '/%')")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    args.append(limit)
    sql = f"""
        SELECT i.id, i.name, i.description, i.quantity, i.status,
               i.purchased_at, i.warranty_until, l.path AS location, i.created_at
        FROM inventory_items i
        LEFT JOIN inventory_locations l ON l.id = i.location_id
        {where_sql}
        ORDER BY i.created_at DESC
        LIMIT ${len(args)}
    """
    async with pg.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def move_item(
    pg: PgClient, *, user_id: int | None, item_id: int, new_location_path: str
) -> bool:
    location_id = await get_or_create_location(
        pg, user_id=user_id, path=new_location_path
    )
    async with pg.acquire() as conn:
        result = await conn.execute(
            "UPDATE inventory_items SET location_id = $2, updated_at = NOW() "
            "WHERE id = $1",
            item_id, location_id,
        )
    return result.endswith(" 1")


async def update_item(
    pg: PgClient,
    *,
    item_id: int,
    status: str | None = None,
    quantity: int | None = None,
    warranty_until: date | None = None,
    description: str | None = None,
) -> bool:
    sets: list[str] = ["updated_at = NOW()"]
    args: list[Any] = []
    if status is not None:
        args.append(status)
        sets.append(f"status = ${len(args) + 1}")
    if quantity is not None:
        args.append(quantity)
        sets.append(f"quantity = ${len(args) + 1}")
    if warranty_until is not None:
        args.append(warranty_until)
        sets.append(f"warranty_until = ${len(args) + 1}")
    if description is not None:
        args.append(description)
        sets.append(f"description = ${len(args) + 1}")

    sql = f"UPDATE inventory_items SET {', '.join(sets)} WHERE id = $1"
    async with pg.acquire() as conn:
        result = await conn.execute(sql, item_id, *args)
    return result.endswith(" 1")
