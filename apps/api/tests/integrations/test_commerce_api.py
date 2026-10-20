"""Commerce API contract tests: shapes match docs/api.md exactly."""

from datetime import date, timedelta
from typing import Any

import pytest

from app.commerce import service as commerce
from app.db import scoped_session
from app.identity.models import Location
from app.integrations import sync
from tests.integrations.conftest import Clock, Connect, Connected, load_days

TOTALS = {
    "net_sales", "gross_sales", "discounts", "returns", "tax", "tips", "orders", "avg_check",
    "labor_cost", "labor_hours", "labor_pct",
}  # fmt: skip
CHANNELS = {"dine_in", "pickup", "delivery", "online", "other"}
FRESH = {"stream", "location_id", "data_as_of", "status"}
FIRST, LAST = date(2026, 2, 28), date(2026, 3, 7)


@pytest.fixture
async def loaded(connect: Connect, clock: Clock) -> Connected:
    conn = await connect()
    await load_days(conn, clock, FIRST, LAST)
    await sync.sync_connection(conn.org_id, conn.connection_id, force=True, now=clock.now)
    return conn


async def get(conn: Connected, path: str, **params: Any) -> Any:
    r = await conn.client.get(
        f"/v1/commerce/{path}", params={"location_id": str(conn.location_id), **params}
    )
    assert r.status_code == 200, r.text
    return r.json()


def check_freshness(items: list[dict[str, Any]], conn: Connected) -> None:
    assert [i["stream"] for i in items] == ["orders", "timecards", "catalog", "team_members"]
    for i in items:
        assert set(i) == FRESH and i["location_id"] == str(conn.location_id)
        assert i["status"] in {"fresh", "stale", "failing", "not_connected"}


async def test_daily_shape_and_contiguous_zero_filled_series(loaded: Connected) -> None:
    body = await get(loaded, "daily", **{"from": "2026-02-19", "to": "2026-03-09"})
    assert set(body) == {"location_id", "currency", "days", "freshness"}
    assert body["currency"] == "USD" and body["location_id"] == str(loaded.location_id)
    days = body["days"]
    dates = [d["business_date"] for d in days]
    assert dates == [str(date(2026, 2, 19) + timedelta(days=i)) for i in range(19)]  # contiguous
    for d in days:
        assert set(d) == TOTALS | {"business_date", "channels"}
        assert set(d["channels"]) == CHANNELS
        assert all(isinstance(v, int) for v in d["channels"].values())
    by = {d["business_date"]: d for d in days}
    # never-loaded day and a future day are zeros, not gaps
    for empty in ("2026-02-19", "2026-03-09"):
        z = by[empty]
        assert z["net_sales"] == z["orders"] == z["labor_cost"] == 0 and z["labor_hours"] == 0
        assert z["avg_check"] is None and z["labor_pct"] is None
        assert set(z["channels"].values()) == {0}
    # a busy Friday: channels sum to net sales, avg check is net/orders, labor % in percent units
    fri = by["2026-03-06"]
    assert sum(fri["channels"].values()) == fri["net_sales"] > 0
    assert fri["avg_check"] == round(fri["net_sales"] / fri["orders"])
    assert fri["labor_pct"] == round(fri["labor_cost"] / fri["net_sales"] * 100, 1)
    assert 10 < fri["labor_pct"] < 60
    check_freshness(body["freshness"], loaded)


async def test_day_without_sales_has_null_labor_pct(loaded: Connected) -> None:
    # A date before the simulated history: no sales, no labor.
    body = await get(loaded, "daily", **{"from": "2020-01-06", "to": "2020-01-06"})
    day = body["days"][0]
    assert day["orders"] == 0 and day["net_sales"] == 0
    assert day["labor_pct"] is None and day["avg_check"] is None  # null, not 0 or infinity


async def test_summary_shape_and_comparison_periods(loaded: Connected) -> None:
    prev = await get(loaded, "summary", **{"from": "2026-03-02", "to": "2026-03-08"})
    assert set(prev) == {
        "currency", "period", "compare_period", "current", "previous", "change", "channels", "freshness",
    }  # fmt: skip
    assert prev["period"] == {"from": "2026-03-02", "to": "2026-03-08"}
    assert prev["compare_period"] == {
        "from": "2026-02-23",
        "to": "2026-03-01",
    }  # same length, before
    assert set(prev["current"]) == TOTALS == set(prev["previous"])
    assert set(prev["change"]) == {"net_sales_pct", "orders_pct", "avg_check_pct", "labor_pct_pts"}

    daily = await get(loaded, "daily", **{"from": "2026-03-02", "to": "2026-03-08"})
    assert prev["current"]["net_sales"] == sum(d["net_sales"] for d in daily["days"])
    assert prev["current"]["orders"] == sum(d["orders"] for d in daily["days"])

    yoy = await get(
        loaded, "summary", **{"from": "2026-03-02", "to": "2026-03-08", "compare": "previous_year"}
    )
    assert yoy["compare_period"] == {
        "from": "2025-03-03",
        "to": "2025-03-09",
    }  # 364 days: Mon -> Mon
    assert date.fromisoformat("2026-03-02").weekday() == date.fromisoformat("2025-03-03").weekday()
    # no data a year ago: percent changes are null, never NaN/inf
    assert yoy["previous"]["net_sales"] == 0
    assert yoy["change"] == {
        "net_sales_pct": None,
        "orders_pct": None,
        "avg_check_pct": None,
        "labor_pct_pts": None,
    }

    for c in prev["channels"]:
        assert set(c) == {"channel", "net_sales", "share_pct"} and c["channel"] in CHANNELS
    assert abs(sum(c["share_pct"] for c in prev["channels"]) - 100) < 0.5
    assert prev["channels"][0]["channel"] == "dine_in"
    check_freshness(prev["freshness"], loaded)


