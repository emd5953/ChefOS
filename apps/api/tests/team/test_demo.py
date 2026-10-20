"""The demo seed creates working manager and employee logins, idempotently."""

import uuid

import pytest
from sqlalchemy import text

from app.cli import DEMO_PASSWORD
from app.db import scoped_session
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


async def test_seed_week_publishes_the_simulated_week(team_org: Connected) -> None:
    owner = uuid.UUID(team_org.account["user"]["id"])
    created = await demo.seed_week(team_org.org_id, owner)
    assert created > 40  # a week of the simulator's crew
    assert await demo.seed_week(team_org.org_id, owner) == 0
    r = await team_org.client.get("/v1/notifications")
    assert r.status_code == 200
    async with scoped_session(team_org.org_id) as db:
        published = await db.scalar(text("SELECT count(*) FROM published_shifts"))
        unassigned = await db.scalar(
            text("SELECT count(*) FROM published_shifts WHERE employee_id IS NULL")
        )
    assert published == created and unassigned < created
