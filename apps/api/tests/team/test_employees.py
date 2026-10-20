"""Employees, positions, stations: POS import, field ownership, scope, merge, audit."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.db import scoped_session
from app.integrations import pipeline
from app.integrations.links import load_links
from app.integrations.providers.square import fake_data as fd
from app.integrations.providers.square.types import RawItem
from tests.team.conftest import (
    AddMember,
    Clock,
    Connect,
    Connected,
    audit_actions,
    by_name,
    employee_id,
    import_team,
)

TITLES = {"Server", "Bartender", "Host", "Line Cook", "Sous Chef", "Dishwasher", "Busser"}


async def _employees(conn: Connected, **params: Any) -> list[dict[str, Any]]:
    r = await conn.client.get("/v1/team/employees", params=params)
    assert r.status_code == 200, r.text
    return list(r.json()["data"])


def _member_payload(member_id: str) -> dict[str, Any]:
    """A team member payload in the fake seller's shape (see FakeSquareClient)."""
    for s in fd.STAFF:
        if s.id == member_id:
            break
    else:
        raise AssertionError(member_id)
    return {
        "id": s.id,
        "status": "ACTIVE",
        "given_name": s.given_name,
        "family_name": s.family_name,
        "email_address": f"{s.given_name}.{s.family_name}@juniperhouse.example".lower(),
        "updated_at": "2026-03-01T00:00:00Z",
        "assigned_locations": {
            "assignment_type": "EXPLICIT_LOCATIONS",
            "location_ids": [fd.LOCATION_ID],
        },
        "wage_setting": {
            "job_assignments": [
                {
                    "job_title": s.role,
                    "pay_type": "HOURLY",
                    "hourly_rate": {"amount": s.hourly_cents, "currency": "USD"},
                }
            ]
        },
    }


async def _ingest_members(conn: Connected, payloads: list[dict[str, Any]]) -> None:
    items = [RawItem(p["id"], None, None, p) for p in payloads]
    async with scoped_session(conn.org_id) as db:
        ctx = pipeline.ConnCtx(
            conn.org_id, conn.connection_id, await load_links(db, conn.connection_id)
        )
        await pipeline.ingest(db, ctx, "team_members", items, None, datetime.now(UTC))


async def test_import_creates_employees_positions_and_locations(team_org: Connected) -> None:
    rows = await _employees(team_org)
    assert len(rows) == 18
    maya = by_name(rows)["Maya Okafor"]
    assert maya["source"] == "square"
    assert maya["email"] == "maya.okafor@juniperhouse.example"
    assert [(p["name"], p["is_primary"]) for p in maya["positions"]] == [("Server", True)]
    assert maya["location_ids"] == [str(team_org.location_id)]
    assert (maya["pos_hourly_rate"], maya["effective_rate"], maya["login"]) == (1600, 1600, "none")
    positions = (await team_org.client.get("/v1/team/positions")).json()["data"]
    assert {p["name"] for p in positions} == TITLES
    assert len({p["color"] for p in positions}) == len(TITLES)
    # The location filter returns the same people; nothing is at a location they don't work at.
    assert len(await _employees(team_org, location_id=str(team_org.location_id))) == 18


