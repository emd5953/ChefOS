"""Waste posting follows the inventory manager role policy."""

import uuid

from tests.team.conftest import AddMember, Connected


async def test_employee_cannot_post_waste(team_org: Connected, add_member: AddMember) -> None:
    employee = await add_member(team_org.org_id, "employee", [team_org.location_id])
    response = await employee.client.post(
        "/v1/inventory/waste",
        json={
            "location_id": str(team_org.location_id),
            "item_id": str(uuid.uuid4()),
            "quantity": 1,
            "reason": "Spill",
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=employee.headers,
    )
    assert response.status_code == 403
