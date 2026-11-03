"""Employee trades require coworker and manager approval, then schedule publication."""

from datetime import date, timedelta
from typing import Any

from tests.team.conftest import AddMember, Clock, Connected, at, audit_actions

WEEK = date(2026, 3, 9)


async def _shift(conn: Connected, employee_id: str, position_id: str, day: date) -> dict[str, Any]:
    response = await conn.client.post(
        "/v1/team/schedule/shifts",
        params={"location_id": str(conn.location_id), "week_start": WEEK.isoformat()},
        json={
            "employee_id": employee_id,
            "position_id": position_id,
            "starts_at": at(day, 16),
            "ends_at": at(day, 20),
        },
        headers=conn.headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_trade_requires_acceptance_and_manager_publish(
    team_org: Connected, add_member: AddMember, frozen: Clock
) -> None:
    pos = (await team_org.client.get("/v1/team/positions")).json()["data"]
    server_id = next(p["id"] for p in pos if p["name"] == "Server")
    employees: list[tuple[str, Any]] = []
    for name, email in (("Trade A", "trade-a@example.test"), ("Trade B", "trade-b@example.test")):
        created = await team_org.client.post(
            "/v1/team/employees",
            json={
                "display_name": name,
                "email": email,
                "location_ids": [str(team_org.location_id)],
                "positions": [{"position_id": server_id, "is_primary": True}],
            },
            headers=team_org.headers,
        )
        assert created.status_code == 201, created.text
        employee_id = created.json()["id"]
        account = await add_member(
            team_org.org_id, "employee", [team_org.location_id], employee=employee_id
        )
        employees.append((employee_id, account))
    friday, saturday = WEEK + timedelta(days=4), WEEK + timedelta(days=5)
    offered = await _shift(team_org, employees[0][0], server_id, friday)
    requested = await _shift(team_org, employees[1][0], server_id, saturday)
    publish = await team_org.client.post(
        "/v1/team/schedule/publish",
        params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
        json={"expected_version": 2, "acknowledge_conflicts": True},
        headers=team_org.headers,
    )
    assert publish.status_code == 200, publish.text

    requester = employees[0][1]
    recipient = employees[1][1]
    trade_body = {"offered_shift_id": offered["id"], "requested_shift_id": requested["id"]}
    created = await requester.client.post(
        "/v1/me/shift-trades", json=trade_body, headers=requester.headers
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "pending_recipient"
    forged = await requester.client.post(
        f"/v1/me/shift-trades/{created.json()['id']}/respond",
        json={"decision": "accept"},
        headers=requester.headers,
    )
    assert forged.status_code == 404
    cancelled = await requester.client.post(
        f"/v1/me/shift-trades/{created.json()['id']}/cancel", headers=requester.headers
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    created = await requester.client.post(
        "/v1/me/shift-trades", json=trade_body, headers=requester.headers
    )
    assert created.status_code == 201, created.text
    declined = await recipient.client.post(
        f"/v1/me/shift-trades/{created.json()['id']}/respond",
        json={"decision": "decline", "note": "Already committed"},
        headers=recipient.headers,
    )
    assert declined.status_code == 200 and declined.json()["status"] == "denied"
    created = await requester.client.post(
        "/v1/me/shift-trades", json=trade_body, headers=requester.headers
    )
    assert created.status_code == 201, created.text
    accepted = await recipient.client.post(
        f"/v1/me/shift-trades/{created.json()['id']}/respond",
        json={"decision": "accept"},
        headers=recipient.headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "pending_manager"

    inbox = await team_org.client.get(
        "/v1/team/shift-trades", params={"location_id": str(team_org.location_id)}
    )
    assert [r["id"] for r in inbox.json()["data"]] == [created.json()["id"]]
    approved = await team_org.client.post(
        f"/v1/team/shift-trades/{created.json()['id']}/decide",
        json={"decision": "approve", "note": "Coverage confirmed"},
        headers=team_org.headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert ("shift_trade_request", "shift_trade.approved") in await audit_actions(team_org)

    # Manager approval changes the draft only, then normal publish makes it visible.
    draft = await team_org.client.get(
        "/v1/team/schedule",
        params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
    )
    by_id = {row["id"]: row for row in draft.json()["shifts"]}
    assert by_id[offered["id"]]["employee_id"] == employees[1][0]
    assert by_id[requested["id"]]["employee_id"] == employees[0][0]
    own_before_publish = await requester.client.get(
        "/v1/me/week", params={"week_start": WEEK.isoformat()}
    )
    assert [s["id"] for s in own_before_publish.json()["my_shifts"]] == [offered["id"]]
    republish = await team_org.client.post(
        "/v1/team/schedule/publish",
        params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
        json={
            "expected_version": draft.json()["schedule"]["version"],
            "acknowledge_conflicts": True,
        },
        headers=team_org.headers,
    )
    assert republish.status_code == 200, republish.text
    own_after_publish = await requester.client.get(
        "/v1/me/week", params={"week_start": WEEK.isoformat()}
    )
    assert [s["id"] for s in own_after_publish.json()["my_shifts"]] == [requested["id"]]
    saved_trade = await requester.client.get("/v1/me/shift-trades")
    assert any(
        row["id"] == created.json()["id"] and row["status"] == "approved"
        for row in saved_trade.json()["data"]
    )
