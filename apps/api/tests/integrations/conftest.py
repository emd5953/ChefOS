"""Fixtures for the integration layer: a fake Square seller on a controllable clock, and
connected orgs. Temporal is never contacted: workflow starts are stubbed or run in the
time-skipping test environment."""

import uuid
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio

from app.db import scoped_session
from app.integrations import providers, service, sync
from app.integrations.models import IntegrationConnection
from app.integrations.providers.square.connector import SquareConnector
from app.integrations.providers.square.fake_client import FakeSquareClient
from tests.conftest import SignupFn

# Saturday 15:00 EST (before the March 8 DST change): the restaurant is open.
T0 = datetime(2026, 3, 7, 20, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@dataclass
class Connected:
    org_id: uuid.UUID
    user_id: uuid.UUID
    location_id: uuid.UUID
    connection_id: uuid.UUID
    client: httpx.AsyncClient
    headers: dict[str, str]
    account: dict[str, Any]


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture(autouse=True)
def square(clock: Clock, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSquareClient]:
    """The fake seller, wired as the process-wide connector for every integration test."""
    client = FakeSquareClient(clock=clock)
    providers.set_connector_override(SquareConnector(client, mode="fake"))

    async def no_kickoff(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(service, "kick_off", no_kickoff)
    monkeypatch.setattr(service.temporal, "delete_schedules", no_kickoff)
    monkeypatch.setattr(service.temporal, "terminate_backfill", no_kickoff)
    yield client
    providers.set_connector_override(None)


Connect = Callable[..., Coroutine[Any, Any, Connected]]


@pytest_asyncio.fixture
async def connect(signup: SignupFn) -> AsyncIterator[Connect]:
    opened: list[httpx.AsyncClient] = []

    async def _connect(
        *, complete: bool = True, extra_locations: int = 0, location_name: str = "Juniper House"
    ) -> Connected:
        acct = await signup(location_name=location_name)
        c: httpx.AsyncClient = acct["client"]
        opened.append(c)
        org_id, user_id = uuid.UUID(acct["org"]["id"]), uuid.UUID(acct["user"]["id"])
        for i in range(extra_locations):
            r = await c.post(
                "/v1/locations",
                json={"name": f"Extra {i}", "timezone": "America/New_York"},
                headers=acct["headers"],
            )
            assert r.status_code == 201, r.text
        async with scoped_session(org_id) as db:
            conn = IntegrationConnection(
                org_id=org_id, provider="square", mode="fake", created_by=user_id
            )
            db.add(conn)
            await db.flush()
            connection_id = conn.id
        if complete:
            await service.complete_oauth(
                service.sign_state(org_id, connection_id, user_id), "fake-code"
            )
        return Connected(
            org_id=org_id,
            user_id=user_id,
            location_id=uuid.UUID(acct["location"]["id"]),
            connection_id=connection_id,
            client=c,
            headers=acct["headers"],
            account=acct,
        )

    yield _connect
    for c in opened:
        await c.aclose()


async def load_days(conn: Connected, clock: Clock, first: date, last: date) -> None:
    """Reference streams, then the backfill chunks for [first, last] (newest first)."""
    await sync.sync_connection(
        conn.org_id,
        conn.connection_id,
        streams=["locations", "catalog", "team_members"],
        force=True,
        now=clock.now,
    )
    await sync.begin_backfill(
        conn.org_id, conn.connection_id, first, last, f"test-backfill-{conn.connection_id}"
    )
    d = last
    while d >= first:
        await sync.backfill_chunk(
            conn.org_id,
            conn.connection_id,
            d,
            d,
            f"test-backfill-{conn.connection_id}",
            now=clock.now,
        )
        d -= timedelta(days=1)
