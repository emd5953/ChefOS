"""Availability and time off: employee requests, manager decisions, builder conflicts."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.integrations.providers.square.business_date import business_date
from app.team.models import AvailabilityBlock
from app.team.requests import unavailable_hits
from tests.team.conftest import (
    AddMember,
    Connected,
    Member,
    at,
    audit_actions,
    employee_id,
    positions,
)

# Requests can't start in the past, so these tests use the real calendar.
TODAY = business_date(datetime.now(UTC), "America/New_York")
WEEK = TODAY - timedelta(days=TODAY.weekday()) + timedelta(days=14)  # two weeks out
SAT, MON = WEEK + timedelta(days=5), WEEK


async def _setup(conn: Connected, add_member: AddMember) -> tuple[uuid.UUID, Member, Member]:
    maya_id = await employee_id(conn, "Maya Okafor")
    maya = await add_member(conn.org_id, "employee", [conn.location_id], employee=maya_id)
    gm = await add_member(conn.org_id, "manager", [conn.location_id])
    return maya_id, maya, gm


async def _shift(conn: Connected, **body: Any) -> dict[str, Any]:
    r = await conn.client.post(
        "/v1/team/schedule/shifts",
        params={"location_id": str(conn.location_id), "week_start": WEEK.isoformat()},
        json=body,
        headers=conn.headers,
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


async def _conflicts(conn: Connected, shift_id: str) -> set[str]:
    r = await conn.client.get(
        "/v1/team/schedule",
        params={"location_id": str(conn.location_id), "week_start": WEEK.isoformat()},
    )
    return {c["kind"] for c in r.json()["conflicts"] if c["shift_id"] == shift_id}


async def _kinds(m: Member) -> list[str]:
    return [n["kind"] for n in (await m.client.get("/v1/notifications")).json()["data"]]


async def test_time_off_flow(team_org: Connected, add_member: AddMember) -> None:
    maya_id, maya, gm = await _setup(team_org, add_member)
    pos = await positions(team_org)
    shift = await _shift(
        team_org,
        employee_id=str(maya_id),
        position_id=pos["Server"],
        starts_at=at(SAT, 16),
        ends_at=at(SAT, 23),
    )
    version = (
        await team_org.client.get(
            "/v1/team/schedule",
            params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
        )
    ).json()["schedule"]["version"]
    r = await team_org.client.post(
        "/v1/team/schedule/publish",
        params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
        json={"expected_version": version}, headers=team_org.headers,
    )  # fmt: skip
    assert r.status_code == 200, r.text

    r = await maya.client.post(
        "/v1/me/time-off",
        json={"starts_on": SAT.isoformat(), "ends_on": SAT.isoformat(), "reason": "Wedding"},
        headers=maya.headers,
    )
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "pending"
    assert "time_off_requested" in await _kinds(gm)
    assert await _conflicts(team_org, shift["id"]) == {"time_off_pending"}

    inbox = (await gm.client.get("/v1/team/requests")).json()
    item = next(t for t in inbox["time_off"] if t["id"] == req["id"])
    assert item["employee_name"] == "Maya Okafor"
    assert [s["id"] for s in item["overlapping_shifts"]] == [shift["id"]]  # published shift inline

    r = await gm.client.post(
        f"/v1/team/time-off/{req['id']}/decide",
        json={"decision": "approve", "note": "Enjoy"},
        headers=gm.headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert (await _kinds(maya))[0] == "time_off_decided"
    assert await _conflicts(team_org, shift["id"]) == {"time_off_approved"}  # flagged, not deleted
    r = await gm.client.post(
        f"/v1/team/time-off/{req['id']}/decide", json={"decision": "deny"}, headers=gm.headers
    )
    assert r.status_code == 409
    history = (await gm.client.get("/v1/team/requests", params={"status": "history"})).json()
    assert [t["id"] for t in history["time_off"]] == [req["id"]]
    assert (await gm.client.get("/v1/team/requests")).json()["time_off"] == []

    # The employee can cancel approved time off that hasn't happened; the flag goes away.
    r = await maya.client.post(f"/v1/me/time-off/{req['id']}/cancel", headers=maya.headers)
    assert r.json()["status"] == "cancelled"
    assert await _conflicts(team_org, shift["id"]) == set()
    r = await maya.client.post(f"/v1/me/time-off/{req['id']}/cancel", headers=maya.headers)
    assert r.status_code == 409
    actions = [a for _, a in await audit_actions(team_org) if a.startswith("time_off.")]
    assert actions == ["time_off.requested", "time_off.approved", "time_off.cancelled"]


async def test_time_off_validation_and_privacy(team_org: Connected, add_member: AddMember) -> None:
    _, maya, _ = await _setup(team_org, add_member)
    diego_id = await employee_id(team_org, "Diego Marsh")
    diego = await add_member(team_org.org_id, "employee", [team_org.location_id], employee=diego_id)
    past = TODAY - timedelta(days=1)
    for body in (
        {"starts_on": past.isoformat(), "ends_on": past.isoformat()},
        {"starts_on": SAT.isoformat(), "ends_on": MON.isoformat()},
        {
            "starts_on": SAT.isoformat(),
            "ends_on": SAT.isoformat(),
            "start_time": "18:00",
            "end_time": "09:00",
        },
    ):
        r = await maya.client.post("/v1/me/time-off", json=body, headers=maya.headers)
        assert r.status_code == 422, body
    r = await maya.client.post(
        "/v1/me/time-off",
        json={"starts_on": SAT.isoformat(), "ends_on": SAT.isoformat()},
        headers=maya.headers,
    )
    mine = r.json()
    # Another employee can't see or cancel it.
    assert (await diego.client.get("/v1/me/time-off")).json()["data"] == []
    r = await diego.client.post(f"/v1/me/time-off/{mine['id']}/cancel", headers=diego.headers)
    assert r.status_code == 404
    # Employees can't use the manager inbox or decide.
    assert (await maya.client.get("/v1/team/requests")).status_code == 403
    r = await maya.client.post(
        f"/v1/team/time-off/{mine['id']}/decide", json={"decision": "approve"}, headers=maya.headers
    )
    assert r.status_code == 403
    # A manager without access to her location can't see or decide it.
    outsider = await add_member(team_org.org_id, "manager", [])
    assert (await outsider.client.get("/v1/team/requests")).json()["time_off"] == []
    r = await outsider.client.post(
        f"/v1/team/time-off/{mine['id']}/decide",
        json={"decision": "approve"},
        headers=outsider.headers,
    )
    assert r.status_code == 404


async def test_availability_flow(team_org: Connected, add_member: AddMember) -> None:
    maya_id, maya, gm = await _setup(team_org, add_member)
    pos = (await positions(team_org))["Server"]
    sat = await _shift(
        team_org,
        employee_id=str(maya_id),
        position_id=pos,
        starts_at=at(SAT, 16),
        ends_at=at(SAT, 23),
    )
    mon_eve = await _shift(
        team_org,
        employee_id=str(maya_id),
        position_id=pos,
        starts_at=at(MON, 17),
        ends_at=at(MON, 23),
    )
    mon_day = await _shift(
        team_org,
        employee_id=str(maya_id),
        position_id=pos,
        starts_at=at(MON, 9),
        ends_at=at(MON, 15),
    )

    first = (
        await maya.client.put(
            "/v1/me/availability",
            json={"effective_from": TODAY.isoformat(), "blocks": [{"weekday": 0}]},
            headers=maya.headers,
        )
    ).json()
    body = {
        "effective_from": TODAY.isoformat(),
        "note": "Classes Monday nights",
        "blocks": [{"weekday": 5}, {"weekday": 0, "start_time": "16:00", "end_time": "20:00"}],
    }
    r = await maya.client.put("/v1/me/availability", json=body, headers=maya.headers)
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["status"] == "pending" and second["blocks"][0] == {
        "weekday": 0,
        "start_time": "16:00",
        "end_time": "20:00",
    }
    mine = (await maya.client.get("/v1/me/availability")).json()
    assert mine["pending"]["id"] == second["id"] and mine["current"] is None
    inbox = (await gm.client.get("/v1/team/requests")).json()["availability"]
    assert [a["id"] for a in inbox] == [second["id"]]  # the first was superseded
    assert first["id"] != second["id"]
    assert await _conflicts(team_org, sat["id"]) == set()  # pending availability doesn't flag

    r = await gm.client.post(
        f"/v1/team/availability/{second['id']}/decide",
        json={"decision": "approve"},
        headers=gm.headers,
    )
    assert r.json()["status"] == "approved"
    assert (await _kinds(maya))[0] == "availability_decided"
    assert (await maya.client.get("/v1/me/availability")).json()["current"]["id"] == second["id"]
    assert await _conflicts(team_org, sat["id"]) == {"unavailable"}
    assert await _conflicts(team_org, mon_eve["id"]) == {"unavailable"}
    assert await _conflicts(team_org, mon_day["id"]) == set()
    r = await gm.client.post(
        f"/v1/team/availability/{second['id']}/decide",
        json={"decision": "approve"},
        headers=gm.headers,
    )
    assert r.status_code == 409
    r = await maya.client.put(
        "/v1/me/availability",
        json={"effective_from": (TODAY - timedelta(days=1)).isoformat(), "blocks": []},
        headers=maya.headers,
    )
    assert r.status_code == 422
    r = await maya.client.put(
        "/v1/me/availability",
        json={"effective_from": TODAY.isoformat(), "blocks": [{"weekday": 7}]},
        headers=maya.headers,
    )
    assert r.status_code == 422
    actions = [a for _, a in await audit_actions(team_org) if a.startswith("availability.")]
    assert actions == ["availability.submitted", "availability.submitted", "availability.approved"]


def test_unavailable_hits_cover_every_day_a_shift_spans() -> None:
    zone = ZoneInfo("America/New_York")
    fri = date(2026, 3, 13)
    sat_morning = AvailabilityBlock(weekday=5, start_time=time(0, 0), end_time=time(6, 0))
    late = datetime(2026, 3, 13, 22, tzinfo=zone).astimezone(UTC)
    assert unavailable_hits(late, late + timedelta(hours=4), [sat_morning], zone) == [sat_morning]
    early = datetime(2026, 3, 13, 16, tzinfo=zone).astimezone(UTC)
    assert unavailable_hits(early, early + timedelta(hours=6), [sat_morning], zone) == []
    all_fri = AvailabilityBlock(weekday=fri.weekday(), start_time=None, end_time=None)
    assert unavailable_hits(early, early + timedelta(hours=1), [all_fri], zone) == [all_fri]
