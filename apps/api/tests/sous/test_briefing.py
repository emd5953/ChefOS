"""Daily briefing contract (docs/specs/breadth.md, SOUS-2): every number matches its underlying
service, business dates follow the location's day start, missing or failing streams are named,
stale data is disclosed, only the published schedule counts, and the model is never needed."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.commerce import service as commerce
from app.db import scoped_session
from app.identity.models import Location
from app.integrations.models import IntegrationConnection, IntegrationStream
from app.sous import service
from app.sous.llm import LLMProvider, OllamaProvider
from tests.integrations.conftest import Clock, Connected
from tests.team.conftest import at, employee_id, import_team

UseProvider = Callable[[LLMProvider], None]
TODAY = date(2026, 3, 7)  # the fake clock's business date (Saturday)
WEEK = date(2026, 3, 2)


@pytest.fixture(autouse=True)
def api_clock(clock: Clock, monkeypatch: pytest.MonkeyPatch) -> Clock:
    """The briefing route reads "now" from the test clock."""
    monkeypatch.setattr(service, "_now", clock)
    return clock


async def get(conn: Connected, **params: Any) -> dict[str, Any]:
    r = await conn.client.get(
        "/v1/sous/briefing", params={"location_id": str(conn.location_id), **params}
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


def evidence(out: dict[str, Any]) -> dict[str, Any]:
    return {e["label"]: e["value"] for e in out["evidence"]}


async def publish_day(conn: Connected, *, publish: bool = True) -> list[dict[str, Any]]:
    """Two shifts on TODAY (one overnight, one open) and one on another day of the week."""
    r = await conn.client.post("/v1/team/positions", json={"name": "Expo"}, headers=conn.headers)
    assert r.status_code == 201, r.text
    pos = r.json()["id"]
    maya = str(await employee_id(conn, "Maya Okafor"))
    params = {"location_id": str(conn.location_id), "week_start": WEEK.isoformat()}
    bodies = [
        {"employee_id": maya, "position_id": pos, "starts_at": at(TODAY, 18),
         "ends_at": at(TODAY + timedelta(days=1), 1), "break_minutes": 30},
        {"position_id": pos, "starts_at": at(TODAY, 10), "ends_at": at(TODAY, 16)},
        {"employee_id": maya, "position_id": pos, "starts_at": at(TODAY - timedelta(days=1), 10),
         "ends_at": at(TODAY - timedelta(days=1), 15)},
    ]  # fmt: skip
    shifts = []
    for b in bodies:
        r = await conn.client.post(
            "/v1/team/schedule/shifts", params=params, json=b, headers=conn.headers
        )
        assert r.status_code == 201, r.text
        shifts.append(r.json())
    if publish:
        version = (await conn.client.get("/v1/team/schedule", params=params)).json()["schedule"][
            "version"
        ]
        r = await conn.client.post(
            "/v1/team/schedule/publish",
            params=params,
            json={"expected_version": version, "acknowledge_conflicts": True},
            headers=conn.headers,
        )
        assert r.status_code == 200, r.text
    return shifts


async def add_stock_items(conn: Connected) -> None:
    """Tomatoes below par (never stocked), Salt with a zero par, Basil without a par."""
    loc = str(conn.location_id)
    for name, par in (("Tomatoes", 5000), ("Salt", 0), ("Basil", None)):
        r = await conn.client.post(
            "/v1/inventory/items",
            json={"name": name, "category": "Produce", "base_unit": "g", "dimension": "mass"},
            headers=conn.headers,
        )
        assert r.status_code == 201, r.text
        if par is not None:
            r = await conn.client.put(
                f"/v1/inventory/items/{r.json()['id']}/par",
                json={"location_id": loc, "par_base_quantity": par},
                headers=conn.headers,
            )
            assert r.status_code == 200, r.text


async def test_every_number_matches_its_underlying_service(loaded: Connected, clock: Clock) -> None:
    await import_team(loaded, clock)
    shifts = await publish_day(loaded)
    await add_stock_items(loaded)
    out = await get(loaded)

    # No date: today at the location; results are the last completed business day.
    assert (out["business_date"], out["results_date"]) == ("2026-03-07", "2026-03-06")
    assert out["timezone"] == "America/New_York" and out["location_name"] == "Juniper House"
    assert out["unavailable"] == [] and out["highlights"]

    async with scoped_session(loaded.org_id) as db:
        loc = await db.get_one(Location, loaded.location_id)
        daily = await commerce.daily(db, loaded.org_id, loc, date(2026, 2, 27), date(2026, 3, 6))
    day, prev = daily.days[-1], daily.days[0]
    sales, labor = out["sales"], out["labor"]
    assert sales["status"] == "ok" and sales["source"] == "commerce.daily"
    assert (sales["net_sales"], sales["orders"], sales["avg_check"]) == (
        day.net_sales, day.orders, day.avg_check,
    )  # fmt: skip
    assert (sales["compare_date"], sales["compare_net_sales"]) == ("2026-02-27", prev.net_sales)
    assert sales["net_sales_change_pct"] == round(
        (day.net_sales - prev.net_sales) / prev.net_sales * 100, 1
    )
    assert labor["status"] == "ok"
    assert (labor["actual_hours"], labor["actual_cost"], labor["labor_pct"]) == (
        day.labor_hours, day.labor_cost, day.labor_pct,
    )  # fmt: skip
    ev = evidence(out)
    assert ev["Net sales (2026-03-06)"] == day.net_sales
    assert ev["Labor % (2026-03-06)"] == day.labor_pct

    # Staffing: TODAY's published shifts only, hours from the schedule service's own math.
    schedule = (
        await loaded.client.get(
            "/v1/team/schedule",
            params={"location_id": str(loaded.location_id), "week_start": WEEK.isoformat()},
        )
    ).json()
    todays = [s for s in schedule["shifts"] if s["business_date"] == TODAY.isoformat()]
    staffing = out["staffing"]
    assert staffing["status"] == "ok" and staffing["shifts"] == len(todays) == 2
    assert staffing["scheduled_hours"] == round(sum(s["paid_hours"] for s in todays), 2) == 12.5
    assert staffing["unassigned_shifts"] == 1 and staffing["by_position"] == {"Expo": 2}
    assert staffing["published_at"] == schedule["schedule"]["published_at"]
    assert [s["employee_name"] for s in staffing["shift_list"]] == [None, "Maya Okafor"]
    assert len(shifts) == 3
    assert ev["Scheduled hours (2026-03-07)"] == 12.5

    # Stock: the same below-par rows the inventory API reports.
    items = (
        await loaded.client.get(
            "/v1/inventory/items", params={"location_id": str(loaded.location_id)}
        )
    ).json()["data"]
    below = [i for i in items if i["below_par"]]
    stock = out["stock"]
    assert stock["status"] == "ok" and stock["below_par_count"] == len(below) == 1
    item = stock["items"][0]
    assert (item["name"], item["on_hand"], item["par"], item["shortfall"], item["base_unit"]) == (
        "Tomatoes", 0.0, 5000.0, 5000.0, "g",
    )  # fmt: skip
    assert stock["items_without_par"] == 1 and stock["business_date"] is None

    assert {f["stream"] for f in out["freshness"]} == {
        "orders", "timecards", "catalog", "team_members",
    }  # fmt: skip
    assert all(f["status"] == "fresh" for f in out["freshness"])
    assert sales["as_of"] is not None

    # A location outside the caller's scope is 404, as for Commerce.
    r = await loaded.client.get(
        "/v1/sous/briefing", params={"location_id": "00000000-0000-7000-8000-000000000000"}
    )
    assert r.status_code == 404


@pytest.mark.parametrize(
    ("local", "business", "results"),
    [
        (datetime(2026, 3, 7, 0, 0), "2026-03-06", "2026-03-05"),  # local midnight
        (datetime(2026, 3, 7, 3, 59), "2026-03-06", "2026-03-05"),  # before the 04:00 start
        (datetime(2026, 3, 7, 4, 0), "2026-03-07", "2026-03-06"),  # the day starts
    ],
)
async def test_default_date_follows_the_location_day_start(
    bare: Connected, clock: Clock, local: datetime, business: str, results: str
) -> None:
    clock.now = (local + timedelta(hours=5)).replace(tzinfo=UTC)  # EST is UTC-5
    out = await get(bare)
    assert (out["business_date"], out["results_date"]) == (business, results)
    assert out["sales"]["business_date"] == results
    assert out["staffing"]["business_date"] == business
    # An explicit date is the briefing's day; results are the day before.
    out = await get(bare, date="2026-03-01")
    assert (out["business_date"], out["results_date"]) == ("2026-03-01", "2026-02-28")


async def test_missing_streams_are_named_not_zeroed(bare: Connected) -> None:
    out = await get(bare)
    # No sales source: sales and labor are unavailable, not $0.
    assert out["sales"]["status"] == out["labor"]["status"] == "unavailable"
    assert out["sales"]["net_sales"] is None and out["labor"]["actual_hours"] is None
    assert out["unavailable"] == ["commerce.daily"]
    assert all(f["status"] == "not_connected" for f in out["freshness"])
    # Nothing scheduled or stocked: present, but empty.
    assert out["staffing"]["status"] == "no_data" and out["stock"]["status"] == "no_data"
    assert out["evidence"] == [] and out["highlights"] == []
    assert any(lim.startswith("Sales unavailable (commerce.daily)") for lim in out["limitations"])


async def test_missing_timecards_are_unknown_labor_not_zero(
    loaded: Connected, owner_engine: AsyncEngine
) -> None:
    async with owner_engine.begin() as c:
        await c.execute(
            text("DELETE FROM daily_labor WHERE location_id = :l AND business_date = :d"),
            {"l": loaded.location_id, "d": date(2026, 3, 6)},
        )
    out = await get(loaded)
    assert out["sales"]["status"] == "ok"
    labor = out["labor"]
    assert labor["status"] == "no_data" and labor["labor_pct"] is None
    assert labor["actual_hours"] is None and labor["actual_cost"] is None
    assert "not zero" in labor["note"]
    assert not any(e["label"].startswith("Labor") for e in out["evidence"])
    assert any("labor is unknown" in lim for lim in out["limitations"])


async def test_missing_orders_for_the_day(loaded: Connected) -> None:
    out = await get(loaded, date="2026-02-01")  # before the loaded history
    assert out["sales"]["status"] == "no_data" and out["sales"]["net_sales"] is None
    assert out["labor"]["status"] == "no_data"
    assert out["unavailable"] == []


async def test_a_failing_component_leaves_the_other_sections(
    loaded: Connected, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_stock_items(loaded)

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("read model offline")

    monkeypatch.setattr(commerce, "daily", broken)
    out = await get(loaded)
    assert out["sales"]["status"] == out["labor"]["status"] == "unavailable"
    assert out["unavailable"] == ["commerce.daily"]
    assert out["stock"]["status"] == "ok" and out["stock"]["below_par_count"] == 1
    assert out["staffing"]["status"] == "no_data"
    assert any("below par" in h for h in out["highlights"])
    assert len(out["freshness"]) == 4


async def test_stale_data_is_disclosed(loaded: Connected, clock: Clock) -> None:
    clock.advance(hours=6)  # no sync since: orders and timecards go stale
    out = await get(loaded)
    status = {f["stream"]: f["status"] for f in out["freshness"]}
    assert status["orders"] == status["timecards"] == "stale"
    assert any(lim.startswith("Sales data is stale") for lim in out["limitations"])
    assert any(lim.startswith("Labor data is stale") for lim in out["limitations"])
    assert out["sales"]["status"] == "ok"  # still shown, with its as-of time


async def test_push_sales_freshness_is_visible_in_briefing(loaded: Connected, clock: Clock) -> None:
    async with scoped_session(loaded.org_id) as db:
        connection = await db.get_one(IntegrationConnection, loaded.connection_id)
        connection.provider = "push"
        orders = await db.get_one(IntegrationStream, (connection.id, "orders"))
        orders.last_success_at = clock.now
        orders.data_as_of = clock.now
        for stream in list(
            await db.scalars(
                select(IntegrationStream).where(
                    IntegrationStream.connection_id == connection.id,
                    IntegrationStream.stream != "orders",
                )
            )
        ):
            await db.delete(stream)
    out = await get(loaded)
    status = {f["stream"]: f["status"] for f in out["freshness"]}
    assert out["sales"]["status"] == "ok" and status["orders"] == "fresh"
    assert status["timecards"] == status["catalog"] == status["team_members"] == "not_connected"


async def test_draft_shifts_never_count_as_staffing(loaded: Connected, clock: Clock) -> None:
    await import_team(loaded, clock)
    await publish_day(loaded, publish=False)
    staffing = (await get(loaded))["staffing"]
    assert staffing["status"] == "no_data" and staffing["shifts"] is None
    assert staffing["note"] == "No schedule is published for this week."


async def test_works_with_the_model_unavailable(
    loaded: Connected, use_provider: UseProvider
) -> None:
    use_provider(OllamaProvider("http://127.0.0.1:9", "missing-model", 0.5))
    status = (await loaded.client.get("/v1/sous/status")).json()
    assert status["available"] is False
    out = await get(loaded)
    assert out["sales"]["status"] == "ok" and out["highlights"]
