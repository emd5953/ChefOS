"""Role matrix, enumerated from the router so new routes can't skip the staff check."""

import uuid

from app.main import app
from tests.team.conftest import AddMember, Connected

STAFF_ONLY_PREFIXES = ("/v1/team/", "/v1/commerce/", "/v1/sous/", "/v1/integrations/")
# Integration routes authenticated another way (signed OAuth state, HMAC).
UNAUTHENTICATED = {
    "/v1/integrations/square/fake-consent",
    "/v1/integrations/square/callback",
}


def _staff_routes() -> list[tuple[str, str]]:
    out = []
    for path, item in app.openapi()["paths"].items():
        if path.startswith(STAFF_ONLY_PREFIXES) and path not in UNAUTHENTICATED:
            out.extend((method.upper(), path) for method in item)
    return sorted(out)


async def test_employee_gets_403_on_every_staff_route(
    team_org: Connected, add_member: AddMember
) -> None:
    emp = await add_member(team_org.org_id, "employee", [team_org.location_id])
    routes = _staff_routes()
    assert ("GET", "/v1/commerce/daily") in routes and ("POST", "/v1/sous/ask") in routes
    assert ("POST", "/v1/team/invitations") in routes
    for method, path in routes:
        url = path.replace("{id}", str(uuid.uuid4())).replace("{connection_id}", str(uuid.uuid4()))
        url = url.replace("{issue_id}", str(uuid.uuid4()))
        r = await emp.client.request(method, url, json={}, headers=emp.headers)
        assert r.status_code == 403, (method, path, r.status_code)
        assert r.json()["code"] == "forbidden"


async def test_manager_and_owner_pass_the_staff_check(
    team_org: Connected, add_member: AddMember
) -> None:
    gm = await add_member(team_org.org_id, "manager", [team_org.location_id])
    params = {"location_id": str(team_org.location_id), "from": "2026-03-01", "to": "2026-03-02"}
    assert (await gm.client.get("/v1/commerce/daily", params=params)).status_code == 200
    assert (await team_org.client.get("/v1/commerce/daily", params=params)).status_code == 200
    # Managers can't use owner/admin integration actions.
    r = await gm.client.post("/v1/integrations/square/authorize", headers=gm.headers)
    assert r.status_code == 403


async def test_employee_keeps_auth_and_locations(
    team_org: Connected, add_member: AddMember
) -> None:
    emp = await add_member(team_org.org_id, "employee", [team_org.location_id])
    assert (await emp.client.get("/v1/auth/me")).json()["role"] == "employee"
    r = await emp.client.post(
        "/v1/locations", json={"name": "X", "timezone": "UTC"}, headers=emp.headers
    )
    assert r.status_code == 403
