"""Open shift claims update the manager draft and become visible only after publish."""

from datetime import date, timedelta

from tests.team.conftest import (
    AddMember,
    Clock,
    Connected,
    at,
    audit_actions,
    employee_id,
    positions,
)

WEEK = date(2026, 3, 9)


async def test_open_shift_claim_requires_manager_approval_and_publish(
    team_org: Connected, add_member: AddMember, frozen: Clock
) -> None:
    pos = await positions(team_org)
    maya_id = await employee_id(team_org, "Maya Okafor")
    maya = await add_member(team_org.org_id, "employee", [team_org.location_id], employee=maya_id)
    friday = WEEK + timedelta(days=4)
    shift = await team_org.client.post(
        "/v1/team/schedule/shifts",
        params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
        json={"position_id": pos["Server"], "starts_at": at(friday, 16), "ends_at": at(friday, 22)},
        headers=team_org.headers,
    )
    assert shift.status_code == 201, shift.text
    publish = await team_org.client.post(
        "/v1/team/schedule/publish",
        params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
        json={"expected_version": 1, "acknowledge_conflicts": True},
        headers=team_org.headers,
    )
    assert publish.status_code == 200, publish.text

    open_shifts = await maya.client.get("/v1/me/open-shifts")
    assert open_shifts.status_code == 200, open_shifts.text
    assert [x["id"] for x in open_shifts.json()["data"]] == [shift.json()["id"]]
    request = await maya.client.post(
        "/v1/me/shift-coverage-requests",
        json={"published_shift_id": shift.json()["id"]},
        headers=maya.headers,
    )
    assert request.status_code == 201, request.text
    assert request.json()["status"] == "pending"
    duplicate = await maya.client.post(
        "/v1/me/shift-coverage-requests",
        json={"published_shift_id": shift.json()["id"]},
        headers=maya.headers,
    )
    assert duplicate.status_code == 409
    cancelled = await maya.client.post(
        f"/v1/me/shift-coverage-requests/{request.json()['id']}/cancel",
        headers=maya.headers,
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    request = await maya.client.post(
        "/v1/me/shift-coverage-requests",
        json={"published_shift_id": shift.json()["id"]},
        headers=maya.headers,
    )
    assert request.status_code == 201, request.text

    manager_inbox = await team_org.client.get(
        "/v1/team/shift-coverage-requests", params={"location_id": str(team_org.location_id)}
    )
    assert [x["id"] for x in manager_inbox.json()["data"]] == [request.json()["id"]]
    decide = await team_org.client.post(
        f"/v1/team/shift-coverage-requests/{request.json()['id']}/decide",
        json={"decision": "approve", "note": "Approved"},
        headers=team_org.headers,
    )
    assert decide.status_code == 200, decide.text
    assert decide.json()["status"] == "approved"
    assert ("shift_coverage_request", "shift_coverage.approved") in await audit_actions(team_org)
    alerts = (await maya.client.get("/v1/notifications")).json()["data"]
    assert any(alert["kind"] == "shift_coverage_decided" for alert in alerts)

    # Approval modifies the draft only; the employee sees the shift after manager republish.
    week = await maya.client.get("/v1/me/week", params={"week_start": WEEK.isoformat()})
    assert week.json()["my_shifts"] == []
    draft = await team_org.client.get(
        "/v1/team/schedule",
        params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
    )
    assert draft.json()["shifts"][0]["employee_id"] == str(maya_id)
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
    week = await maya.client.get("/v1/me/week", params={"week_start": WEEK.isoformat()})
    assert [x["id"] for x in week.json()["my_shifts"]] == [shift.json()["id"]]