async def test_summary_change_percentages(loaded: Connected) -> None:
    body = await get(loaded, "summary", **{"from": "2026-03-05", "to": "2026-03-07"})  # Thu-Sat
    cur, prev = body["current"], body["previous"]
    assert prev["net_sales"] > 0  # Feb 26-28 is loaded (Feb 28 only partly)
    exp = round((cur["net_sales"] - prev["net_sales"]) / prev["net_sales"] * 100, 1)
    assert body["change"]["net_sales_pct"] == exp
    assert body["change"]["labor_pct_pts"] == round(cur["labor_pct"] - prev["labor_pct"], 1)


async def test_items_shape(loaded: Connected) -> None:
    body = await get(loaded, "items", **{"from": "2026-03-03", "to": "2026-03-07", "limit": 5})
    assert set(body) == {"currency", "items", "unmapped_net_sales"}
    assert 0 < len(body["items"]) <= 5
    for it in body["items"]:
        assert set(it) == {"name", "category", "quantity", "net_sales", "share_pct"}
    nets = [i["net_sales"] for i in body["items"]]
    assert nets == sorted(nets, reverse=True)
    assert body["unmapped_net_sales"] > 0  # the ad-hoc "Open Food" line
    assert body["items"][0]["category"] != "Uncategorized"


async def test_today_uses_the_location_clock_and_trickles(loaded: Connected, clock: Clock) -> None:
    async def today() -> Any:
        async with scoped_session(loaded.org_id) as db:
            loc = await db.get_one(Location, loaded.location_id)
            return (await commerce.today(db, loaded.org_id, loc, now=clock.now)).model_dump()

    t = await today()
    assert t["business_date"] == date(2026, 3, 7)  # Saturday 15:00 EST
    assert t["orders"] > 0 and t["net_sales"] > 0 and t["avg_check"] > 0
    assert [h["hour"] for h in t["by_hour"]] == list(range(24))
    assert all(h["orders"] == 0 for h in t["by_hour"] if h["hour"] >= 15)  # nothing after "now"
    assert any(h["orders"] > 0 for h in t["by_hour"] if 11 <= h["hour"] <= 14)
    assert t["last_order_at"] <= clock.now

    clock.advance(hours=3)  # the dinner rush trickles in
    await sync.sync_connection(loaded.org_id, loaded.connection_id, force=True, now=clock.now)
    t2 = await today()
    assert t2["orders"] > t["orders"] and t2["last_order_at"] > t["last_order_at"]
    assert any(h["orders"] > 0 for h in t2["by_hour"] if h["hour"] >= 17)

    live = await get(loaded, "today")
    assert set(live) == {
        "currency",
        "business_date",
        "net_sales",
        "orders",
        "avg_check",
        "last_order_at",
        "by_hour",
        "freshness",
    }
    assert all(set(h) == {"hour", "net_sales", "orders"} for h in live["by_hour"])


async def test_validation_and_access(loaded: Connected, connect: Connect) -> None:
    c, loc = loaded.client, str(loaded.location_id)
    r = await c.get(
        "/v1/commerce/daily", params={"location_id": loc, "from": "2026-03-05", "to": "2026-03-01"}
    )
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    r = await c.get("/v1/commerce/daily", params={"from": "2026-03-01", "to": "2026-03-05"})
    assert r.status_code == 422  # location_id is required
    r = await c.get(
        "/v1/commerce/summary",
        params={"location_id": loc, "from": "2026-03-01", "to": "2026-03-05", "compare": "nope"},
    )
    assert r.status_code == 422
    other = await connect(complete=False)
    r = await other.client.get(
        "/v1/commerce/daily", params={"location_id": loc, "from": "2026-03-01", "to": "2026-03-05"}
    )
    assert r.status_code == 404
    c.cookies.clear()
    r = await c.get("/v1/commerce/today", params={"location_id": loc})
    assert r.status_code == 401


async def test_unconnected_location_reports_not_connected(connect: Connect) -> None:
    conn = await connect(complete=False)
    body = await get(conn, "daily", **{"from": "2026-03-01", "to": "2026-03-02"})
    assert [f["status"] for f in body["freshness"]] == ["not_connected"] * 4
    assert all(f["data_as_of"] is None for f in body["freshness"])
    assert len(body["days"]) == 2


async def test_queries_are_fast_for_a_90_day_range(loaded: Connected) -> None:
    import time

    t = time.perf_counter()
    for path in ("daily", "summary", "items"):
        await get(loaded, path, **{"from": "2025-12-08", "to": "2026-03-07"})
    assert (time.perf_counter() - t) / 3 < 0.3
