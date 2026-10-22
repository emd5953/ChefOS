"""Pipeline correctness: idempotency, reconciliation, returns, version guards, deferral."""

import asyncio
import copy
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import scoped_session
from app.integrations import pipeline, sync
from app.integrations.links import load_links
from app.integrations.providers.square.fake_client import FakeSquareClient
from app.integrations.providers.square.types import RawItem, Window, parse_ts
from tests.integrations.conftest import Clock, Connect, Connected, load_days

NY = ZoneInfo("America/New_York")
FIRST, LAST = date(2026, 3, 3), date(2026, 3, 5)

SNAPSHOT = {
    "orders": "SELECT external_id, business_date, closed_at, channel, gross_sales, discounts, "
    "returns, net_sales, tax, tips, source_version FROM sales_orders ORDER BY external_id",
    "lines": "SELECT o.external_id, l.line_uid, l.name, l.quantity, l.gross, l.net, "
    "l.menu_item_variant_id IS NOT NULL AS mapped FROM sales_order_lines l "
    "JOIN sales_orders o ON o.id = l.order_id ORDER BY 1, 2",
    "returns": "SELECT o.external_id, r.external_id AS rid, r.business_date, r.amount "
    "FROM sales_order_returns r JOIN sales_orders o ON o.id = r.order_id ORDER BY 1, 2",
    "timecards": "SELECT external_id, business_date, clock_in, clock_out, paid_hours, cost, "
    "staff_member_id IS NOT NULL AS linked FROM timecards ORDER BY 1",
    "daily_sales": "SELECT business_date, channel, orders, net_sales, gross_sales, discounts, "
    "returns, tax, tips FROM daily_sales ORDER BY 1, 2",
    "daily_labor": "SELECT business_date, actual_hours, actual_cost FROM daily_labor ORDER BY 1",
    "raw": "SELECT stream, count(*) AS n FROM raw_records GROUP BY 1 ORDER BY 1",
    "menu": "SELECT (SELECT count(*) FROM menu_items) AS items, "
    "(SELECT count(*) FROM menu_item_variants) AS variants, "
    "(SELECT count(*) FROM staff_members) AS staff",
}


async def snapshot(conn: Connected) -> dict[str, list[tuple[Any, ...]]]:
    async with scoped_session(conn.org_id) as db:
        return {
            k: [tuple(r) for r in (await db.execute(text(q))).all()] for k, q in SNAPSHOT.items()
        }


async def test_replay_is_exactly_idempotent_for_a_closed_window(
    connect: Connect, clock: Clock
) -> None:
    conn = await connect()
    await load_days(conn, clock, FIRST, LAST)
    await sync.sync_connection(conn.org_id, conn.connection_id, force=True, now=clock.now)
    before = await snapshot(conn)
    assert len(before["orders"]) > 300 and before["timecards"] and before["daily_labor"]
    # Same backfill windows again, then two more forced incremental passes.
    await load_days(conn, clock, FIRST, LAST)
    for _ in range(2):
        await sync.sync_connection(conn.org_id, conn.connection_id, force=True, now=clock.now)
    assert await snapshot(conn) == before


async def test_webhook_replays_do_not_change_state(
    connect: Connect, clock: Clock, square: FakeSquareClient
) -> None:
    conn = await connect()
    await load_days(conn, clock, FIRST, LAST)
    before = await snapshot(conn)
    ids = [r[0] for r in before["orders"][:25]]
    for _ in range(3):
        changed = await sync.webhook_fetch(
            conn.org_id, conn.connection_id, "orders", ids, now=clock.now
        )
        assert changed == 0
    assert await snapshot(conn) == before


def _business_date(ts: datetime) -> date:
    local = ts.astimezone(NY)
    return local.date() if local.hour >= 4 else local.date() - timedelta(days=1)


