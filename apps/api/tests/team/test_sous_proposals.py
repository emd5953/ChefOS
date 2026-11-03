"""Sous schedule proposals persist separately and require an explicit manager decision."""

from datetime import date, datetime

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


async def test_update_and_remove_proposals_change_only_the_draft_until_publish(
    team_org: Connected, frozen: Clock
) -> None:
    position_id = (await positions(team_org))["Server"]
    params = {"location_id": str(team_org.location_id), "week_start": WEEK.isoformat()}
    created = await team_org.client.post(
        "/v1/team/schedule/shifts",
        params=params,
        json={"position_id": position_id, "starts_at": at(WEEK, 10), "ends_at": at(WEEK, 16)},
        headers=team_org.headers,
    )
    assert created.status_code == 201, created.text
    shift_id = created.json()["id"]
    published = await team_org.client.post(
        "/v1/team/schedule/publish",
        params=params,
        json={"expected_version": 1, "acknowledge_conflicts": True},
        headers=team_org.headers,
    )
    assert published.status_code == 200, published.text

    replacement = {
        "position_id": position_id,
        "starts_at": at(WEEK, 11),
        "ends_at": at(WEEK, 17),
        "notes": "Adjusted by Sous",
    }
    proposed = await team_org.client.post(
        "/v1/sous/proposals",
        json={
            "location_id": str(team_org.location_id),
            "action": "update",
            "target_shift_id": shift_id,
            "shift": replacement,
        },
        headers=team_org.headers,
    )
    assert proposed.status_code == 201, proposed.text
    draft = await team_org.client.get("/v1/team/schedule", params=params)
    assert datetime.fromisoformat(draft.json()["shifts"][0]["starts_at"]) == datetime.fromisoformat(
        at(WEEK, 10)
    )
    decision = await team_org.client.post(
        f"/v1/sous/proposals/{proposed.json()['id']}/decision",
        json={"approve": True},
        headers=team_org.headers,
    )
    assert decision.status_code == 200 and decision.json()["status"] == "approved"
    draft = await team_org.client.get("/v1/team/schedule", params=params)
    assert datetime.fromisoformat(draft.json()["shifts"][0]["starts_at"]) == datetime.fromisoformat(
        at(WEEK, 11)
    )
    assert draft.json()["schedule"]["published_version"] == 1

    published = await team_org.client.post(
        "/v1/team/schedule/publish",
        params=params,
        json={
            "expected_version": draft.json()["schedule"]["version"],
            "acknowledge_conflicts": True,
        },
        headers=team_org.headers,
    )
    assert published.status_code == 200, published.text
    draft = await team_org.client.get("/v1/team/schedule", params=params)
    removed = await team_org.client.post(
        "/v1/sous/proposals",
        json={
            "location_id": str(team_org.location_id),
            "action": "remove",
            "target_shift_id": shift_id,
        },
        headers=team_org.headers,
    )
    assert removed.status_code == 201, removed.text
    decision = await team_org.client.post(
        f"/v1/sous/proposals/{removed.json()['id']}/decision",
        json={"approve": True},
        headers=team_org.headers,
    )
    assert decision.status_code == 200 and decision.json()["status"] == "approved"
    draft = await team_org.client.get("/v1/team/schedule", params=params)
    assert draft.json()["shifts"] == []
    assert draft.json()["schedule"]["published_version"] == 2
    published = await team_org.client.post(
        "/v1/team/schedule/publish",
        params=params,
        json={
            "expected_version": draft.json()["schedule"]["version"],
            "acknowledge_conflicts": True,
        },
        headers=team_org.headers,
    )
    assert published.status_code == 200, published.text
    final = await team_org.client.get("/v1/team/schedule", params=params)
    assert final.json()["shifts"] == []
