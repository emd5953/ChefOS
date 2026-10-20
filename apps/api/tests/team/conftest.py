"""Fixtures for Team: the integrations fixtures (fake Square, clock) plus an org whose Square
team members are imported, and helpers that add manager/employee logins."""

# ruff: noqa: F811  (re-exported fixtures are used as arguments below)

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import scoped_session
from app.integrations import sync
from app.main import app
from app.security.passwords import hash_password
from tests.conftest import PASSWORD
from tests.integrations.conftest import (  # noqa: F401
    Clock,
    Connect,
    Connected,
    clock,
    connect,
    load_days,
    square,
)


@dataclass
class Member:
    user_id: uuid.UUID
    email: str
    client: httpx.AsyncClient
    headers: dict[str, str]


AddMember = Callable[..., Awaitable[Member]]


async def import_team(conn: Connected, clock: Clock) -> None:
    await sync.sync_connection(
        conn.org_id,
        conn.connection_id,
        streams=["locations", "team_members"],
        force=True,
        now=clock.now,
    )


@pytest_asyncio.fixture
async def team_org(connect: Connect, clock: Clock) -> Connected:
    """Juniper House connected, with its 18 Square team members imported."""
    conn = await connect()
    await import_team(conn, clock)
    return conn


async def employee_id(conn: Connected, name: str) -> uuid.UUID:
    async with scoped_session(conn.org_id) as db:
        found = await db.scalar(
            text("SELECT id FROM staff_members WHERE display_name = :n"), {"n": name}
        )
    assert found is not None, name
    return uuid.UUID(str(found))


@pytest_asyncio.fixture
async def add_member(owner_engine: AsyncEngine) -> AsyncIterator[AddMember]:
    """Insert a user with `role` (and location access) into an org, then log in as them."""
    opened: list[httpx.AsyncClient] = []
    password_hash = await hash_password(PASSWORD)

    async def _add(
        org_id: uuid.UUID,
        role: str,
        location_ids: list[uuid.UUID] | None = None,
        *,
        employee: uuid.UUID | None = None,
    ) -> Member:
        user_id = uuid.uuid4()
        email = f"{role}-{user_id.hex[:8]}@example.com"
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, display_name) "
                    "VALUES (:i, :e, :p, :n)"
                ),
                {"i": user_id, "e": email, "p": password_hash, "n": role.title()},
            )
            await conn.execute(
                text("INSERT INTO memberships (id, org_id, user_id, role) VALUES (:i, :o, :u, :r)"),
                {"i": uuid.uuid4(), "o": org_id, "u": user_id, "r": role},
            )
            for loc in location_ids or []:
                await conn.execute(
                    text(
                        "INSERT INTO location_access (org_id, user_id, location_id) "
                        "VALUES (:o, :u, :l)"
                    ),
                    {"o": org_id, "u": user_id, "l": loc},
                )
            if employee is not None:
                await conn.execute(
                    text("UPDATE staff_members SET user_id = :u WHERE id = :e"),
                    {"u": user_id, "e": employee},
                )
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        opened.append(c)
        r = await c.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return Member(user_id, email, c, {"X-CSRF-Token": r.json()["csrf_token"]})

    yield _add
    for c in opened:
        await c.aclose()


async def audit_actions(conn: Connected) -> list[tuple[str, str]]:
    async with scoped_session(conn.org_id) as db:
        rows = await db.execute(
            text("SELECT entity_type, action FROM audit_events ORDER BY created_at, id")
        )
        return [(r.entity_type, r.action) for r in rows]


def by_name(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["display_name"]: r for r in rows}
