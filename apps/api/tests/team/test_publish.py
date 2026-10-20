"""Publish: snapshot, per-employee diff, alerts, concurrency, and the employee's view."""

import random
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from app.db import scoped_session
from tests.team.conftest import (
    AddMember,
    Clock,
    Connected,
    Member,
    at,
    audit_actions,
    employee_id,
    positions,
)

WEEK = date(2026, 3, 9)


def _params(conn: Connected, week: date = WEEK) -> dict[str, str]:
    return {"location_id": str(conn.location_id), "week_start": week.isoformat()}


async def _shift(conn: Connected, **body: Any) -> dict[str, Any]:
    r = await conn.client.post(
        "/v1/team/schedule/shifts", params=_params(conn), json=body, headers=conn.headers
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


async def _schedule(conn: Connected) -> dict[str, Any]:
    return dict((await conn.client.get("/v1/team/schedule", params=_params(conn))).json())


async def _publish(conn: Connected, ack: bool = True, version: int | None = None) -> Any:
    if version is None:
        version = (await _schedule(conn))["schedule"]["version"]
    return await conn.client.post(
        "/v1/team/schedule/publish",
        params=_params(conn),
        json={"expected_version": version, "acknowledge_conflicts": ack},
        headers=conn.headers,
    )


async def _alerts(m: Member) -> list[dict[str, Any]]:
    return list((await m.client.get("/v1/notifications")).json()["data"])


async def _login(conn: Connected, add_member: AddMember, name: str) -> tuple[uuid.UUID, Member]:
    emp = await employee_id(conn, name)
    return emp, await add_member(conn.org_id, "employee", [conn.location_id], employee=emp)


async def test_publish_alerts_and_employee_view(
    team_org: Connected, add_member: AddMember, frozen: Clock
) -> None:
    pos = await positions(team_org)
    maya_id, maya = await _login(team_org, add_member, "Maya Okafor")
    diego = await employee_id(team_org, "Diego Marsh")
    fri, sat = WEEK + timedelta(days=4), WEEK + timedelta(days=5)
    m1 = await _shift(
        team_org,
        employee_id=str(maya_id),
        position_id=pos["Server"],
        starts_at=at(fri, 16),
        ends_at=at(fri, 23),
    )
    await _shift(
        team_org,
        employee_id=str(diego),
        position_id=pos["Server"],
        starts_at=at(fri, 16),
        ends_at=at(fri, 23),
    )
    await _shift(team_org, position_id=pos["Host"], starts_at=at(sat, 16), ends_at=at(sat, 22))

    # Nothing is visible before publishing.
    week = (await maya.client.get("/v1/me/week", params={"week_start": WEEK.isoformat()})).json()
    assert (week["my_shifts"], week["published_at"]) == ([], None)

    r = await _publish(team_org, ack=False)
    assert (r.status_code, r.json()["code"]) == (
        409,
        "conflicts_unacknowledged",
    )  # the unassigned shift
    r = await _publish(team_org)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["published_version"], out["changed"], out["notified_users"]) == (1, True, 1)
    assert sorted(out["changed_employees"]) == sorted([str(maya_id), str(diego)])
    assert [e["display_name"] for e in out["employees_without_login"]] == ["Diego Marsh"]
    alerts = await _alerts(maya)
    assert [a["kind"] for a in alerts] == ["schedule_published"]
    assert (
        alerts[0]["link"] == f"/my?week={WEEK.isoformat()}" and "Fri 4pm-11pm" in alerts[0]["body"]
    )

    week = (await maya.client.get("/v1/me/week", params={"week_start": WEEK.isoformat()})).json()
    assert [s["id"] for s in week["my_shifts"]] == [m1["id"]]
    assert [t["employee_name"] for t in week["team_shifts"]] == ["Diego Marsh"]
    assert week["published_at"] is not None and week["employee"]["id"] == str(maya_id)
    blob = str(week)
    assert "cost" not in blob and "rate" not in blob

    # Draft edits stay invisible until republish; republishing alerts only who changed.
    r = await team_org.client.patch(
        f"/v1/team/shifts/{m1['id']}",
        json={"starts_at": at(fri, 17), "expected_updated_at": m1["updated_at"]},
        headers=team_org.headers,
    )
    assert r.status_code == 200
    assert (await _schedule(team_org))["schedule"]["unpublished_changes"] == 1
    week = (await maya.client.get("/v1/me/week", params={"week_start": WEEK.isoformat()})).json()
    assert week["my_shifts"][0]["starts_at"] == m1["starts_at"]
    out = (await _publish(team_org)).json()
    assert (out["published_version"], out["changed_employees"], out["notified_users"]) == (
        2,
        [str(maya_id)],
        1,
    )
    alerts = await _alerts(maya)
    assert alerts[0]["kind"] == "schedule_changed" and "Fri moved to 5pm-11pm" in alerts[0]["body"]
    assert ("schedule", "schedule.published") in await audit_actions(team_org)

    # A no-op republish changes nothing.
    out = (await _publish(team_org)).json()
    assert (out["published_version"], out["changed"], out["notified_users"]) == (2, False, 0)
    assert len(await _alerts(maya)) == 2


