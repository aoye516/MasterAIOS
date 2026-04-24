"""CLI sub-commands for the mindscape agent (notes + watch_list + learning_plans)."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from aios.embed import embed_query
from aios.pg import PgClient
from aios.mindscape.db import (
    PLAN_STATUSES,
    WATCH_KINDS,
    WATCH_STATUSES,
    PlanInput,
    WatchInput,
    add_note,
    add_plan,
    add_watch_item,
    drop_watch,
    find_watch_semantic,
    finish_watch,
    list_plans,
    list_watch,
    update_plan,
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


def _parse_milestones(s: str | None) -> list[dict] | None:
    if not s:
        return None
    val = json.loads(s)
    if not isinstance(val, list):
        raise ValueError("milestones must be a JSON array of objects")
    return val


# =============================================================================
# Notes
# =============================================================================

async def cmd_note(args: argparse.Namespace) -> int:
    embedding = None
    if not args.no_embed:
        try:
            embedding = await embed_query(args.content)
        except Exception as e:
            if not args.json:
                print(f"WARN: embedding failed ({e}); inserting without vector",
                      flush=True)

    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()] or None
    async with PgClient() as pg:
        nid = await add_note(
            pg, user_id=args.user_id, content=args.content, tags=tags,
            embedding=embedding,
        )
    payload = {"note_id": nid, "tags": tags, "embedded": embedding is not None}
    _emit(args, payload, [
        f"note #{nid} 写入 archival_memory" + (f" [tags: {','.join(tags)}]" if tags else ""),
    ])
    return 0


async def cmd_notes(args: argparse.Namespace) -> int:
    """List/search notes via aios.pg.search_archival, restricted to content_type='note'."""
    from aios.pg import search_archival

    embedding = None
    if args.query:
        try:
            embedding = await embed_query(args.query)
        except Exception as e:
            if not args.json:
                print(f"WARN: embed failed ({e}); falling back to fulltext", flush=True)

    async with PgClient() as pg:
        # search_archival empty-query path raises; fall back to a generic listing
        if not args.query:
            async with pg.acquire() as conn:
                raw = await conn.fetch(
                    "SELECT id, user_id, content, content_type, metadata, created_at, "
                    "       NULL::float AS score "
                    "FROM archival_memory "
                    "WHERE content_type = 'note' "
                    "  AND ($1::int IS NULL OR user_id = $1) "
                    "ORDER BY created_at DESC LIMIT $2",
                    args.user_id, args.limit,
                )
            rows = [dict(r) for r in raw]
        else:
            rows_obj = await search_archival(
                pg, args.query, user_id=args.user_id,
                embedding=embedding, limit=args.limit * 3,
            )
            rows = [r.to_dict() for r in rows_obj]
            rows = [r for r in rows if r.get("content_type") == "note"][:args.limit]

    pretty = ["(no notes matched)"] if not rows else []
    for r in rows:
        snippet = (r.get("content") or "").strip().replace("\n", " ")
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        score_val = r.get("score")
        score = float(score_val) if score_val is not None else 0.0
        pretty.append(f"  #{r['id']} [{score:.3f}] {snippet}")
    _emit(args, rows, pretty)
    return 0


# =============================================================================
# watch_list
# =============================================================================

async def cmd_want(args: argparse.Namespace) -> int:
    """`aios mind want <kind> '<title>' [--author X] [--score 8.7] ...`

    `--score` 是用户/sub-agent 在调用前用 web_search 抓到的外部评分，CLI 本身不联网。
    """
    embedding = None
    if not args.no_embed:
        text = f"{args.title}" + (f" — {args.summary}" if args.summary else "")
        try:
            embedding = await embed_query(text)
        except Exception as e:
            if not args.json:
                print(f"WARN: embedding failed ({e}); inserting without vector",
                      flush=True)

    item = WatchInput(
        kind=args.kind,
        title=args.title,
        author=args.author,
        status=args.status,
        external_score=args.score,
        external_source=args.score_source,
        source_url=args.url,
        summary=args.summary,
        embedding=embedding,
    )
    async with PgClient() as pg:
        wid = await add_watch_item(pg, user_id=args.user_id, item=item)
    payload = {
        "watch_id": wid, "kind": args.kind, "title": args.title,
        "external_score": args.score, "embedded": embedding is not None,
    }
    score_str = f"  [⭐{args.score}]" if args.score else ""
    _emit(args, payload, [f"want #{wid} [{args.kind}] 「{args.title}」{score_str}"])
    return 0


async def cmd_watchlist(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        rows = await list_watch(
            pg, user_id=args.user_id, kind=args.kind,
            status=args.status, sort=args.sort, top=args.top,
        )
    pretty = ["(empty)"] if not rows else []
    for r in rows:
        score = f"⭐{r['external_score']}" if r["external_score"] else "—"
        rate = f"   我打:{r['rating']}" if r.get("rating") else ""
        pretty.append(
            f"  #{r['id']} [{r['kind']:<5} · {r['status']:<6}] 「{r['title']}」"
            f"  {score}{rate}"
        )
    _emit(args, rows, pretty)
    return 0


async def cmd_finish(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        ok = await finish_watch(
            pg, item_id=args.item_id, rating=args.rating, notes=args.notes,
        )
    if not ok:
        print(f"WARN: watch item #{args.item_id} not found", flush=True)
        return 1
    _emit(args, {"item_id": args.item_id, "ok": True, "rating": args.rating},
          [f"finished #{args.item_id}" + (f" 我打 {args.rating}" if args.rating else "")])
    return 0


async def cmd_drop(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        ok = await drop_watch(pg, item_id=args.item_id)
    if not ok:
        print(f"WARN: watch item #{args.item_id} not found", flush=True)
        return 1
    _emit(args, {"item_id": args.item_id, "ok": True}, [f"dropped #{args.item_id}"])
    return 0


async def cmd_recall(args: argparse.Namespace) -> int:
    """`aios mind recall '<query>'` — 语义召回 watch_list（"我之前想看的那本讲...的书"）"""
    embedding = await embed_query(args.query)
    async with PgClient() as pg:
        rows = await find_watch_semantic(
            pg, user_id=args.user_id, query_embedding=embedding, top=args.top,
        )
    pretty = [f"(nothing matched '{args.query}')"] if not rows else []
    for r in rows:
        d = float(r["distance"]) if r.get("distance") is not None else 0.0
        pretty.append(
            f"  #{r['id']} [{r['kind']:<5}] 「{r['title']}」  d={d:.3f}"
        )
    _emit(args, rows, pretty)
    return 0


# =============================================================================
# learning_plans
# =============================================================================

async def cmd_plan_add(args: argparse.Namespace) -> int:
    plan = PlanInput(
        name=args.name,
        goal=args.goal,
        milestones=_parse_milestones(args.milestones),
        review_cron=args.review_cron,
        status=args.status,
        notes=args.notes,
    )
    async with PgClient() as pg:
        pid = await add_plan(pg, user_id=args.user_id, plan=plan)
    _emit(args, {"plan_id": pid, "name": args.name, "status": args.status},
          [f"plan #{pid} 「{args.name}」 status={args.status}"])
    return 0


async def cmd_plan_list(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        rows = await list_plans(
            pg, user_id=args.user_id, status=args.status, limit=args.limit,
        )
    pretty = ["(no plans)"] if not rows else []
    for r in rows:
        miles = r.get("milestones") or []
        if isinstance(miles, str):
            try:
                miles = json.loads(miles)
            except Exception:
                miles = []
        done = sum(1 for m in miles if m.get("done"))
        total = len(miles)
        progress = f"  [{done}/{total}]" if total else ""
        pretty.append(f"  #{r['id']} [{r['status']:<6}] 「{r['name']}」{progress}")
    _emit(args, rows, pretty)
    return 0


async def cmd_plan_update(args: argparse.Namespace) -> int:
    async with PgClient() as pg:
        ok = await update_plan(
            pg, plan_id=args.plan_id,
            status=args.status,
            milestones=_parse_milestones(args.milestones),
            notes=args.notes,
            review_cron=args.review_cron,
        )
    if not ok:
        print(f"WARN: plan #{args.plan_id} not found", flush=True)
        return 1
    _emit(args, {"plan_id": args.plan_id, "ok": True}, [f"updated plan #{args.plan_id}"])
    return 0


# =============================================================================
# Misc
# =============================================================================

async def cmd_ping(args: argparse.Namespace) -> int:  # noqa: ARG001
    _emit(args, {"agent": "mindscape", "status": "ok"}, ["mindscape: ok"])
    return 0


# =============================================================================
# Argparse wiring
# =============================================================================

def add_subparsers(parent_sub: argparse._SubParsersAction) -> None:
    p_root = parent_sub.add_parser(
        "mind", help="Mindscape agent (notes + watch_list + learning plans)"
    )
    sub = p_root.add_subparsers(dest="mind_cmd", required=True)

    # ping
    p_ping = sub.add_parser("ping", help="connectivity self-check")
    p_ping.add_argument("--json", action="store_true")

    # ---- notes ----
    p_n = sub.add_parser("note", help="record a quick memo into archival_memory")
    p_n.add_argument("content")
    p_n.add_argument("--tags", default=None, help="comma-separated tags")
    p_n.add_argument("--no-embed", action="store_true")
    p_n.add_argument("--user-id", type=int, default=None)
    p_n.add_argument("--json", action="store_true")

    p_ns = sub.add_parser("notes", help="search/list notes (semantic + fulltext fallback)")
    p_ns.add_argument("--query", default="")
    p_ns.add_argument("--limit", type=int, default=10)
    p_ns.add_argument("--user-id", type=int, default=None)
    p_ns.add_argument("--json", action="store_true")

    # ---- watch_list ----
    p_w = sub.add_parser("want", help="add to want-to-read/-watch/-listen list")
    p_w.add_argument("kind", choices=list(WATCH_KINDS))
    p_w.add_argument("title")
    p_w.add_argument("--author", default=None)
    p_w.add_argument("--status", default="todo", choices=list(WATCH_STATUSES))
    p_w.add_argument("--score", type=float, default=None,
                     help="external public score (douban/imdb), fetched by sub-agent via web_search")
    p_w.add_argument("--score-source", default=None, help="'douban' | 'imdb' | 'goodreads' | ...")
    p_w.add_argument("--url", default=None, help="source URL")
    p_w.add_argument("--summary", default=None, help="short blurb")
    p_w.add_argument("--no-embed", action="store_true")
    p_w.add_argument("--user-id", type=int, default=None)
    p_w.add_argument("--json", action="store_true")

    p_wl = sub.add_parser("watchlist", help="list watch_list entries")
    p_wl.add_argument("--kind", default=None, choices=list(WATCH_KINDS))
    p_wl.add_argument("--status", default=None, choices=list(WATCH_STATUSES))
    p_wl.add_argument("--sort", default="added", choices=["added", "score", "rating"])
    p_wl.add_argument("--top", type=int, default=20)
    p_wl.add_argument("--user-id", type=int, default=None)
    p_wl.add_argument("--json", action="store_true")

    p_f = sub.add_parser("finish", help="mark a watch_list item as finished + my rating")
    p_f.add_argument("item_id", type=int)
    p_f.add_argument("--rating", type=float, default=None, help="my 0-10 score")
    p_f.add_argument("--notes", default=None)
    p_f.add_argument("--json", action="store_true")

    p_d = sub.add_parser("drop", help="mark a watch_list item as dropped")
    p_d.add_argument("item_id", type=int)
    p_d.add_argument("--json", action="store_true")

    p_r = sub.add_parser("recall", help="semantic search over watch_list")
    p_r.add_argument("query")
    p_r.add_argument("--top", type=int, default=5)
    p_r.add_argument("--user-id", type=int, default=None)
    p_r.add_argument("--json", action="store_true")

    # ---- plans ----
    p_pa = sub.add_parser("plan-add", help="add a learning plan")
    p_pa.add_argument("name")
    p_pa.add_argument("--goal", default=None)
    p_pa.add_argument("--milestones", default=None,
                      help="JSON array, e.g. '[{\"title\":\"读完\",\"done\":false,\"due\":\"2026-05-30\"}]'")
    p_pa.add_argument("--review-cron", default=None,
                      help="weekly|biweekly|monthly|cron expr")
    p_pa.add_argument("--status", default="doing", choices=list(PLAN_STATUSES))
    p_pa.add_argument("--notes", default=None)
    p_pa.add_argument("--user-id", type=int, default=None)
    p_pa.add_argument("--json", action="store_true")

    p_pl = sub.add_parser("plan-list", help="list learning plans")
    p_pl.add_argument("--status", default=None, choices=list(PLAN_STATUSES))
    p_pl.add_argument("--limit", type=int, default=20)
    p_pl.add_argument("--user-id", type=int, default=None)
    p_pl.add_argument("--json", action="store_true")

    p_pu = sub.add_parser("plan-update", help="update a learning plan's status / milestones / notes")
    p_pu.add_argument("plan_id", type=int)
    p_pu.add_argument("--status", default=None, choices=list(PLAN_STATUSES))
    p_pu.add_argument("--milestones", default=None, help="replace milestones JSON")
    p_pu.add_argument("--review-cron", default=None)
    p_pu.add_argument("--notes", default=None)
    p_pu.add_argument("--json", action="store_true")


HANDLERS = {
    "ping": cmd_ping,
    "note": cmd_note,
    "notes": cmd_notes,
    "want": cmd_want,
    "watchlist": cmd_watchlist,
    "finish": cmd_finish,
    "drop": cmd_drop,
    "recall": cmd_recall,
    "plan-add": cmd_plan_add,
    "plan-list": cmd_plan_list,
    "plan-update": cmd_plan_update,
}


async def dispatch(args: argparse.Namespace) -> int:
    cmd = getattr(args, "mind_cmd")
    return await HANDLERS[cmd](args)
