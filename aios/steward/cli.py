"""CLI sub-commands for the steward agent (ledger + inventory).

Wired into aios/cli.py via add_subparsers() / dispatch().
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from aios.embed import embed_query
from aios.pg import PgClient
from aios.steward.db import (
    DEFAULT_ACCOUNT_NAME,
    ItemInput,
    TxInput,
    add_item,
    add_transaction,
    find_items_semantic,
    get_or_create_account,
    get_or_create_category,
    get_or_create_location,
    list_accounts,
    list_categories,
    list_items,
    list_locations,
    list_transactions,
    move_item,
    sum_transactions,
    update_item,
)


# =============================================================================
# Argument helpers
# =============================================================================

def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    if s.lower() in ("today", "今天"):
        return date.today()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_decimal(s: str) -> Decimal:
    try:
        return Decimal(str(s).strip())
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"invalid amount: {s!r}") from e


def _month_range(month: str) -> tuple[date, date]:
    """'2026-04' → (2026-04-01, 2026-04-30)."""
    yr, mo = month.split("-")
    yr_i, mo_i = int(yr), int(mo)
    start = date(yr_i, mo_i, 1)
    if mo_i == 12:
        end = date(yr_i + 1, 1, 1)
    else:
        end = date(yr_i, mo_i + 1, 1)
    from datetime import timedelta
    return start, end - timedelta(days=1)


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


# =============================================================================
# Command handlers
# =============================================================================

async def cmd_ping(args: argparse.Namespace) -> int:  # noqa: ARG001
    payload = {"agent": "steward", "status": "ok"}
    _emit(args, payload, ["steward: ok"])
    return 0


# ---- Ledger: expense / income ----

async def cmd_expense(args: argparse.Namespace) -> int:
    """`aios steward expense --amount 38 [--category 餐饮] [--account 支付宝] ...`"""
    tx = TxInput(
        amount=_parse_decimal(args.amount),
        kind="expense",
        account=args.account or DEFAULT_ACCOUNT_NAME,
        category=args.category,
        happened_at=_parse_date(args.date),
        note=args.note,
        raw_text=args.raw,
    )
    async with PgClient() as pg:
        tx_id = await add_transaction(pg, user_id=args.user_id, tx=tx)
    payload = {"transaction_id": tx_id, "amount": str(tx.amount), "kind": "expense",
               "account": tx.account, "category": tx.category}
    _emit(args, payload, [f"expense #{tx_id} -¥{tx.amount} {tx.account} / {tx.category or '其他'}"])
    return 0


async def cmd_income(args: argparse.Namespace) -> int:
    tx = TxInput(
        amount=_parse_decimal(args.amount),
        kind="income",
        account=args.account or DEFAULT_ACCOUNT_NAME,
        category=args.category,
        happened_at=_parse_date(args.date),
        note=args.note,
        raw_text=args.raw,
    )
    async with PgClient() as pg:
        tx_id = await add_transaction(pg, user_id=args.user_id, tx=tx)
    payload = {"transaction_id": tx_id, "amount": str(tx.amount), "kind": "income",
               "account": tx.account, "category": tx.category}
    _emit(args, payload, [f"income #{tx_id} +¥{tx.amount} {tx.account} / {tx.category or '其他'}"])
    return 0


async def cmd_tx_list(args: argparse.Namespace) -> int:
    since, until = (None, None)
    if args.month:
        since, until = _month_range(args.month)
    async with PgClient() as pg:
        rows = await list_transactions(
            pg,
            user_id=args.user_id,
            kind=args.kind,
            since=since,
            until=until,
            category=args.category,
            limit=args.limit,
        )
    pretty = [f"(no transactions)"] if not rows else []
    for r in rows:
        sign = "-" if r["kind"] == "expense" else "+" if r["kind"] == "income" else "="
        pretty.append(
            f"#{r['id']} {r['happened_at']} {sign}¥{r['amount']} "
            f"{r['account'] or '?'} / {r['category'] or '?'}"
            + (f"  — {r['note']}" if r.get("note") else "")
        )
    _emit(args, rows, pretty)
    return 0


async def cmd_tx_sum(args: argparse.Namespace) -> int:
    since, until = (None, None)
    if args.month:
        since, until = _month_range(args.month)
    async with PgClient() as pg:
        rows = await sum_transactions(
            pg, user_id=args.user_id, kind=args.kind,
            since=since, until=until, by=args.by,
        )
    pretty = [f"(no {args.kind} in window)"] if not rows else []
    for r in rows:
        bucket = r["bucket"] if r["bucket"] is not None else "(uncategorized)"
        pretty.append(f"  {bucket:<20} ¥{r['total']}  [{r['count']}笔]")
    _emit(args, rows, pretty)
    return 0


async def cmd_report(args: argparse.Namespace) -> int:
    month = args.month or date.today().strftime("%Y-%m")
    since, until = _month_range(month)
    async with PgClient() as pg:
        expense_total = await sum_transactions(
            pg, user_id=args.user_id, kind="expense",
            since=since, until=until, by="kind",
        )
        income_total = await sum_transactions(
            pg, user_id=args.user_id, kind="income",
            since=since, until=until, by="kind",
        )
        expense_by_cat = await sum_transactions(
            pg, user_id=args.user_id, kind="expense",
            since=since, until=until, by="category",
        )
    e_total = expense_total[0]["total"] if expense_total else Decimal("0")
    i_total = income_total[0]["total"] if income_total else Decimal("0")
    net = i_total - e_total

    payload = {
        "month": month,
        "expense_total": str(e_total),
        "income_total": str(i_total),
        "net": str(net),
        "top_categories": expense_by_cat[:5],
    }
    pretty = [
        f"=== {month} 月度报表 ===",
        f"  支出: ¥{e_total}",
        f"  收入: ¥{i_total}",
        f"  净流: ¥{net}",
        "",
        "  Top 5 支出类目:",
    ]
    for r in expense_by_cat[:5]:
        bucket = r["bucket"] if r["bucket"] is not None else "(uncategorized)"
        pretty.append(f"    {bucket:<14} ¥{r['total']} [{r['count']}笔]")
    _emit(args, payload, pretty)
    return 0


# ---- Accounts / categories ----

async def cmd_account_add(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        aid = await get_or_create_account(
            pg, user_id=args.user_id, name=args.name, kind=args.kind
        )
    _emit(args, {"id": aid, "name": args.name, "kind": args.kind},
          [f"account #{aid} {args.name} ({args.kind})"])
    return 0


async def cmd_account_list(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        rows = await list_accounts(pg, user_id=args.user_id)
    pretty = ["(no accounts)"] if not rows else [
        f"  #{r['id']} {r['name']} ({r['kind']})" + ("  [archived]" if r["archived"] else "")
        for r in rows
    ]
    _emit(args, rows, pretty)
    return 0


async def cmd_category_add(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        cid = await get_or_create_category(
            pg, user_id=args.user_id, name=args.name, kind=args.kind
        )
    _emit(args, {"id": cid, "name": args.name, "kind": args.kind},
          [f"category #{cid} {args.name} ({args.kind})"])
    return 0


async def cmd_category_list(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        rows = await list_categories(pg, user_id=args.user_id, kind=args.kind)
    pretty = ["(no categories)"] if not rows else [
        f"  #{r['id']} [{r['kind']:<8}] {r['name']}" for r in rows
    ]
    _emit(args, rows, pretty)
    return 0


# ---- Inventory: locations ----

async def cmd_location_add(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        lid = await get_or_create_location(pg, user_id=args.user_id, path=args.path)
    _emit(args, {"id": lid, "path": args.path}, [f"location #{lid} {args.path}"])
    return 0


async def cmd_location_list(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        rows = await list_locations(pg, user_id=args.user_id)
    pretty = ["(no locations)"] if not rows else [f"  #{r['id']} {r['path']}" for r in rows]
    _emit(args, rows, pretty)
    return 0


# ---- Inventory: items ----

async def cmd_put(args: argparse.Namespace) -> int:
    """`aios steward put 'name' [--at LOCATION] [--description ...] ...`"""
    embedding = None
    if not args.no_embed:
        text = args.name + (f" — {args.description}" if args.description else "")
        try:
            embedding = await embed_query(text)
        except Exception as e:
            if args.json:
                pass
            else:
                print(f"WARN: embedding failed ({e}); inserting without vector",
                      flush=True)

    item = ItemInput(
        name=args.name,
        location_path=args.at,
        description=args.description,
        quantity=args.quantity,
        purchased_at=_parse_date(args.purchased_at),
        warranty_until=_parse_date(args.warranty_until),
        transaction_id=args.transaction_id,
        status=args.status,
        embedding=embedding,
    )
    async with PgClient() as pg:
        iid = await add_item(pg, user_id=args.user_id, item=item)
    payload = {
        "item_id": iid, "name": args.name, "at": args.at,
        "embedded": embedding is not None,
    }
    pretty = [f"item #{iid} 「{args.name}」 → {args.at or '(no location)'}"]
    _emit(args, payload, pretty)
    return 0


async def cmd_where(args: argparse.Namespace) -> int:
    """`aios steward where '充电宝'`  — semantic search."""
    embedding = await embed_query(args.query)
    async with PgClient() as pg:
        rows = await find_items_semantic(
            pg, user_id=args.user_id, query_embedding=embedding,
            top=args.top, status_filter=args.status,
        )
    pretty = [f"(nothing matched '{args.query}')"] if not rows else []
    for r in rows:
        d = float(r["distance"]) if r.get("distance") is not None else 0.0
        pretty.append(
            f"  #{r['id']} 「{r['name']}」 @ {r['location'] or '(no location)'}"
            f"  [d={d:.3f}, ×{r['quantity']}]"
        )
    _emit(args, rows, pretty)
    return 0


async def cmd_item_list(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        rows = await list_items(
            pg, user_id=args.user_id, status=args.status,
            location_path=args.at, limit=args.limit,
        )
    pretty = ["(no items)"] if not rows else []
    for r in rows:
        pretty.append(
            f"  #{r['id']} 「{r['name']}」 @ {r['location'] or '(no location)'}"
            f"  [{r['status']}, ×{r['quantity']}]"
        )
    _emit(args, rows, pretty)
    return 0


async def cmd_item_move(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        ok = await move_item(pg, user_id=args.user_id, item_id=args.item_id, new_location_path=args.to)
    if not ok:
        print(f"WARN: item #{args.item_id} not found", flush=True)
        return 1
    _emit(args, {"item_id": args.item_id, "to": args.to}, [f"moved item #{args.item_id} → {args.to}"])
    return 0


async def cmd_item_update(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        ok = await update_item(
            pg, item_id=args.item_id,
            status=args.status, quantity=args.quantity,
            warranty_until=_parse_date(args.warranty_until),
            description=args.description,
        )
    if not ok:
        print(f"WARN: item #{args.item_id} not found", flush=True)
        return 1
    _emit(args, {"item_id": args.item_id, "ok": True}, [f"updated item #{args.item_id}"])
    return 0


# =============================================================================
# Argparse wiring
# =============================================================================

def add_subparsers(parent_sub: argparse._SubParsersAction) -> None:
    p_root = parent_sub.add_parser("steward", help="Ledger + inventory steward agent")
    sub = p_root.add_subparsers(dest="steward_cmd", required=True)

    p_ping = sub.add_parser("ping", help="connectivity self-check")
    p_ping.add_argument("--json", action="store_true")

    # ---- expense / income ----
    for cmd, kind in (("expense", "expense"), ("income", "income")):
        p = sub.add_parser(cmd, help=f"add a single {kind} transaction")
        p.add_argument("--amount", required=True, help="non-negative decimal, e.g. 38.50")
        p.add_argument("--account", default=None, help=f"account name (default: {DEFAULT_ACCOUNT_NAME})")
        p.add_argument("--category", default=None, help="category name (auto-created if new)")
        p.add_argument("--date", default=None, help="YYYY-MM-DD or 'today' (default today)")
        p.add_argument("--note", default=None)
        p.add_argument("--raw", default=None, help="original natural-language input")
        p.add_argument("--user-id", type=int, default=None)
        p.add_argument("--json", action="store_true")

    p_txl = sub.add_parser("tx-list", help="list recent transactions")
    p_txl.add_argument("--month", default=None, help="YYYY-MM (overrides --since/--until)")
    p_txl.add_argument("--kind", default=None, choices=["expense", "income", "transfer"])
    p_txl.add_argument("--category", default=None)
    p_txl.add_argument("--limit", type=int, default=20)
    p_txl.add_argument("--user-id", type=int, default=None)
    p_txl.add_argument("--json", action="store_true")

    p_txs = sub.add_parser("tx-sum", help="aggregate transactions by category/account/day/kind")
    p_txs.add_argument("--month", default=None, help="YYYY-MM (default: all time)")
    p_txs.add_argument("--kind", default="expense", choices=["expense", "income", "transfer"])
    p_txs.add_argument("--by", default="category", choices=["category", "account", "day", "kind"])
    p_txs.add_argument("--user-id", type=int, default=None)
    p_txs.add_argument("--json", action="store_true")

    p_rep = sub.add_parser("report", help="monthly report (expense / income / net / top categories)")
    p_rep.add_argument("--month", default=None, help="YYYY-MM (default: current)")
    p_rep.add_argument("--user-id", type=int, default=None)
    p_rep.add_argument("--json", action="store_true")

    # ---- accounts ----
    p_aa = sub.add_parser("account-add", help="add a new account")
    p_aa.add_argument("--name", required=True)
    p_aa.add_argument("--kind", default="cash",
                      choices=["cash", "alipay", "wechat", "bank", "creditcard", "other"])
    p_aa.add_argument("--user-id", type=int, default=None)
    p_aa.add_argument("--json", action="store_true")

    p_al = sub.add_parser("account-list", help="list accounts")
    p_al.add_argument("--user-id", type=int, default=None)
    p_al.add_argument("--json", action="store_true")

    # ---- categories ----
    p_ca = sub.add_parser("category-add", help="add a new category")
    p_ca.add_argument("--name", required=True)
    p_ca.add_argument("--kind", default="expense", choices=["expense", "income", "transfer"])
    p_ca.add_argument("--user-id", type=int, default=None)
    p_ca.add_argument("--json", action="store_true")

    p_cl = sub.add_parser("category-list", help="list categories")
    p_cl.add_argument("--kind", default=None, choices=["expense", "income", "transfer"])
    p_cl.add_argument("--user-id", type=int, default=None)
    p_cl.add_argument("--json", action="store_true")

    # ---- locations ----
    p_la = sub.add_parser("location-add", help="add a location (creates parents as needed)")
    p_la.add_argument("path", help="materialized path like 卧室/床头柜/抽屉2")
    p_la.add_argument("--user-id", type=int, default=None)
    p_la.add_argument("--json", action="store_true")

    p_ll = sub.add_parser("location-list", help="list all locations (sorted by path)")
    p_ll.add_argument("--user-id", type=int, default=None)
    p_ll.add_argument("--json", action="store_true")

    # ---- items ----
    p_put = sub.add_parser("put", help="register a new physical item (with optional location + warranty)")
    p_put.add_argument("name", help="item name, e.g. '护照'")
    p_put.add_argument("--at", default=None, help="location path, e.g. 卧室/床头柜/抽屉2")
    p_put.add_argument("--description", default=None)
    p_put.add_argument("--quantity", type=int, default=1)
    p_put.add_argument("--purchased-at", default=None, help="YYYY-MM-DD")
    p_put.add_argument("--warranty-until", default=None, help="YYYY-MM-DD")
    p_put.add_argument("--transaction-id", type=int, default=None,
                       help="link to a ledger_transactions row")
    p_put.add_argument("--status", default="have",
                       choices=["have", "lent", "gone", "broken"])
    p_put.add_argument("--no-embed", action="store_true",
                       help="skip vector embedding (useful in tests / when SF unavailable)")
    p_put.add_argument("--user-id", type=int, default=None)
    p_put.add_argument("--json", action="store_true")

    p_w = sub.add_parser("where", help="semantic search: '那个充电的小玩意儿'")
    p_w.add_argument("query")
    p_w.add_argument("--top", type=int, default=5)
    p_w.add_argument("--status", default="have")
    p_w.add_argument("--user-id", type=int, default=None)
    p_w.add_argument("--json", action="store_true")

    p_il = sub.add_parser("item-list", help="list items (filter by status + location)")
    p_il.add_argument("--status", default="have")
    p_il.add_argument("--at", default=None, help="restrict to a location subtree")
    p_il.add_argument("--limit", type=int, default=50)
    p_il.add_argument("--user-id", type=int, default=None)
    p_il.add_argument("--json", action="store_true")

    p_im = sub.add_parser("item-move", help="move an item to a new location")
    p_im.add_argument("item_id", type=int)
    p_im.add_argument("--to", required=True, help="destination location path")
    p_im.add_argument("--user-id", type=int, default=None)
    p_im.add_argument("--json", action="store_true")

    p_iu = sub.add_parser("item-update", help="update item status/quantity/warranty/description")
    p_iu.add_argument("item_id", type=int)
    p_iu.add_argument("--status", default=None,
                      choices=["have", "lent", "gone", "broken"])
    p_iu.add_argument("--quantity", type=int, default=None)
    p_iu.add_argument("--warranty-until", default=None)
    p_iu.add_argument("--description", default=None)
    p_iu.add_argument("--json", action="store_true")


HANDLERS = {
    "ping": cmd_ping,
    "expense": cmd_expense,
    "income": cmd_income,
    "tx-list": cmd_tx_list,
    "tx-sum": cmd_tx_sum,
    "report": cmd_report,
    "account-add": cmd_account_add,
    "account-list": cmd_account_list,
    "category-add": cmd_category_add,
    "category-list": cmd_category_list,
    "location-add": cmd_location_add,
    "location-list": cmd_location_list,
    "put": cmd_put,
    "where": cmd_where,
    "item-list": cmd_item_list,
    "item-move": cmd_item_move,
    "item-update": cmd_item_update,
}


async def dispatch(args: argparse.Namespace) -> int:
    cmd = getattr(args, "steward_cmd")
    return await HANDLERS[cmd](args)