async def test_daily_sales_reconciles_to_payloads_to_the_cent(
    connect: Connect, clock: Clock, square: FakeSquareClient
) -> None:
    conn = await connect()
    # An order is fetched by updated_at, so a refunded order arrives with its refund day. Load
    # through "now" so every sale and refund for the checked days (up to 3 days back) is in.
    await load_days(conn, clock, date(2026, 2, 28), date(2026, 3, 7))
    checked = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]

    # Independent computation from the raw payloads: item gross - item discounts, booked on the
    # close date; refunds (return line gross - discount) booked on the refund's date.
    creds = square.obtain_token("fake-x", "")
    start = datetime(2026, 2, 27, tzinfo=UTC)
    expected: dict[date, int] = {}
    orders: dict[date, int] = {}
    refunds_seen = 0
    cursor = None
    while True:
        page = square.search_orders(creds, ["L_JUNIPER_BK"], start, clock.now, cursor, 500)
        for item in page.items:
            p = item.payload
            gross = sum(int(li["gross_sales_money"]["amount"]) for li in p["line_items"])
            disc = sum(int(li["total_discount_money"]["amount"]) for li in p["line_items"])
            d = _business_date(parse_ts(p["closed_at"]))
            expected[d] = expected.get(d, 0) + gross - disc
            orders[d] = orders.get(d, 0) + 1
            for ret in p.get("returns", []):
                assert len(p["refunds"]) == 1  # the fake issues one refund per refunded order
                rd = _business_date(parse_ts(p["refunds"][0]["created_at"]))
                for rli in ret["return_line_items"]:
                    amt = int(rli["gross_return_money"]["amount"]) - int(
                        rli["total_discount_money"]["amount"]
                    )
                    expected[rd] = expected.get(rd, 0) - amt
                    refunds_seen += 1
        cursor = page.next_cursor
        if not cursor:
            break
    assert refunds_seen > 0, "pick days that include at least one refund"

    async with scoped_session(conn.org_id) as db:
        rows = (
            await db.execute(
                text(
                    "SELECT business_date, sum(net_sales) AS net, sum(orders) AS n FROM daily_sales "
                    "WHERE business_date BETWEEN :f AND :t GROUP BY 1"
                ),
                {"f": checked[0], "t": checked[-1]},
            )
        ).all()
    got = {r.business_date: (int(r.net), int(r.n)) for r in rows}
    for d in checked:
        assert got[d][0] == expected[d], d
        assert got[d][1] == orders.get(d, 0), d


def _order(
    order_id: str, loc: str, *, version: int, closed: str, updated: str, **extra: Any
) -> dict[str, Any]:
    """A minimal Square order: one $20.00 line, $2.00 discount, $18.00 net."""
    p: dict[str, Any] = {
        "id": order_id,
        "location_id": loc,
        "created_at": closed,
        "closed_at": closed,
        "updated_at": updated,
        "state": "COMPLETED",
        "version": version,
        "source": {"name": "Square Point of Sale"},
        "line_items": [
            {
                "uid": "l1",
                "name": "Burger",
                "quantity": "1",
                "base_price_money": {"amount": 2000, "currency": "USD"},
                "gross_sales_money": {"amount": 2000, "currency": "USD"},
                "total_discount_money": {"amount": 200, "currency": "USD"},
                "total_tax_money": {"amount": 160, "currency": "USD"},
            }
        ],
        "total_money": {"amount": 1960, "currency": "USD"},
        "total_tax_money": {"amount": 160, "currency": "USD"},
    }
    p.update(extra)
    return p


def _refund(amount: int, created: str) -> dict[str, Any]:
    return {
        "returns": [
            {
                "uid": "ret1",
                "return_line_items": [
                    {
                        "uid": "r1",
                        "quantity": "1",
                        "base_price_money": {"amount": 2000, "currency": "USD"},
                        "gross_return_money": {"amount": 2000, "currency": "USD"},
                        "total_discount_money": {"amount": 200, "currency": "USD"},
                        "total_tax_money": {"amount": 160, "currency": "USD"},
                    }
                ],
            }
        ],
        "refunds": [
            {
                "id": "rf1",
                "created_at": created,
                "status": "COMPLETED",
                "amount_money": {"amount": amount, "currency": "USD"},
            }
        ],
    }


def raw(payload: dict[str, Any]) -> RawItem:
    return RawItem(payload["id"], parse_ts(payload["updated_at"]), str(payload["version"]), payload)


async def ingest(
    conn: Connected, stream: str, items: list[RawItem], now: datetime
) -> pipeline.BatchResult:
    async with scoped_session(conn.org_id) as db:
        ctx = pipeline.ConnCtx(
            conn.org_id, conn.connection_id, await load_links(db, conn.connection_id)
        )
        return await pipeline.ingest(db, ctx, stream, items, None, now)


