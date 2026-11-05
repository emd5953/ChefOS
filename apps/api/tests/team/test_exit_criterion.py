"""Phase 3 exit criterion on seeded data (spec "Happy path"): a GM builds and publishes a week
against forecast, the employee sees it, and time off flows through approval."""

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.business_date import business_date
from app.cli import DEMO_PASSWORD
from app.main import app
from app.team import demo
from tests.team.conftest import Connected


async def _login(email: str) -> tuple[httpx.AsyncClient, dict[str, str]]:
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    r = await c.post("/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return c, {"X-CSRF-Token": r.json()["csrf_token"]}


async def test_happy_path(team_org: Connected, monkeypatch: pytest.MonkeyPatch) -> None:
    gm_email, maya_email = (f"{p}-{uuid.uuid4().hex[:6]}@chefos.local" for p in ("gm", "maya"))
    monkeypatch.setattr(demo, "GM_EMAIL", gm_email)
    monkeypatch.setattr(demo, "EMPLOYEE_EMAIL", maya_email)
    owner = uuid.UUID(team_org.account["user"]["id"])
    await demo.seed_logins(team_org.org_id, owner, DEMO_PASSWORD)
    assert await demo.seed_week(team_org.org_id, owner) > 0

    gm, gh = await _login(gm_email)
    maya, mh = await _login(maya_email)
    loc = str(team_org.location_id)
    today = business_date(datetime.now(UTC), "America/New_York")
    this_week = today - timedelta(days=today.weekday())
    nxt = this_week + timedelta(days=7)
    try:
        # Maya sees this week's seeded, published schedule on her phone view.
        week = (await maya.get("/v1/me/week")).json()
        assert week["week_start"] == this_week.isoformat() and week["published_at"]

        # The GM copies this week into next week and publishes it.
        params = {"location_id": loc, "week_start": nxt.isoformat()}
        r = await gm.post(
            "/v1/team/schedule/copy",
            params=params,
            json={"from_week_start": this_week.isoformat()},
            headers=gh,
        )
        assert r.status_code == 200 and r.json()["copied"] > 0
        sched = (await gm.get("/v1/team/schedule", params=params)).json()
        assert sched["week"]["scheduled_hours"] > 0 and sched["schedule"]["unpublished_changes"] > 0
        r = await gm.post(
            "/v1/team/schedule/publish",
            params=params,
            json={"expected_version": sched["schedule"]["version"], "acknowledge_conflicts": True},
            headers=gh,
        )
        assert r.status_code == 200 and r.json()["published_version"] == 1

        mine = (await maya.get("/v1/me/week", params={"week_start": nxt.isoformat()})).json()[
            "my_shifts"
        ]
        assert mine, "Maya works next week in the simulator's pattern"
        alerts = (await maya.get("/v1/notifications")).json()["data"]
        assert alerts[0]["kind"] == "schedule_published"

        # Maya asks for one of those days off; the GM sees the shift inline and approves.
        day = date.fromisoformat(mine[0]["business_date"])
        r = await maya.post(
            "/v1/me/time-off",
            json={"starts_on": day.isoformat(), "ends_on": day.isoformat()},
            headers=mh,
        )
        assert r.status_code == 201, r.text
        req = r.json()
        inbox = (await gm.get("/v1/team/requests", params={"location_id": loc})).json()["time_off"]
        item = next(t for t in inbox if t["id"] == req["id"])
        assert mine[0]["id"] in [s["id"] for s in item["overlapping_shifts"]]
        r = await gm.post(
            f"/v1/team/time-off/{req['id']}/decide", json={"decision": "approve"}, headers=gh
        )
        assert r.json()["status"] == "approved"
        assert (await maya.get("/v1/notifications")).json()["data"][0]["kind"] == "time_off_decided"

        # The builder flags her shift; the GM reassigns it and republishes.
        sched = (await gm.get("/v1/team/schedule", params=params)).json()
        assert "time_off_approved" in {
            c["kind"] for c in sched["conflicts"] if c["shift_id"] == mine[0]["id"]
        }
        shift = next(s for s in sched["shifts"] if s["id"] == mine[0]["id"])
        free = next(
            e for e in sched["employees"]
            if e["active"] and e["id"] != shift["employee_id"]
            and not any(s["employee_id"] == e["id"] and s["starts_at"] < shift["ends_at"] and s["ends_at"] > shift["starts_at"] for s in sched["shifts"])
        )  # fmt: skip
        r = await gm.patch(
            f"/v1/team/shifts/{shift['id']}",
            json={"employee_id": free["id"], "expected_updated_at": shift["updated_at"]},
            headers=gh,
        )
        assert r.status_code == 200, r.text
        version = (await gm.get("/v1/team/schedule", params=params)).json()["schedule"]["version"]
        r = await gm.post(
            "/v1/team/schedule/publish",
            params=params,
            json={"expected_version": version, "acknowledge_conflicts": True},
            headers=gh,
        )
        out = r.json()
        # Only Maya and the new assignee changed; Maya is the only one with a login.
        assert sorted(out["changed_employees"]) == sorted([shift["employee_id"], free["id"]])
        assert out["notified_users"] == 1
        assert (await maya.get("/v1/notifications")).json()["data"][0]["kind"] == "schedule_changed"
    finally:
        await gm.aclose()
        await maya.aclose()