async def test_stale_version_and_overlap_block_publish(team_org: Connected, frozen: Clock) -> None:
    pos = await positions(team_org)
    maya = await employee_id(team_org, "Maya Okafor")
    s = await _shift(
        team_org,
        employee_id=str(maya),
        position_id=pos["Server"],
        starts_at=at(WEEK, 16),
        ends_at=at(WEEK, 22),
    )
    r = await _publish(team_org, version=0)
    assert (r.status_code, r.json()["code"]) == (409, "schedule_changed")
    async with scoped_session(team_org.org_id) as db:  # an overlap that bypassed the write check
        await db.execute(
            text(
                "INSERT INTO shifts (id, org_id, location_id, schedule_id, employee_id, position_id, "
                "business_date, starts_at, ends_at, created_by) SELECT :i, org_id, location_id, "
                "schedule_id, employee_id, position_id, business_date, starts_at, ends_at, created_by "
                "FROM shifts WHERE id = :s"
            ),
            {"i": uuid.uuid4(), "s": s["id"]},
        )
    r = await _publish(team_org)
    assert (r.status_code, r.json()["code"]) == (409, "shift_overlap")


async def test_past_shifts_do_not_alert(
    team_org: Connected, add_member: AddMember, frozen: Clock
) -> None:
    pos = await positions(team_org)
    maya_id, maya = await _login(team_org, add_member, "Maya Okafor")
    this_week = date(2026, 3, 2)  # the clock is Saturday Mar 7, 15:00
    params = _params(team_org, this_week)
    r = await team_org.client.post(
        "/v1/team/schedule/shifts", params=params, headers=team_org.headers,
        json={"employee_id": str(maya_id), "position_id": pos["Server"],
              "starts_at": at(this_week, 16), "ends_at": at(this_week, 22)},
    )  # fmt: skip
    past = r.json()
    version = (await team_org.client.get("/v1/team/schedule", params=params)).json()["schedule"][
        "version"
    ]
    r = await team_org.client.post(
        "/v1/team/schedule/publish",
        params=params,
        json={"expected_version": version},
        headers=team_org.headers,
    )
    assert r.json()["notified_users"] == 0  # first publish, but the only shift is in the past
    await team_org.client.delete(f"/v1/team/shifts/{past['id']}", headers=team_org.headers)
    version += 1
    r = await team_org.client.post(
        "/v1/team/schedule/publish",
        params=params,
        json={"expected_version": version},
        headers=team_org.headers,
    )
    assert (r.json()["changed"], r.json()["notified_users"]) == (True, 0)
    assert await _alerts(maya) == []


async def _published(conn: Connected) -> dict[str, tuple[Any, ...]]:
    async with scoped_session(conn.org_id) as db:
        rows = await db.execute(
            text("SELECT id, employee_id, starts_at, ends_at, position_id FROM published_shifts")
        )
        return {str(r.id): tuple(r)[1:] for r in rows}


def _per_employee(snap: dict[str, tuple[Any, ...]]) -> dict[Any, set[tuple[Any, ...]]]:
    out: dict[Any, set[tuple[Any, ...]]] = {}
    for sid, (emp, *rest) in snap.items():
        if emp is not None:
            out.setdefault(emp, set()).add((sid, *rest))
    return out


