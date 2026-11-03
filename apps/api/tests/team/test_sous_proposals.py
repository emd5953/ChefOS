"""Sous schedule proposals persist separately and require an explicit manager decision."""

from datetime import date

from tests.team.conftest import Clock, Connected, at, positions

WEEK = date(2026, 3, 9)


async def _create(conn: Connected, position_id: str) -> dict:
    response = await conn.client.post(
        "/v1/sous/proposals",
        json={
            "location_id": str(conn.location_id),
            "week_start": WEEK.isoformat(),
            "shift": {
                "position_id": position_id,
                "starts_at": at(WEEK, 10),
                "ends_at": at(WEEK, 16),
                "notes": "Proposed by Sous",
            },
        },
        headers=conn.headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_proposal_is_draft_until_approved_and_requires_publish(
    team_org: Connected, frozen: Clock
) -> None:
    position_id = (await positions(team_org))["Server"]
    proposal = await _create(team_org, position_id)
    assert proposal["status"] == "pending"
    schedule = await team_org.client.get(
        "/v1/team/schedule",
        params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
    )
    assert schedule.status_code == 200
    assert schedule.json()["schedule"]["version"] == proposal["expected_version"]
    decision = await team_org.client.post(
        f"/v1/sous/proposals/{proposal['id']}/decision",
        json={"approve": True},
        headers=team_org.headers,
    )
    assert decision.status_code == 200 and decision.json()["status"] == "approved"
    schedule = await team_org.client.get(
        "/v1/team/schedule",
        params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
    )
    assert schedule.json()["schedule"]["version"] == proposal["expected_version"] + 1
    assert schedule.json()["schedule"]["published_version"] is None


async def test_proposal_becomes_stale_if_schedule_changed(
    team_org: Connected, frozen: Clock
) -> None:
    position_id = (await positions(team_org))["Server"]
    proposal = await _create(team_org, position_id)
    edit = await team_org.client.post(
        "/v1/team/schedule/shifts",
        params={"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()},
        json={"position_id": position_id, "starts_at": at(WEEK, 18), "ends_at": at(WEEK, 22)},
        headers=team_org.headers,
    )
    assert edit.status_code == 201, edit.text
    decision = await team_org.client.post(
        f"/v1/sous/proposals/{proposal['id']}/decision",
        json={"approve": True},
        headers=team_org.headers,
    )
    assert decision.status_code == 200 and decision.json()["status"] == "stale"
