"""Invitations: single use, expiry, revoke, hashing, throttle, atomic accept, roles."""

import hashlib
import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import scoped_session
from app.main import app
from app.security import throttle
from tests.team.conftest import AddMember, Connected, audit_actions, by_name, employee_id

NEW_PASSWORD = "plum-orchard-lantern-7"


def _anon() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _token(created: dict[str, Any]) -> str:
    return str(created["accept_url"]).rsplit("/invite/", 1)[1]


async def _invite(conn: Connected, **body: Any) -> httpx.Response:
    body.setdefault("location_ids", [str(conn.location_id)])
    return await conn.client.post("/v1/team/invitations", json=body, headers=conn.headers)


async def test_invite_accept_links_employee_atomically(team_org: Connected) -> None:
    maya = await employee_id(team_org, "Maya Okafor")
    r = await _invite(team_org, employee_id=str(maya))
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["invitation"]["email"] == "maya.okafor@juniperhouse.example"
    assert created["invitation"]["status"] == "pending"
    assert created["accept_url"].startswith("http://localhost:3000/invite/")
    token = _token(created)
    assert (
        by_name((await team_org.client.get("/v1/team/employees")).json()["data"])["Maya Okafor"][
            "login"
        ]
        == "invited"
    )
    async with scoped_session(team_org.org_id) as db:
        stored = await db.scalar(text("SELECT token_hash FROM invitations"))
    assert stored == hashlib.sha256(token.encode()).hexdigest()  # hashed at rest

    async with _anon() as anon:
        preview = (await anon.get(f"/v1/invitations/{token}")).json()
        assert (preview["org_name"], preview["role"], preview["employee_name"]) == (
            "Test Org",
            "employee",
            "Maya Okafor",
        )
        r = await anon.post(
            f"/v1/invitations/{token}/accept",
            json={"display_name": "Maya", "password": NEW_PASSWORD},
        )
        assert r.status_code == 201, r.text
        assert r.json()["role"] == "employee" and "chefos_session" in r.cookies
        me = (await anon.get("/v1/auth/me")).json()
        assert me["user"]["email"] == "maya.okafor@juniperhouse.example"
        # Single use.
        r = await anon.post(
            f"/v1/invitations/{token}/accept", json={"display_name": "M", "password": NEW_PASSWORD}
        )
        assert (r.status_code, r.json()["code"]) == (410, "invitation_invalid")
        assert (await anon.get(f"/v1/invitations/{token}")).status_code == 410
        # The new login works and is an employee: manager APIs are forbidden.
        assert (await anon.get("/v1/team/employees")).status_code == 403
    async with scoped_session(team_org.org_id) as db:
        row = (
            await db.execute(
                text(
                    "SELECT s.user_id = m.user_id AS linked, m.role, "
                    "(SELECT count(*) FROM location_access a WHERE a.user_id = m.user_id) AS access "
                    "FROM staff_members s JOIN memberships m ON m.user_id = s.user_id WHERE s.id = :e"
                ),
                {"e": maya},
            )
        ).one()
    assert tuple(row) == (True, "employee", 1)
    assert (
        by_name((await team_org.client.get("/v1/team/employees")).json()["data"])["Maya Okafor"][
            "login"
        ]
        == "active"
    )
    actions = await audit_actions(team_org)
    assert ("invitation", "invitation.created") in actions and (
        "invitation",
        "invitation.accepted",
    ) in actions


