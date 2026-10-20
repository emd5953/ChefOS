"""A second org must not see, change, or write the first org's Team data."""

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import get_engine, lookup_invitation, scoped_session
from tests.team.conftest import Clock, Connect, Connected, at, import_team

TABLES = [
    "staff_members", "positions", "employee_positions", "stations", "employee_locations",
    "invitations", "schedules", "shifts", "published_shifts", "notifications",
    "availability_sets", "availability_blocks", "time_off_requests",
]  # fmt: skip


@pytest.fixture
async def two(connect: Connect, clock: Clock) -> tuple[Connected, Connected]:
    a, b = await connect(), await connect()
    for c in (a, b):
        await import_team(c, clock)
        r = await c.client.post(
            "/v1/team/stations",
            json={"location_id": str(c.location_id), "name": "Grill"},
            headers=c.headers,
        )
        assert r.status_code == 201, r.text
        r = await c.client.post(
            "/v1/team/invitations",
            json={
                "role": "manager",
                "email": "x@example.com",
                "location_ids": [str(c.location_id)],
            },
            headers=c.headers,
        )
        assert r.status_code == 201, r.text
        pos = (await c.client.get("/v1/team/positions")).json()["data"][0]["id"]
        monday = date(2026, 3, 9)
        r = await c.client.post(
            "/v1/team/schedule/shifts",
            params={"location_id": str(c.location_id), "week_start": monday.isoformat()},
            json={"position_id": pos, "starts_at": at(monday, 9), "ends_at": at(monday, 15)},
            headers=c.headers,
        )
        assert r.status_code == 201, r.text
        r = await c.client.post(
            "/v1/team/schedule/publish",
            params={"location_id": str(c.location_id), "week_start": monday.isoformat()},
            json={"expected_version": 1, "acknowledge_conflicts": True},
            headers=c.headers,
        )
        assert r.status_code == 200, r.text
        async with scoped_session(c.org_id) as db:
            await db.execute(
                text(
                    "INSERT INTO notifications (id, org_id, user_id, kind, title) "
                    "VALUES (:i, :o, :u, 'schedule_changed', 't')"
                ),
                {"i": uuid.uuid4(), "o": c.org_id, "u": c.user_id},
            )
            emp = await db.scalar(text("SELECT id FROM staff_members LIMIT 1"))
            p = {"o": c.org_id, "u": c.user_id, "e": emp, "s": uuid.uuid4()}
            await db.execute(
                text(
                    "INSERT INTO time_off_requests (id, org_id, employee_id, starts_on, ends_on, "
                    "created_by) VALUES (gen_random_uuid(), :o, :e, '2026-03-14', '2026-03-14', :u)"
                ),
                p,
            )
            await db.execute(
                text(
                    "INSERT INTO availability_sets (id, org_id, employee_id, effective_from, "
                    "submitted_by) VALUES (:s, :o, :e, '2026-03-09', :u)"
                ),
                p,
            )
            await db.execute(
                text(
                    "INSERT INTO availability_blocks (id, org_id, set_id, weekday) "
                    "VALUES (gen_random_uuid(), :o, :s, 5)"
                ),
                p,
            )
    return a, b


async def test_invitation_lookup_returns_only_ids(two: tuple[Connected, Connected]) -> None:
    a, _ = two
    assert await lookup_invitation("0" * 64) is None
    async with scoped_session(a.org_id) as db:
        token_hash = await db.scalar(text("SELECT token_hash FROM invitations"))
    found = await lookup_invitation(token_hash)
    assert found is not None and found[1] == a.org_id
    async with get_engine().connect() as conn:
        cols = (
            await conn.execute(text("SELECT * FROM invitation_for_token(:h)"), {"h": token_hash})
        ).keys()
    assert list(cols) == ["invitation_id", "org_id"]


async def test_other_org_sees_nothing_and_changes_nothing(two: tuple[Connected, Connected]) -> None:
    a, b = two
    for table in TABLES:
        async with scoped_session(a.org_id) as db:
            assert await db.scalar(text(f"SELECT count(*) FROM {table}")) >= 1, table
        async with scoped_session(b.org_id) as db:
            p = {"o": a.org_id}
            assert await db.scalar(text(f"SELECT count(*) FROM {table} WHERE org_id = :o"), p) == 0
            assert (
                await db.execute(text(f"UPDATE {table} SET org_id = org_id WHERE org_id = :o"), p)
            ).rowcount == 0
            assert (
                await db.execute(text(f"DELETE FROM {table} WHERE org_id = :o"), p)
            ).rowcount == 0
        async with scoped_session(a.org_id) as db:
            assert await db.scalar(text(f"SELECT count(*) FROM {table}")) >= 1, table


async def test_cannot_write_into_another_org(two: tuple[Connected, Connected]) -> None:
    a, b = two
    with pytest.raises(DBAPIError, match="row-level security"):
        async with scoped_session(b.org_id) as db:
            await db.execute(
                text(
                    "INSERT INTO positions (id, org_id, name, color) VALUES (:i, :o, 'x', '#000000')"
                ),
                {"i": uuid.uuid4(), "o": a.org_id},
            )