async def test_notified_set_is_exactly_who_changed(
    team_org: Connected, add_member: AddMember, frozen: Clock
) -> None:
    """Property test: random edits between publishes; alerts go to exactly the employees whose
    published shifts differ, and an unchanged republish alerts no one."""
    rng = random.Random(7)
    pos = (await positions(team_org))["Server"]
    names = ["Maya Okafor", "Diego Marsh", "Priya Raman", "Jonah Feld"]
    members = {}
    for n in names:
        emp, m = await _login(team_org, add_member, n)
        members[emp] = m
    staff = list(members)
    days = [WEEK + timedelta(days=i) for i in range(7)]
    for _round in range(6):
        before = _per_employee(await _published(team_org))
        shifts = (await _schedule(team_org))["shifts"]
        for _ in range(rng.randint(0, 4)):
            op = rng.choice(["add", "move", "delete", "reassign"]) if shifts else "add"
            if op == "add":
                d = rng.choice(days)
                h = rng.choice([8, 16])
                await team_org.client.post(
                    "/v1/team/schedule/shifts",
                    params=_params(team_org),
                    headers=team_org.headers,
                    json={
                        "employee_id": str(rng.choice(staff)),
                        "position_id": pos,
                        "starts_at": at(d, h),
                        "ends_at": at(d, h + 6),
                    },
                )  # fmt: skip  (409 overlap is fine: nothing changes)
            else:
                s = rng.choice(shifts)
                if op == "delete":
                    await team_org.client.delete(
                        f"/v1/team/shifts/{s['id']}", headers=team_org.headers
                    )
                else:
                    body: dict[str, Any] = {"expected_updated_at": s["updated_at"]}
                    if op == "move":
                        d = date.fromisoformat(s["business_date"])
                        body |= {"starts_at": at(d, 9), "ends_at": at(d, 14)}
                    else:
                        body["employee_id"] = str(rng.choice(staff))
                    await team_org.client.patch(
                        f"/v1/team/shifts/{s['id']}", json=body, headers=team_org.headers
                    )
            shifts = (await _schedule(team_org))["shifts"]
        counts = {e: len(await _alerts(m)) for e, m in members.items()}
        r = await _publish(team_org)
        assert r.status_code == 200, r.text
        after = _per_employee(await _published(team_org))
        expected = {e for e in staff if before.get(e, set()) != after.get(e, set())}
        alerted = {e for e, m in members.items() if len(await _alerts(m)) > counts[e]}
        assert alerted == expected
        assert {uuid.UUID(x) for x in r.json()["changed_employees"]} == expected
        # Republishing right away is a no-op.
        r = await _publish(team_org)
        assert r.json()["changed"] is False
        assert {e for e, m in members.items() if len(await _alerts(m)) > counts[e]} == expected


async def test_notifications_api(team_org: Connected, add_member: AddMember) -> None:
    emp, m = await _login(team_org, add_member, "Maya Okafor")
    other = await add_member(team_org.org_id, "manager", [team_org.location_id])
    async with scoped_session(team_org.org_id) as db:
        for i in range(3):
            await db.execute(
                text(
                    "INSERT INTO notifications (id, org_id, user_id, kind, title) VALUES (:i, :o, :u, 'schedule_changed', :t)"
                ),
                {"i": uuid.uuid4(), "o": team_org.org_id, "u": m.user_id, "t": f"n{i}"},
            )
    body = (await m.client.get("/v1/notifications", params={"unread_only": "true"})).json()
    assert body["unread_count"] == 3 and len(body["data"]) == 3
    first = body["data"][0]["id"]
    # Another user can't mark mine read.
    r = await other.client.post(
        "/v1/notifications/read", json={"ids": [first]}, headers=other.headers
    )
    assert r.json()["unread_count"] == 0
    r = await m.client.post("/v1/notifications/read", json={"ids": [first]}, headers=m.headers)
    assert r.json()["unread_count"] == 2
    r = await m.client.post("/v1/notifications/read", json={"all": True}, headers=m.headers)
    assert r.json()["unread_count"] == 0
    assert len((await m.client.get("/v1/notifications")).json()["data"]) == 3
    del emp


async def test_me_requires_an_employee_record(team_org: Connected) -> None:
    r = await team_org.client.get("/v1/me/week")
    assert r.status_code == 404
    r = await team_org.client.get("/v1/me/week", params={"week_start": "2026-03-10"})
    assert r.status_code in (404, 422)


async def test_pos_deactivation_notifies_managers(
    team_org: Connected, add_member: AddMember
) -> None:
    from tests.team.test_employees import _ingest_members, _member_payload

    gm = await add_member(team_org.org_id, "manager", [team_org.location_id])
    payload = _member_payload("TM_BUS1")
    payload["status"] = "INACTIVE"
    await _ingest_members(team_org, [payload])
    alerts = (await gm.client.get("/v1/notifications")).json()["data"]
    assert [a["kind"] for a in alerts] == ["employee_deactivated"]
    owner_alerts = (await team_org.client.get("/v1/notifications")).json()["data"]
    assert [a["kind"] for a in owner_alerts] == ["employee_deactivated"]
