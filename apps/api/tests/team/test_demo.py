"""The demo seed creates working manager and employee logins, idempotently."""

import uuid

import pytest

from app.cli import DEMO_PASSWORD
from app.team import demo
from tests.team.conftest import Connected


async def test_seed_logins(team_org: Connected, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = uuid.UUID(team_org.account["user"]["id"])
    gm, maya = (f"{p}-{uuid.uuid4().hex[:6]}@chefos.local" for p in ("gm", "maya"))
    monkeypatch.setattr(demo, "GM_EMAIL", gm)  # unique per run: users are global
    monkeypatch.setattr(demo, "EMPLOYEE_EMAIL", maya)
    assert await demo.seed_logins(team_org.org_id, owner, DEMO_PASSWORD) == [gm, maya]
    assert await demo.seed_logins(team_org.org_id, owner, DEMO_PASSWORD) == []
    c = team_org.client
    r = await c.post("/v1/auth/login", json={"email": maya, "password": DEMO_PASSWORD})
    assert r.json()["role"] == "employee"
    r = await c.post("/v1/auth/login", json={"email": gm, "password": DEMO_PASSWORD})
    assert r.json()["role"] == "manager"