async def test_revoke_expiry_and_replacement(
    team_org: Connected, owner_engine: AsyncEngine
) -> None:
    diego = await employee_id(team_org, "Diego Marsh")
    first = (await _invite(team_org, employee_id=str(diego))).json()
    second = (await _invite(team_org, employee_id=str(diego))).json()
    async with _anon() as anon:
        assert (await anon.get(f"/v1/invitations/{_token(first)}")).status_code == 410  # replaced
        r = await team_org.client.post(
            f"/v1/team/invitations/{second['invitation']['id']}/revoke", headers=team_org.headers
        )
        assert r.json()["status"] == "revoked"
        assert (await anon.get(f"/v1/invitations/{_token(second)}")).status_code == 410
        third = (await _invite(team_org, employee_id=str(diego))).json()
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE invitations SET expires_at = now() - interval '1 second' WHERE id = :i"
                ),
                {"i": third["invitation"]["id"]},
            )
        r = await anon.post(
            f"/v1/invitations/{_token(third)}/accept",
            json={"display_name": "D", "password": NEW_PASSWORD},
        )
        assert r.status_code == 410
    listed = (await team_org.client.get("/v1/team/invitations")).json()["data"]
    assert sorted(i["status"] for i in listed) == ["expired", "revoked", "revoked"]
    pending = (
        await team_org.client.get("/v1/team/invitations", params={"status": "pending"})
    ).json()
    assert pending["data"] == []


async def test_existing_account_and_weak_password(team_org: Connected) -> None:
    r = await _invite(team_org, role="manager", email=team_org.account["payload"]["email"])
    token = _token(r.json())
    async with _anon() as anon:
        r = await anon.post(
            f"/v1/invitations/{token}/accept", json={"display_name": "X", "password": "short"}
        )
        assert r.status_code == 422 and "password" in r.json()["errors"]
        r = await anon.post(
            f"/v1/invitations/{token}/accept", json={"display_name": "X", "password": NEW_PASSWORD}
        )
        assert (r.status_code, r.json()["code"]) == (409, "email_has_account")


async def test_accept_is_throttled_per_ip(team_org: Connected) -> None:
    async with _anon() as anon:
        for _ in range(throttle.IP_LIMIT):
            r = await anon.post(
                f"/v1/invitations/bogus-{uuid.uuid4().hex}/accept",
                json={"display_name": "X", "password": NEW_PASSWORD},
            )
            assert r.status_code == 410
        r = await anon.post(
            "/v1/invitations/bogus/accept", json={"display_name": "X", "password": NEW_PASSWORD}
        )
        assert r.status_code == 429


async def test_invite_rules(team_org: Connected, add_member: AddMember) -> None:
    maya = await employee_id(team_org, "Maya Okafor")
    gm = await add_member(team_org.org_id, "manager", [team_org.location_id])
    # Managers invite employees only.
    r = await gm.client.post(
        "/v1/team/invitations",
        json={
            "role": "manager",
            "email": "x@example.com",
            "location_ids": [str(team_org.location_id)],
        },
        headers=gm.headers,
    )
    assert r.status_code == 403
    r = await gm.client.post(
        "/v1/team/invitations",
        json={"employee_id": str(maya), "location_ids": [str(team_org.location_id)]},
        headers=gm.headers,
    )
    assert r.status_code == 201
    # No email anywhere, an outside location, an employee with a login.
    r = await _invite(team_org)
    assert r.status_code == 422 and "email" in r.json()["errors"]
    r = await _invite(team_org, email="a@example.com", location_ids=[str(uuid.uuid4())])
    assert r.status_code == 422
    linked = await employee_id(team_org, "Diego Marsh")
    await add_member(team_org.org_id, "employee", [team_org.location_id], employee=linked)
    r = await _invite(team_org, employee_id=str(linked))
    assert r.status_code == 409


async def test_manager_invite_without_employee(team_org: Connected) -> None:
    r = await _invite(team_org, role="manager", email="gm@example.com")
    async with _anon() as anon:
        r = await anon.post(
            f"/v1/invitations/{_token(r.json())}/accept",
            json={"display_name": "Jordan", "password": NEW_PASSWORD},
        )
        assert r.status_code == 201 and r.json()["role"] == "manager"
        rows = (await anon.get("/v1/team/employees")).json()["data"]
        assert len(rows) == 18  # sees the location they were given
        login = await anon.post(
            "/v1/auth/login", json={"email": "gm@example.com", "password": NEW_PASSWORD}
        )
        assert login.status_code == 200