async def test_resync_preserves_manager_edits_and_creates_positions_once(
    team_org: Connected, clock: Clock
) -> None:
    c, h = team_org.client, team_org.headers
    maya = by_name(await _employees(team_org))["Maya Okafor"]
    positions = {p["name"]: p["id"] for p in (await c.get("/v1/team/positions")).json()["data"]}
    r = await c.patch(
        f"/v1/team/employees/{maya['id']}",
        json={
            "phone": "+1 718 555 0100",
            "rate_override": 1850,
            "email": "maya@example.com",
            "positions": [
                {"position_id": positions["Bartender"], "is_primary": True},
                {"position_id": positions["Server"]},
            ],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    # Square changes her name and rate; a manager-edited field must not move.
    payload = _member_payload("TM_SRV1")
    payload["given_name"] = "Maya J."
    payload["wage_setting"]["job_assignments"][0]["hourly_rate"]["amount"] = 1700
    await _ingest_members(team_org, [payload])
    await import_team(team_org, clock)  # and a full resync from the fake

    after = by_name(await _employees(team_org))
    maya = after.get("Maya J. Okafor") or after["Maya Okafor"]
    assert maya["phone"] == "+1 718 555 0100"
    assert maya["email"] == "maya@example.com"
    assert (maya["rate_override"], maya["effective_rate"]) == (1850, 1850)
    assert [(p["name"], p["is_primary"]) for p in maya["positions"]] == [
        ("Bartender", True),
        ("Server", False),
    ]
    positions_after = (await c.get("/v1/team/positions")).json()["data"]
    assert sorted(p["name"] for p in positions_after) == sorted(TITLES)


async def test_pos_change_updates_pos_fields(team_org: Connected) -> None:
    payload = _member_payload("TM_SRV2")
    payload["given_name"] = "Diego R."
    payload["wage_setting"]["job_assignments"][0]["hourly_rate"]["amount"] = 1999
    await _ingest_members(team_org, [payload])
    diego = by_name(await _employees(team_org))["Diego R. Marsh"]
    assert (diego["pos_hourly_rate"], diego["effective_rate"]) == (1999, 1999)


async def test_square_deactivation_is_audited_once(team_org: Connected) -> None:
    payload = _member_payload("TM_BUS1")
    payload["status"] = "INACTIVE"
    await _ingest_members(team_org, [payload])
    await _ingest_members(team_org, [payload])  # already inactive: no second event
    sam = by_name(await _employees(team_org, include_inactive="true"))["Sam Whitaker"]
    assert sam["active"] is False
    assert "Sam Whitaker" not in by_name(await _employees(team_org))
    actions = await audit_actions(team_org)
    assert actions.count(("employee", "employee.deactivated_by_pos")) == 1


async def test_assigned_locations_follow_linked_locations(connect: Connect, clock: Clock) -> None:
    conn = await connect(extra_locations=1, location_name="Main")
    await import_team(conn, clock)
    async with scoped_session(conn.org_id) as db:
        linked = await db.scalar(
            text("SELECT count(*) FROM integration_location_links WHERE location_id IS NOT NULL")
        )
    # Two platform locations: the Square location is not auto-linked, so nothing is assigned.
    assert linked == 0
    rows = await _employees(conn, include_inactive="true")
    assert len(rows) == 18 and all(r["location_ids"] == [] for r in rows)
    r = await conn.client.patch(
        f"/v1/integrations/connections/{conn.connection_id}/locations",
        json={
            "links": [
                {"external_location_id": fd.LOCATION_ID, "location_id": str(conn.location_id)}
            ]
        },
        headers=conn.headers,
    )
    assert r.status_code == 200, r.text
    await import_team(conn, clock)
    assert all(r["location_ids"] == [str(conn.location_id)] for r in await _employees(conn))
    # "All locations" maps to every linked location.
    payload = _member_payload("TM_HOST1")
    payload["assigned_locations"] = {"assignment_type": "ALL_CURRENT_LOCATIONS"}
    await _ingest_members(conn, [payload])
    zoe = by_name(await _employees(conn))["Zoe Bennett"]
    assert zoe["location_ids"] == [str(conn.location_id)]


async def test_pos_owned_fields_are_read_only(team_org: Connected) -> None:
    maya = await employee_id(team_org, "Maya Okafor")
    for body in ({"display_name": "M"}, {"active": False}, {"hourly_rate": 1}, {"job_title": "x"}):
        r = await team_org.client.patch(
            f"/v1/team/employees/{maya}", json=body, headers=team_org.headers
        )
        assert r.status_code == 422, body
        assert r.json()["code"] == "pos_owned_field"
        assert list(r.json()["errors"]) == list(body)


async def test_manual_employee_lifecycle(team_org: Connected) -> None:
    c, h = team_org.client, team_org.headers
    pos = (await c.post("/v1/team/positions", json={"name": "Expo"}, headers=h)).json()
    r = await c.post(
        "/v1/team/employees",
        json={
            "display_name": "  Kai Lund ",
            "email": "KAI@example.com",
            "location_ids": [str(team_org.location_id)],
            "positions": [{"position_id": pos["id"]}],
            "rate_override": 2100,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    kai = r.json()
    assert (kai["display_name"], kai["email"], kai["source"]) == (
        "Kai Lund",
        "kai@example.com",
        "manual",
    )
    assert kai["positions"][0]["is_primary"] is True
    assert (kai["pos_hourly_rate"], kai["effective_rate"]) == (None, 2100)
    r = await c.patch(
        f"/v1/team/employees/{kai['id']}", json={"display_name": "Kai Lunde"}, headers=h
    )
    assert r.json()["display_name"] == "Kai Lunde"
    r = await c.patch(f"/v1/team/employees/{kai['id']}", json={"hourly_rate": 10}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    r = await c.patch(f"/v1/team/employees/{kai['id']}", json={"active": False}, headers=h)
    assert r.json()["active"] is False
    # Validation: unknown location, unknown position, two primaries.
    bad = [
        {"display_name": "X", "location_ids": [str(uuid.uuid4())]},
        {"display_name": "X", "location_ids": [str(team_org.location_id)], "positions": [{"position_id": str(uuid.uuid4())}]},
        {"display_name": "X", "location_ids": [str(team_org.location_id)], "positions": [
            {"position_id": pos["id"], "is_primary": True}, {"position_id": str(uuid.uuid4()), "is_primary": True}]},
        {"display_name": "X", "location_ids": []},
    ]  # fmt: skip
    for body in bad:
        r = await c.post("/v1/team/employees", json=body, headers=h)
        assert r.status_code == 422, (body, r.text)


async def test_positions_and_stations(team_org: Connected) -> None:
    c, h = team_org.client, team_org.headers
    r = await c.post("/v1/team/positions", json={"name": "server"}, headers=h)
    assert r.status_code == 409 and "name" in r.json()["errors"]
    pos = (
        await c.post("/v1/team/positions", json={"name": "Barback", "color": "#ABCDEF"}, headers=h)
    ).json()
    assert pos["color"] == "#abcdef"
    r = await c.patch(f"/v1/team/positions/{pos['id']}", json={"archived": True}, headers=h)
    assert r.json()["archived_at"] is not None
    names = {p["name"] for p in (await c.get("/v1/team/positions")).json()["data"]}
    assert "Barback" not in names
    r = await c.get("/v1/team/positions", params={"include_archived": "true"})
    assert "Barback" in {p["name"] for p in r.json()["data"]}

    r = await c.post(
        "/v1/team/stations",
        json={"location_id": str(team_org.location_id), "name": "Grill"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    station = r.json()
    r = await c.patch(f"/v1/team/stations/{station['id']}", json={"name": "Grill 1"}, headers=h)
    assert r.json()["name"] == "Grill 1"
    listed = (
        await c.get("/v1/team/stations", params={"location_id": str(team_org.location_id)})
    ).json()
    assert [s["name"] for s in listed["data"]] == ["Grill 1"]
    r = await c.get("/v1/team/stations", params={"location_id": str(uuid.uuid4())})
    assert r.status_code == 404


async def test_merge_moves_refs_timecards_and_login(
    team_org: Connected, add_member: AddMember
) -> None:
    c, h = team_org.client, team_org.headers
    manual = (
        await c.post(
            "/v1/team/employees",
            json={
                "display_name": "Maya O",
                "phone": "555-0101",
                "location_ids": [str(team_org.location_id)],
            },
            headers=h,
        )
    ).json()
    maya = await employee_id(team_org, "Maya Okafor")
    await add_member(
        team_org.org_id, "employee", [team_org.location_id], employee=uuid.UUID(manual["id"])
    )
    async with scoped_session(team_org.org_id) as db:
        await db.execute(
            text(
                "INSERT INTO timecards (id, org_id, connection_id, external_id, location_id, "
                "staff_member_id, external_staff_ref, business_date, clock_in, hourly_rate, "
                "paid_hours, cost, source, source_version, source_updated_at) VALUES "
                "(:i, :o, :c, 'tc-m', :l, :s, 'TM_SRV1', '2026-03-02', now(), 1600, 1, 1600, "
                "'square', 1, now())"
            ),
            {"i": uuid.uuid4(), "o": team_org.org_id, "c": team_org.connection_id,
             "l": team_org.location_id, "s": maya},
        )  # fmt: skip

    # The Square record folds into the manual one, which keeps the login and becomes POS-linked.
    r = await c.post(f"/v1/team/employees/{maya}/merge", json={"into_id": manual["id"]}, headers=h)
    assert r.status_code == 200, r.text
    merged = r.json()
    assert (merged["source"], merged["display_name"], merged["phone"]) == (
        "square",
        "Maya Okafor",
        "555-0101",
    )
    assert (merged["login"], merged["pos_hourly_rate"]) == ("active", 1600)
    assert [p["name"] for p in merged["positions"]] == ["Server"]
    async with scoped_session(team_org.org_id) as db:
        ref = await db.scalar(
            text(
                "SELECT entity_id FROM external_refs WHERE external_id = 'TM_SRV1' AND entity_type = 'staff_member'"
            )
        )
        tc = await db.scalar(
            text("SELECT staff_member_id FROM timecards WHERE external_id = 'tc-m'")
        )
        src = (
            await db.execute(
                text("SELECT active, merged_into_id FROM staff_members WHERE id = :i"), {"i": maya}
            )
        ).one()
    assert str(ref) == str(tc) == manual["id"]
    assert (src.active, str(src.merged_into_id)) == (False, manual["id"])
    assert ("employee", "employee.merged") in await audit_actions(team_org)
    # A second merge of the same record is refused; so is merging two logins.
    r = await c.post(f"/v1/team/employees/{maya}/merge", json={"into_id": manual["id"]}, headers=h)
    assert r.status_code == 409
    diego = await employee_id(team_org, "Diego Marsh")
    await add_member(team_org.org_id, "employee", [team_org.location_id], employee=diego)
    r = await c.post(f"/v1/team/employees/{diego}/merge", json={"into_id": manual["id"]}, headers=h)
    assert r.status_code == 409


async def test_manager_scope_and_admin_only_merge(
    team_org: Connected, add_member: AddMember
) -> None:
    c, h = team_org.client, team_org.headers
    other = (
        await c.post(
            "/v1/locations", json={"name": "Annex", "timezone": "America/New_York"}, headers=h
        )
    ).json()
    outside = (
        await c.post(
            "/v1/team/employees",
            json={"display_name": "Annex Cook", "location_ids": [other["id"]]},
            headers=h,
        )
    ).json()
    gm = await add_member(team_org.org_id, "manager", [team_org.location_id])
    rows = (await gm.client.get("/v1/team/employees")).json()["data"]
    assert len(rows) == 18 and "Annex Cook" not in by_name(rows)
    assert (
        await gm.client.get("/v1/team/employees", params={"location_id": other["id"]})
    ).status_code == 404
    r = await gm.client.patch(
        f"/v1/team/employees/{outside['id']}", json={"phone": "555"}, headers=gm.headers
    )
    assert r.status_code == 404
    maya = await employee_id(team_org, "Maya Okafor")
    # A manager edits only their locations: adding the annex is refused, and removing all
    # their locations keeps the ones they can't see.
    r = await gm.client.patch(
        f"/v1/team/employees/{maya}", json={"location_ids": [other["id"]]}, headers=gm.headers
    )
    assert r.status_code == 422
    r = await gm.client.post(
        f"/v1/team/employees/{maya}/merge", json={"into_id": outside["id"]}, headers=gm.headers
    )
    assert r.status_code == 403
    r = await c.patch(
        f"/v1/team/employees/{maya}",
        json={"location_ids": [str(team_org.location_id), other["id"]]},
        headers=h,
    )
    assert len(r.json()["location_ids"]) == 2
    r = await gm.client.patch(
        f"/v1/team/employees/{maya}",
        json={"location_ids": [str(team_org.location_id)]},
        headers=gm.headers,
    )
    assert sorted(r.json()["location_ids"]) == sorted([str(team_org.location_id), other["id"]])


async def test_employee_role_gets_403(team_org: Connected, add_member: AddMember) -> None:
    emp = await add_member(team_org.org_id, "employee", [team_org.location_id])
    assert (await emp.client.get("/v1/team/employees")).status_code == 403
    r = await emp.client.post("/v1/team/positions", json={"name": "X"}, headers=emp.headers)
    assert r.status_code == 403


async def test_other_org_ids_read_as_missing(
    team_org: Connected, connect: Connect, clock: Clock
) -> None:
    other = await connect()
    await import_team(other, clock)
    theirs = await employee_id(other, "Maya Okafor")
    r = await team_org.client.patch(
        f"/v1/team/employees/{theirs}", json={"phone": "555-0000"}, headers=team_org.headers
    )
    assert r.status_code == 404
    mine = await employee_id(team_org, "Maya Okafor")
    r = await team_org.client.post(
        f"/v1/team/employees/{mine}/merge", json={"into_id": str(theirs)}, headers=team_org.headers
    )
    assert r.status_code == 404


async def test_mutations_are_audited_in_the_same_transaction(team_org: Connected) -> None:
    c, h = team_org.client, team_org.headers
    pos = (await c.post("/v1/team/positions", json={"name": "Runner"}, headers=h)).json()
    await c.patch(f"/v1/team/positions/{pos['id']}", json={"color": "#000000"}, headers=h)
    emp = (
        await c.post(
            "/v1/team/employees",
            json={"display_name": "Ana", "location_ids": [str(team_org.location_id)]},
            headers=h,
        )
    ).json()
    await c.patch(f"/v1/team/employees/{emp['id']}", json={"phone": "555-1234"}, headers=h)
    await c.patch(f"/v1/team/employees/{emp['id']}", json={"phone": "555-1234"}, headers=h)  # no-op
    st = (
        await c.post(
            "/v1/team/stations",
            json={"location_id": str(team_org.location_id), "name": "Bar"},
            headers=h,
        )
    ).json()
    await c.patch(f"/v1/team/stations/{st['id']}", json={"archived": True}, headers=h)
    # A failing request leaves no event behind.
    await c.patch(
        f"/v1/team/employees/{emp['id']}",
        json={"positions": [{"position_id": str(uuid.uuid4())}]},
        headers=h,
    )
    actions = [
        a for a in await audit_actions(team_org) if a[0] in ("employee", "position", "station")
    ]
    assert actions == [
        ("position", "position.created"),
        ("position", "position.updated"),
        ("employee", "employee.created"),
        ("employee", "employee.updated"),
        ("station", "station.created"),
        ("station", "station.archived"),
    ]
    async with scoped_session(team_org.org_id) as db:
        details = await db.scalar(
            text("SELECT details FROM audit_events WHERE action = 'employee.updated'")
        )
    assert details == {"before": {"phone": None}, "after": {"phone": "555-1234"}}
