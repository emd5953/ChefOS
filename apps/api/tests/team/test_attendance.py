"""Clock-in alerts (TEAM-6): late, missed and on-time shifts, overnight shifts, alert dedupe,
tenancy, and daily_labor.scheduled_hours from published shifts.

Timecards come from the fake seller, whose clock-ins follow its own shift pattern: a published
shift for someone it has off that day never gets a clock-in."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from app.db import scoped_session
from app.integrations import readmodels, sync
from app.integrations.providers.square import fake_data as fd
from app.team import attendance
from app.team.attendance import Punch
from app.team.demo import resolve_refs_any
from tests.team.conftest import (
    NY,
    AddMember,
    Clock,
    Connect,
    Connected,
    Member,
    at,
    import_team,
    positions,
)

DAY = date(2026, 3, 5)  # Thursday; the clock starts at 15:00 New York
WEEK = date(2026, 3, 2)


def test_open_punch_running_hours_use_as_of_and_closed_punches_use_paid_hours() -> None:
    clock_in = datetime(2026, 3, 5, 14, tzinfo=UTC)
    as_of = clock_in + timedelta(hours=2, minutes=30)
    open_punch = Punch(uuid.uuid4(), None, DAY, clock_in, None, Decimal("0"))
    closed_punch = Punch(
        uuid.uuid4(), None, DAY, clock_in - timedelta(hours=4), clock_in, Decimal("3.25")
    )

    assert attendance._hours([open_punch], as_of) == 2.5
    assert attendance._hours([closed_punch], as_of) == 3.25
    # Day totals sum the same closed paid hours and open running time.
    assert attendance._hours([open_punch, closed_punch], as_of) == 5.75


@pytest.fixture
def now(clock: Clock, monkeypatch: pytest.MonkeyPatch) -> Clock:
    from app.team import schedule

    monkeypatch.setattr(schedule, "_now", clock)
    monkeypatch.setattr(attendance, "_now", clock)
    clock.now = datetime(DAY.year, DAY.month, DAY.day, 15, tzinfo=NY).astimezone(UTC)
    return clock


async def _refs(conn: Connected) -> dict[str, uuid.UUID]:
    async with scoped_session(conn.org_id) as db:
        return await resolve_refs_any(db, "staff_member", [s.id for s in fd.STAFF])


def _off(d: date) -> list[fd.StaffMember]:
    """Team members the fake seller has no shift for on `d` (so no clock-ins)."""
    working = {s.staff_id for s in fd.shifts_for_day(d)}
    return [s for s in fd.STAFF if s.id not in working]


async def _shift(
    conn: Connected,
    emp: uuid.UUID | None,
    pos: str,
    start: str,
    end: str,
    break_minutes: int = 0,
) -> str:
    body: dict[str, Any] = {
        "position_id": pos, "starts_at": start, "ends_at": end, "break_minutes": break_minutes,
    }  # fmt: skip
    if emp is not None:
        body["employee_id"] = str(emp)
    r = await conn.client.post(
        "/v1/team/schedule/shifts",
        params={"location_id": str(conn.location_id), "week_start": WEEK.isoformat()},
        json=body,
        headers=conn.headers,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _publish(conn: Connected) -> None:
    params = {"location_id": str(conn.location_id), "week_start": WEEK.isoformat()}
    sched = (await conn.client.get("/v1/team/schedule", params=params)).json()
    r = await conn.client.post(
        "/v1/team/schedule/publish",
        params=params,
        json={"expected_version": sched["schedule"]["version"], "acknowledge_conflicts": True},
        headers=conn.headers,
    )
    assert r.status_code == 200, r.text


async def _sync_timecards(conn: Connected, clock: Clock) -> None:
    await sync.sync_connection(
        conn.org_id, conn.connection_id, streams=["timecards"], force=True, now=clock.now
    )


async def _detect(conn: Connected, clock: Clock) -> int:
    return await attendance.check_connection(conn.org_id, conn.connection_id, now=clock.now)


async def _day(conn: Connected, d: date, viewer: Member | None = None) -> dict[str, Any]:
    r = await (viewer or conn).client.get(
        "/v1/team/attendance",
        params={"location_id": str(conn.location_id), "from": d.isoformat(), "to": d.isoformat()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period"] == {"from": d.isoformat(), "to": d.isoformat()}
    return dict(body["days"][0])


async def _alerts(client: Any) -> list[dict[str, Any]]:
    rows = (await client.get("/v1/notifications")).json()["data"]
    return [n for n in rows if n["kind"] in attendance.ALERT_KINDS]


async def test_late_missed_and_on_time_alert_managers_once(
    team_org: Connected, add_member: AddMember, now: Clock
) -> None:
    pos, refs = await positions(team_org), await _refs(team_org)
    off = _off(DAY)
    # On time: someone the seller has clocked in already, scheduled exactly as they work.
    worked = next(
        s
        for s in fd.shifts_for_day(DAY)
        if s.staff_id in refs and s.start.astimezone(NY) <= now.now - timedelta(minutes=10)
    )
    on_time = await _shift(
        team_org,
        refs[worked.staff_id],
        pos["Server"],
        worked.start.isoformat(),
        worked.end.isoformat(),
    )
    # Late: started 30 minutes ago, nobody clocked in. Missed: ended at noon, nobody came.
    late = await _shift(team_org, refs[off[0].id], pos["Server"], at(DAY, 14, 30), at(DAY, 20))
    missed = await _shift(team_org, refs[off[1].id], pos["Server"], at(DAY, 8), at(DAY, 12))
    await _shift(team_org, None, pos["Host"], at(DAY, 9), at(DAY, 13))  # open: nobody to judge
    await _publish(team_org)
    await _sync_timecards(team_org, now)
    gm = await add_member(team_org.org_id, "manager", [team_org.location_id])
    other = await add_member(team_org.org_id, "manager", [])  # manages no location here

    assert await _detect(team_org, now) == 4  # late + missed, to the owner and the GM
    by_kind = {a["kind"]: a for a in await _alerts(gm.client)}
    assert set(by_kind) == {"shift_late", "shift_missed"}
    assert by_kind["shift_late"]["entity_id"] == late
    assert by_kind["shift_missed"]["entity_id"] == missed
    late_name = f"{off[0].given_name} {off[0].family_name}"
    assert by_kind["shift_late"]["title"] == f"{late_name} hasn't clocked in"
    assert by_kind["shift_late"]["link"].startswith("/app/team/attendance?")
    assert await _alerts(other.client) == []

    day = await _day(team_org, DAY, gm)
    status = {s["shift_id"]: (s["status"], s["minutes_late"]) for s in day["shifts"]}
    assert status == {on_time: ("in_progress", None), late: ("late", 30), missed: ("missed", None)}
    assert day["counts"] == {
        "on_time": 0, "late": 1, "missed": 1, "upcoming": 0, "awaiting_data": 0,
        "in_progress": 1,
    }  # fmt: skip
    row = next(s for s in day["shifts"] if s["shift_id"] == on_time)
    assert row["clock_in"] is not None and row["clock_out"] is None  # still on the clock
    async with scoped_session(team_org.org_id) as db:
        actual = await db.scalar(
            text("""SELECT sum(CASE WHEN clock_out IS NULL
                                  THEN greatest(0, extract(epoch FROM (:as_of - clock_in)) / 3600)
                                  ELSE paid_hours END)
                     FROM timecards WHERE business_date = :d"""),
            {"d": DAY, "as_of": now.now},
        )
    assert day["actual_hours"] == round(float(actual), 2)
    # Late 5.5h + missed 4h + open 4h, plus the on-time shift (the seller's own times).
    assert day["scheduled_hours"] == round(row["scheduled_hours"] + 5.5 + 4 + 4, 2)

    # Dedupe: re-running, later syncs, and the clock moving on write nothing new.
    assert await _detect(team_org, now) == 0
    now.advance(minutes=5)
    await _sync_timecards(team_org, now)
    assert await _detect(team_org, now) == 0
    assert sorted(a["kind"] for a in await _alerts(gm.client)) == ["shift_late", "shift_missed"]


async def test_overnight_shift_is_late_then_missed_on_its_start_date(
    team_org: Connected, add_member: AddMember, now: Clock
) -> None:
    pos, refs = await positions(team_org), await _refs(team_org)
    fri = DAY + timedelta(days=1)
    person = _off(DAY)[0]  # works Friday, but nobody clocks in before 06:00
    overnight = await _shift(team_org, refs[person.id], pos["Server"], at(DAY, 22), at(fri, 6))
    await _publish(team_org)
    await _sync_timecards(team_org, now)
    gm = await add_member(team_org.org_id, "manager", [team_org.location_id])

    now.advance(hours=7, minutes=15)  # 22:15
    # Timecards are only known up to the last sync: not a no-show yet.
    shift = (await _day(team_org, DAY, gm))["shifts"][0]
    assert (shift["shift_id"], shift["status"]) == (overnight, "awaiting_data")
    assert await _detect(team_org, now) == 0

    await _sync_timecards(team_org, now)
    assert await _detect(team_org, now) == 2
    assert [a["kind"] for a in await _alerts(gm.client)] == ["shift_late"]

    now.advance(hours=8)  # Friday 06:15
    await _sync_timecards(team_org, now)
    assert await _detect(team_org, now) == 2
    assert sorted(a["kind"] for a in await _alerts(gm.client)) == ["shift_late", "shift_missed"]
    day = await _day(team_org, DAY, gm)  # listed under the business date it started on
    shift = next(s for s in day["shifts"] if s["shift_id"] == overnight)
    assert (shift["status"], shift["scheduled_hours"]) == ("missed", 8.0)
    assert all(s["shift_id"] != overnight for s in (await _day(team_org, fri, gm))["shifts"])


async def test_attendance_is_tenant_and_location_scoped(
    connect: Connect, clock: Clock, add_member: AddMember, now: Clock
) -> None:
    a, b = await connect(), await connect()
    for c in (a, b):
        await import_team(c, clock)
    refs = await _refs(a)
    late = await _shift(
        a, refs[_off(DAY)[0].id], (await positions(a))["Server"], at(DAY, 14), at(DAY, 20)
    )
    await _publish(a)
    await _sync_timecards(a, clock)
    assert await _detect(a, clock) > 0
    assert await _detect(b, clock) == 0  # b has no shifts; a's are invisible to it

    params = {"location_id": str(a.location_id), "from": DAY.isoformat(), "to": DAY.isoformat()}
    assert (await b.client.get("/v1/team/attendance", params=params)).status_code == 404
    b_gm = await add_member(b.org_id, "manager", [b.location_id])
    assert (await b_gm.client.get("/v1/team/attendance", params=params)).status_code == 404
    assert await _alerts(b_gm.client) == [] and await _alerts(b.client) == []
    elsewhere = await add_member(a.org_id, "manager", [])
    assert (await elsewhere.client.get("/v1/team/attendance", params=params)).status_code == 404
    employee = await add_member(a.org_id, "employee", [a.location_id])
    assert (await employee.client.get("/v1/team/attendance", params=params)).status_code == 403
    async with scoped_session(b.org_id) as db:
        seen = await db.scalar(
            text("SELECT count(*) FROM notifications WHERE entity_id = :s"), {"s": late}
        )
    assert seen == 0


async def test_scheduled_hours_fill_daily_labor_from_published_shifts(
    team_org: Connected, now: Clock
) -> None:
    pos, refs = await positions(team_org), await _refs(team_org)
    off = _off(DAY)

    async def labor() -> tuple[float, Any]:
        async with scoped_session(team_org.org_id) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT scheduled_hours, data_as_of FROM daily_labor "
                        "WHERE business_date = :d"
                    ),
                    {"d": DAY},
                )
            ).one()
        return float(row.scheduled_hours), row.data_as_of

    await _sync_timecards(team_org, now)
    hours, as_of = await labor()
    assert hours == 0  # nothing published yet

    # Drafts don't count; publishing refreshes the existing row and keeps its data_as_of.
    await _shift(team_org, refs[off[0].id], pos["Server"], at(DAY, 16), at(DAY, 23))
    assert (await labor())[0] == 0
    await _publish(team_org)
    assert await labor() == (7.0, as_of)
    await _shift(team_org, None, pos["Host"], at(DAY, 9), at(DAY, 13))
    await _shift(
        team_org, refs[off[1].id], pos["Server"], at(DAY, 10), at(DAY, 17), break_minutes=30
    )
    await _publish(team_org)
    assert (await labor())[0] == 17.5  # 7 + 4 open + 6.5 paid

    # A rebuild from timecards derives the same scheduled hours.
    async with scoped_session(team_org.org_id) as db:
        await readmodels.rebuild_daily_labor(
            db, team_org.org_id, [(team_org.location_id, DAY)], now.now
        )
    assert (await labor())[0] == 17.5
