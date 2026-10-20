"""A second org must not see, change, or write the first org's Team data."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import scoped_session
from tests.team.conftest import Clock, Connect, Connected, import_team

TABLES = ["staff_members", "positions", "employee_positions", "stations", "employee_locations"]


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
    return a, b


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