async def test_concurrent_first_versions_replace_lines_and_rebuild_old_date(
    connect: Connect, clock: Clock, owner_engine: AsyncEngine
) -> None:
    conn = await connect()
    first = _order(
        "racing",
        "L_JUNIPER_BK",
        version=1,
        closed="2026-03-01T23:00:00Z",
        updated="2026-03-01T23:00:00Z",
    )
    newer = copy.deepcopy(first)
    newer.update(version=2, closed_at="2026-03-02T23:00:00Z", updated_at="2026-03-02T23:00:00Z")
    first_written, release_first, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    second_pid: list[int] = []

    async def writer(payload: dict, hold: bool) -> None:
        async with scoped_session(conn.org_id) as db:
            ctx = pipeline.ConnCtx(
                conn.org_id, conn.connection_id, await load_links(db, conn.connection_id)
            )
            if not hold:
                second_pid.append(await db.scalar(text("SELECT pg_backend_pid()")))
                second_started.set()
            await pipeline.ingest(db, ctx, "orders", [raw(payload)], None, clock.now)
            if hold:
                first_written.set()
                await release_first.wait()

    async with asyncio.timeout(10), asyncio.TaskGroup() as group:
        group.create_task(writer(first, True))
        await first_written.wait()
        group.create_task(writer(newer, False))
        await second_started.wait()
        # Wait until the second transaction is blocked by the uncommitted first write. Before
        # the fix it had already read "no order" and blocked at the upsert; after it blocks
        # before that read. No timing-dependent sleeps decide when the first writer commits.
        try:
            async with owner_engine.connect() as db:
                while not await db.scalar(  # noqa: ASYNC110 -- PostgreSQL lock state has no asyncio event
                    text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": second_pid[0]}
                ):
                    await asyncio.sleep(0.01)
        finally:
            release_first.set()

    async with scoped_session(conn.org_id) as db:
        assert (
            await db.scalar(
                text("SELECT source_version FROM sales_orders WHERE external_id='racing'")
            )
            == 2
        )
        assert await db.scalar(
            text(
                "SELECT count(*) FROM sales_order_lines l JOIN sales_orders o ON l.order_id=o.id WHERE o.external_id='racing'"
            )
        ) == len(newer["line_items"])
    assert await daily(conn, date(2026, 3, 1)) == {"net": 0, "returns": 0, "orders": 0}
    assert await daily(conn, date(2026, 3, 2)) == {"net": 1800, "returns": 0, "orders": 1}


async def daily(conn: Connected, d: date) -> dict[str, int]:
    async with scoped_session(conn.org_id) as db:
        r = (
            await db.execute(
                text(
                    "SELECT COALESCE(sum(net_sales), 0) AS net, COALESCE(sum(returns), 0) AS ret, "
                    "COALESCE(sum(orders), 0) AS n FROM daily_sales WHERE business_date = :d"
                ),
                {"d": d},
            )
        ).one()
    return {"net": int(r.net), "returns": int(r.ret), "orders": int(r.n)}


async def test_refund_reduces_net_sales_on_its_processing_date(
    connect: Connect, clock: Clock
) -> None:
    conn = await connect()
    sale_day, refund_day = date(2026, 3, 1), date(2026, 3, 3)
    v1 = _order(
        "o1",
        "L_JUNIPER_BK",
        version=1,
        closed="2026-03-01T23:00:00Z",
        updated="2026-03-01T23:00:00Z",
    )
    v2 = copy.deepcopy(v1)
    v2.update(version=2, updated_at="2026-03-03T17:00:00Z", **_refund(1960, "2026-03-03T17:00:00Z"))

    await ingest(conn, "orders", [raw(v1)], clock.now)
    assert await daily(conn, sale_day) == {"net": 1800, "returns": 0, "orders": 1}

    await ingest(conn, "orders", [raw(v2)], clock.now)
    # The sale keeps its day; the return lands on the day it was processed.
    assert await daily(conn, sale_day) == {"net": 1800, "returns": 0, "orders": 1}
    assert await daily(conn, refund_day) == {"net": -1800, "returns": 1800, "orders": 0}


