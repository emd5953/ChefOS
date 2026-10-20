"""Schedule builder: shift writes, validation, overlap, concurrency, copy, conflicts, totals."""

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.db import scoped_session
from tests.team.conftest import (
    NY,
    AddMember,
    Clock,
    Connected,
    at,
    audit_actions,
    employee_id,
    positions,
)

WEEK = date(2026, 3, 9)  # Monday after the clock's Saturday (2026-03-07)
LAST_WEEK = date(2026, 3, 2)  # contains the Mar 8 DST change


async def _shift(conn: Connected, week: date = WEEK, **body: Any) -> Any:
    return await conn.client.post(
        "/v1/team/schedule/shifts",
        params={"location_id": str(conn.location_id), "week_start": week.isoformat()},
        json=body,
        headers=conn.headers,
    )


async def _read(conn: Connected, week: date = WEEK) -> dict[str, Any]:
    r = await conn.client.get(
        "/v1/team/schedule",
        params={"location_id": str(conn.location_id), "week_start": week.isoformat()},
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


async def test_create_read_and_totals(team_org: Connected, frozen: Clock) -> None:
    pos = await positions(team_org)
    maya = await employee_id(team_org, "Maya Okafor")  # $16.00/h
    fri = WEEK + timedelta(days=4)
    r = await _shift(
        team_org,
        employee_id=str(maya),
        position_id=pos["Server"],
        starts_at=at(fri, 16),
        ends_at=at(fri, 23),
        break_minutes=30,
    )
    assert r.status_code == 201, r.text
    shift = r.json()
    assert (shift["business_date"], shift["paid_hours"], shift["cost"]) == (
        fri.isoformat(),
        6.5,
        10400,
    )
    await _shift(team_org, position_id=pos["Host"], starts_at=at(fri, 17), ends_at=at(fri, 21))
    body = await _read(team_org)
    assert body["schedule"]["version"] == 2 and body["schedule"]["published_version"] is None
    assert body["schedule"]["unpublished_changes"] == 2
    day = next(d for d in body["days"] if d["business_date"] == fri.isoformat())
    assert (day["scheduled_hours"], day["projected_cost"], day["unrated_shifts"]) == (
        10.5,
        10400,
        1,
    )
    # Connected but no sales history yet.
    assert (day["forecast_sales"], day["forecast_reason"]) == (None, "not_enough_history")
    assert body["week"]["forecast_sales"] is None
    kinds = {(c["shift_id"], c["kind"]) for c in body["conflicts"]}
    assert any(k == "unassigned" for _, k in kinds) and (shift["id"], "overlap") not in kinds
    assert any(e["display_name"] == "Maya Okafor" and e["has_rate"] for e in body["employees"])
    assert body["freshness"][0]["stream"] == "orders"


async def test_forecast_and_labor_pct(team_org: Connected, frozen: Clock) -> None:
    async with scoped_session(team_org.org_id) as db:
        for weeks, net in enumerate((400_000, 500_000, 600_000, 900_000), start=1):
            d = WEEK + timedelta(days=4) - timedelta(weeks=weeks)  # Fridays Mar 6 .. Feb 13
            await db.execute(
                text(
                    "INSERT INTO daily_sales (org_id, location_id, business_date, channel, orders, "
                    "net_sales, gross_sales, discounts, returns, tax, tips, currency, computed_at, "
                    "data_as_of) VALUES (:o, :l, :d, 'dine_in', 10, :n, :n, 0, 0, 0, 0, 'USD', now(), now())"
                ),
                {"o": team_org.org_id, "l": team_org.location_id, "d": d, "n": net},
            )
    pos = await positions(team_org)
    maya = await employee_id(team_org, "Maya Okafor")
    fri = WEEK + timedelta(days=4)
    await _shift(
        team_org,
        employee_id=str(maya),
        position_id=pos["Server"],
        starts_at=at(fri, 15),
        ends_at=at(fri, 23),
    )
    day = next(d for d in (await _read(team_org))["days"] if d["business_date"] == fri.isoformat())
    assert day["forecast_sales"] == 550_000  # median of 4, 5, 6, 9 thousand
    assert day["forecast_basis"] == ["2026-03-06", "2026-02-27", "2026-02-20", "2026-02-13"]
    assert day["projected_labor_pct"] == round(12800 / 550_000 * 100, 1)
    sat = next(d for d in (await _read(team_org))["days"] if d["business_date"] == "2026-03-14")
    assert (
        sat["forecast_sales"] == 0 and sat["forecast_reason"] is None
    )  # history exists: closed = 0


async def test_not_connected_forecast(connect: Any, clock: Clock, frozen: Clock) -> None:
    conn = await connect(complete=False)
    r = await conn.client.post("/v1/team/positions", json={"name": "Cook"}, headers=conn.headers)
    await _shift(conn, position_id=r.json()["id"], starts_at=at(WEEK, 9), ends_at=at(WEEK, 15))
    day = (await _read(conn))["days"][0]
    assert (day["forecast_sales"], day["forecast_reason"]) == (None, "not_connected")
    assert (await _read(conn))["week"]["reference_labor_pct"] is None


async def test_validation(team_org: Connected) -> None:
    pos = (await positions(team_org))["Server"]
    fri = WEEK + timedelta(days=4)
    cases = [
        ({"starts_at": at(fri, 16), "ends_at": at(fri, 16)}, "ends_at"),
        ({"starts_at": at(fri, 16), "ends_at": at(fri, 16, 10)}, "ends_at"),
        ({"starts_at": at(fri, 6), "ends_at": at(fri, 23)}, "ends_at"),
        ({"starts_at": at(fri, 16), "ends_at": at(fri, 17), "break_minutes": 60}, "break_minutes"),
        (
            {
                "starts_at": at(WEEK + timedelta(days=8), 16),
                "ends_at": at(WEEK + timedelta(days=8), 20),
            },
            "starts_at",
        ),
        (
            {"starts_at": "2026-03-13T16:00:00", "ends_at": "2026-03-13T20:00:00"},
            "starts_at",
        ),  # no offset
        (
            {"starts_at": at(fri, 16), "ends_at": at(fri, 20), "station_id": str(uuid.uuid4())},
            "station_id",
        ),
        (
            {"starts_at": at(fri, 16), "ends_at": at(fri, 20), "employee_id": str(uuid.uuid4())},
            "employee_id",
        ),
    ]
    for body, field in cases:
        r = await _shift(team_org, position_id=pos, **body)
        assert r.status_code == 422, (body, r.text)
        assert field in r.json()["errors"], (body, r.json())
    r = await _shift(
        team_org,
        week=WEEK + timedelta(days=1),
        position_id=pos,
        starts_at=at(fri, 16),
        ends_at=at(fri, 20),
    )
    assert r.status_code == 422 and "week_start" in r.json()["errors"]
    # Crossing midnight is fine; a 01:00 start belongs to the previous business date.
    r = await _shift(
        team_org, position_id=pos, starts_at=at(fri, 22), ends_at=at(fri + timedelta(days=1), 2)
    )
    assert r.status_code == 201
    r = await _shift(team_org, position_id=pos, starts_at=at(fri, 1), ends_at=at(fri, 3))
    assert r.json()["business_date"] == (fri - timedelta(days=1)).isoformat()


async def test_overlap_any_location_is_409(team_org: Connected) -> None:
    c, h = team_org.client, team_org.headers
    pos = (await positions(team_org))["Server"]
    maya = str(await employee_id(team_org, "Maya Okafor"))
    annex = (
        await c.post(
            "/v1/locations", json={"name": "Annex", "timezone": "America/New_York"}, headers=h
        )
    ).json()
    tue = WEEK + timedelta(days=1)
    r = await c.post(
        "/v1/team/schedule/shifts",
        params={"location_id": annex["id"], "week_start": WEEK.isoformat()},
        json={
            "employee_id": maya,
            "position_id": pos,
            "starts_at": at(tue, 10),
            "ends_at": at(tue, 15),
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    r = await _shift(
        team_org, employee_id=maya, position_id=pos, starts_at=at(tue, 14), ends_at=at(tue, 20)
    )
    assert (r.status_code, r.json()["code"]) == (409, "shift_overlap")
    assert "Annex" in r.json()["detail"]
    r = await _shift(
        team_org, employee_id=maya, position_id=pos, starts_at=at(tue, 15), ends_at=at(tue, 20)
    )
    assert r.status_code == 201  # back-to-back is not an overlap


async def test_patch_delete_and_stale_writes(team_org: Connected) -> None:
    c, h = team_org.client, team_org.headers
    pos = await positions(team_org)
    wed = WEEK + timedelta(days=2)
    shift = (
        await _shift(team_org, position_id=pos["Host"], starts_at=at(wed, 16), ends_at=at(wed, 22))
    ).json()
    maya = str(await employee_id(team_org, "Maya Okafor"))
    r = await c.patch(
        f"/v1/team/shifts/{shift['id']}",
        json={
            "employee_id": maya,
            "ends_at": at(wed, 23),
            "expected_updated_at": shift["updated_at"],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert (updated["employee_id"], updated["paid_hours"]) == (maya, 7.0)
    r = await c.patch(
        f"/v1/team/shifts/{shift['id']}",
        json={"notes": "x", "expected_updated_at": shift["updated_at"]},
        headers=h,
    )
    assert r.status_code == 409  # stale
    r = await c.delete(f"/v1/team/shifts/{shift['id']}", headers=h)
    assert r.status_code == 204
    assert (await _read(team_org))["shifts"] == []
    assert (await _read(team_org))["schedule"]["version"] == 3
    actions = [a for _, a in await audit_actions(team_org) if a.startswith("shift.")]
    assert actions == ["shift.created", "shift.updated", "shift.deleted"]
    r = await c.delete(f"/v1/team/shifts/{shift['id']}", headers=h)
    assert r.status_code == 404


async def test_copy_week(team_org: Connected) -> None:
    c, h = team_org.client, team_org.headers
    pos = (await positions(team_org))["Server"]
    maya = str(await employee_id(team_org, "Maya Okafor"))
    diego = await employee_id(team_org, "Diego Marsh")
    await _shift(
        team_org,
        LAST_WEEK,
        employee_id=maya,
        position_id=pos,
        starts_at=at(LAST_WEEK, 16),
        ends_at=at(LAST_WEEK, 23),
    )
    await _shift(
        team_org,
        LAST_WEEK,
        employee_id=str(diego),
        position_id=pos,
        starts_at=at(LAST_WEEK, 10),
        ends_at=at(LAST_WEEK, 15),
    )
    async with scoped_session(team_org.org_id) as db:  # Diego leaves after last week
        await db.execute(
            text("UPDATE staff_members SET active = false WHERE id = :i"), {"i": diego}
        )
    params = {"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()}
    r = await c.post(
        "/v1/team/schedule/copy",
        params=params,
        json={"from_week_start": LAST_WEEK.isoformat()},
        headers=h,
    )
    assert r.json() == {"copied": 2, "unassigned": 1}
    shifts = (await _read(team_org))["shifts"]
    local = sorted(datetime.fromisoformat(s["starts_at"]).astimezone(NY).hour for s in shifts)
    assert local == [10, 16]  # wall time kept across the DST change
    assert {s["business_date"] for s in shifts} == {WEEK.isoformat()}
    r = await c.post(
        "/v1/team/schedule/copy",
        params=params,
        json={"from_week_start": LAST_WEEK.isoformat()},
        headers=h,
    )
    assert r.status_code == 409
    assert ("schedule", "schedule.copied") in await audit_actions(team_org)


async def test_conflicts_inactive_and_location(team_org: Connected) -> None:
    c, h = team_org.client, team_org.headers
    pos = (await positions(team_org))["Server"]
    maya = await employee_id(team_org, "Maya Okafor")
    shift = (
        await _shift(
            team_org,
            employee_id=str(maya),
            position_id=pos,
            starts_at=at(WEEK, 16),
            ends_at=at(WEEK, 22),
        )
    ).json()
    async with scoped_session(team_org.org_id) as db:
        await db.execute(text("DELETE FROM employee_locations WHERE employee_id = :i"), {"i": maya})
        await db.execute(text("UPDATE staff_members SET active = false WHERE id = :i"), {"i": maya})
    kinds = {
        c["kind"] for c in (await _read(team_org))["conflicts"] if c["shift_id"] == shift["id"]
    }
    assert kinds == {"inactive_employee", "not_assigned_to_location"}
    # New shifts for an inactive employee are refused.
    r = await _shift(
        team_org,
        employee_id=str(maya),
        position_id=pos,
        starts_at=at(WEEK, 10),
        ends_at=at(WEEK, 12),
    )
    assert r.status_code == 422
    del c, h


async def test_manager_scope(team_org: Connected, add_member: AddMember) -> None:
    pos = (await positions(team_org))["Server"]
    shift = (
        await _shift(team_org, position_id=pos, starts_at=at(WEEK, 16), ends_at=at(WEEK, 22))
    ).json()
    other = await add_member(team_org.org_id, "manager", [])
    r = await other.client.get(
        "/v1/team/schedule",
        params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
    )
    assert r.status_code == 404
    r = await other.client.delete(f"/v1/team/shifts/{shift['id']}", headers=other.headers)
    assert r.status_code == 404
    gm = await add_member(team_org.org_id, "manager", [team_org.location_id])
    r = await gm.client.delete(f"/v1/team/shifts/{shift['id']}", headers=gm.headers)
    assert r.status_code == 204