async def test_older_versions_are_landed_but_not_applied(connect: Connect, clock: Clock) -> None:
    conn = await connect()
    v1 = _order(
        "o2",
        "L_JUNIPER_BK",
        version=1,
        closed="2026-03-01T23:00:00Z",
        updated="2026-03-01T23:00:00Z",
    )
    v2 = copy.deepcopy(v1)
    v2.update(version=2, updated_at="2026-03-03T17:00:00Z", **_refund(1960, "2026-03-03T17:00:00Z"))
    await ingest(conn, "orders", [raw(v2)], clock.now)
    res = await ingest(conn, "orders", [raw(v1)], clock.now)  # stale arrives late
    assert res.landed == 1 and res.applied == 0
    async with scoped_session(conn.org_id) as db:
        version = await db.scalar(
            text("SELECT source_version FROM sales_orders WHERE external_id='o2'")
        )
        raws = await db.scalar(text("SELECT count(*) FROM raw_records WHERE external_id='o2'"))
        pending = await db.scalar(
            text("SELECT count(*) FROM raw_records WHERE normalize_status='pending'")
        )
    assert (version, raws, pending) == (2, 2, 0)
    assert await daily(conn, date(2026, 3, 3)) == {"net": -1800, "returns": 1800, "orders": 0}


async def test_bad_payload_is_quarantined_and_others_continue(
    connect: Connect, clock: Clock
) -> None:
    conn = await connect()
    good = _order(
        "g1",
        "L_JUNIPER_BK",
        version=1,
        closed="2026-03-01T23:00:00Z",
        updated="2026-03-01T23:00:00Z",
    )
    bad = {"id": "b1", "location_id": "L_JUNIPER_BK", "version": 1, "updated_at": "2026-03-01T23:00:00Z",
           "line_items": [{"quantity": "not-a-number"}]}  # fmt: skip
    res = await ingest(conn, "orders", [raw(bad), raw(good)], clock.now)
    assert (res.applied, res.quarantined) == (1, 1)
    async with scoped_session(conn.org_id) as db:
        row = (
            await db.execute(
                text(
                    "SELECT normalize_status, normalize_error FROM raw_records WHERE external_id='b1'"
                )
            )
        ).one()
    assert row.normalize_status == "quarantined" and row.normalize_error


async def test_orders_for_unlinked_locations_wait_until_linked(
    connect: Connect, clock: Clock
) -> None:
    from app.integrations import service

    # Two platform locations: no auto-link, so the Square location stays unlinked.
    conn = await connect(extra_locations=1, location_name="Main")
    async with scoped_session(conn.org_id) as db:
        assert (await load_links(db, conn.connection_id))["L_JUNIPER_BK"].location_id is None
    o = _order(
        "u1",
        "L_JUNIPER_BK",
        version=1,
        closed="2026-03-01T23:00:00Z",
        updated="2026-03-01T23:00:00Z",
    )
    res = await ingest(conn, "orders", [raw(o)], clock.now)
    assert (res.deferred, res.applied) == (1, 0)
    async with scoped_session(conn.org_id) as db:
        issue = (await db.execute(text("SELECT kind, occurrences FROM mapping_issues"))).one()
        orders = await db.scalar(text("SELECT count(*) FROM sales_orders"))
    assert tuple(issue) == ("unmapped_location", 1) and orders == 0

    newly = await service.patch_links(
        conn.org_id, conn.connection_id, [("L_JUNIPER_BK", conn.location_id)]
    )
    assert newly
    assert await sync.process_deferred(conn.org_id, conn.connection_id, clock.now) == 1
    assert await daily(conn, date(2026, 3, 1)) == {"net": 1800, "returns": 0, "orders": 1}


def _timecard(
    tc_id: str, member: str, start: str, end: str | None, version: int = 1
) -> dict[str, Any]:
    p: dict[str, Any] = {
        "id": tc_id,
        "location_id": "L_JUNIPER_BK",
        "team_member_id": member,
        "start_at": start,
        "status": "CLOSED" if end else "OPEN",
        "wage": {"title": "Server", "hourly_rate": {"amount": 2000, "currency": "USD"}},
        "breaks": [{"id": "b", "start_at": "2026-03-02T22:00:00Z", "end_at": "2026-03-02T22:30:00Z", "is_paid": False}],
        "version": version,
        "updated_at": end or start,
    }  # fmt: skip
    if end:
        p["end_at"] = end
    return p


async def test_labor_hand_calculation_and_unmapped_team_member(
    connect: Connect, clock: Clock
) -> None:
    conn = await connect()
    await sync.sync_connection(
        conn.org_id, conn.connection_id, streams=["team_members"], force=True, now=clock.now
    )
    async with scoped_session(conn.org_id) as db:
        known = await db.scalar(
            text("SELECT external_id FROM external_refs WHERE entity_type='staff_member' LIMIT 1")
        )
    # 12:00-20:00 local... 16:00Z-04:00Z: 8h shift, 30 min unpaid break => 7.5h * $20 = $150.00
    a = _timecard("tc-a", known, "2026-03-02T16:00:00Z", "2026-03-03T00:00:00Z")
    b = _timecard(
        "tc-b", "TM_STRANGER", "2026-03-02T16:00:00Z", "2026-03-02T20:00:00Z"
    )  # 4h - 0.5h
    await ingest(conn, "timecards", [raw(a), raw(b)], clock.now)
    async with scoped_session(conn.org_id) as db:
        labor = (
            await db.execute(
                text(
                    "SELECT actual_hours, actual_cost FROM daily_labor WHERE business_date='2026-03-02'"
                )
            )
        ).one()
        issue = (await db.execute(text("SELECT kind, external_id FROM mapping_issues"))).one()
        linked = {
            r.external_id: r.linked
            for r in await db.execute(
                text("SELECT external_id, staff_member_id IS NOT NULL AS linked FROM timecards")
            )
        }
    # (7.5h + 3.5h) = 11h, $150 + $70 = $220.00
    assert (float(labor.actual_hours), labor.actual_cost) == (11.0, 22000)
    assert tuple(issue) == ("unmapped_team_member", "TM_STRANGER")
    assert linked == {"tc-a": True, "tc-b": False}  # unmapped: stored, counted, not per-employee


async def test_open_timecard_gets_cost_when_it_closes(connect: Connect, clock: Clock) -> None:
    conn = await connect()
    await sync.sync_connection(
        conn.org_id, conn.connection_id, streams=["team_members"], force=True, now=clock.now
    )
    async with scoped_session(conn.org_id) as db:
        known = await db.scalar(
            text("SELECT external_id FROM external_refs WHERE entity_type='staff_member' LIMIT 1")
        )
    open_tc = _timecard("tc-o", known, "2026-03-02T16:00:00Z", None)
    open_tc["breaks"] = []
    await ingest(conn, "timecards", [raw(open_tc)], clock.now)
    async with scoped_session(conn.org_id) as db:
        assert (
            await db.scalar(
                text("SELECT actual_cost FROM daily_labor WHERE business_date='2026-03-02'")
            )
            == 0
        )
    closed = _timecard("tc-o", known, "2026-03-02T16:00:00Z", "2026-03-02T20:00:00Z", version=2)
    closed["breaks"] = []
    await ingest(conn, "timecards", [raw(closed)], clock.now)
    async with scoped_session(conn.org_id) as db:
        assert (
            await db.scalar(
                text("SELECT actual_cost FROM daily_labor WHERE business_date='2026-03-02'")
            )
            == 8000
        )


async def test_unmapped_item_counts_in_sales_and_raises_an_issue(
    connect: Connect, clock: Clock
) -> None:
    conn = await connect()
    o = _order(
        "m1",
        "L_JUNIPER_BK",
        version=1,
        closed="2026-03-01T23:00:00Z",
        updated="2026-03-01T23:00:00Z",
    )
    await ingest(conn, "orders", [raw(o)], clock.now)
    async with scoped_session(conn.org_id) as db:
        issue = (await db.execute(text("SELECT kind, external_id FROM mapping_issues"))).one()
        unmapped = await db.scalar(
            text("SELECT sum(net) FROM sales_order_lines WHERE menu_item_variant_id IS NULL")
        )
    assert tuple(issue) == ("unmapped_item", "Burger") and unmapped == 1800
    assert (await daily(conn, date(2026, 3, 1)))["net"] == 1800


async def test_window_helper_covers_the_business_day() -> None:
    w: Window = sync.day_window(
        "America/New_York", __import__("datetime").time(4, 0), date(2026, 3, 7), date(2026, 3, 8)
    )
    # Mar 7 04:00 EST = 09:00Z; Mar 9 04:00 EDT = 08:00Z (clocks changed on Mar 8)
    assert w.start == datetime(2026, 3, 7, 9, tzinfo=UTC) and w.end == datetime(
        2026, 3, 9, 8, tzinfo=UTC
    )
    assert isinstance(uuid.uuid4(), uuid.UUID)
