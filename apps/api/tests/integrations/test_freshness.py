"""Freshness transitions with an injected clock: fresh -> stale -> failing -> fresh."""

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import scoped_session
from app.integrations import sync
from app.integrations.freshness import location_freshness
from app.integrations.models import (
    IntegrationConnection,
    IntegrationLocationLink,
    IntegrationStream,
)
from app.integrations.providers.square.client import SquareAuthError, SquareTransientError
from app.integrations.providers.square.fake_client import FakeSquareClient
from tests.integrations.conftest import Clock, Connect


async def statuses(org_id: uuid.UUID, location_id: uuid.UUID, now) -> dict[str, str]:  # type: ignore[no-untyped-def]
    async with scoped_session(org_id) as db:
        return {f.stream: f.status for f in await location_freshness(db, location_id, now)}


async def test_fresh_then_stale_then_failing_then_recovered(
    connect: Connect, clock: Clock, square: FakeSquareClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert get_settings().sync_interval_seconds == 60
    monkeypatch.setattr(sync, "_RETRIES", 0)  # no backoff sleeps in this test
    conn = await connect()
    t0 = clock.now
    await sync.sync_connection(conn.org_id, conn.connection_id, force=True, now=t0)

    # within 2x the 60s interval
    assert (await statuses(conn.org_id, conn.location_id, t0 + timedelta(seconds=100)))[
        "orders"
    ] == "fresh"
    # past it, with no failures: stale (a paused worker)
    assert (await statuses(conn.org_id, conn.location_id, t0 + timedelta(seconds=130)))[
        "orders"
    ] == "stale"

    def boom(*_: object, **__: object) -> None:
        raise SquareTransientError("503")

    monkeypatch.setattr(square, "search_orders", boom)
    for i in range(1, 4):
        stats = await sync.sync_connection(
            conn.org_id, conn.connection_id, force=True, now=t0 + timedelta(seconds=60 * i)
        )
        assert any(s.stream == "orders" and s.error for s in stats)
        expected = "failing" if i == 3 else "fresh"
        # data stays "as of" the last success; only the third consecutive failure flips it
        got = await statuses(conn.org_id, conn.location_id, t0 + timedelta(seconds=60 * i + 1))
        assert got["orders"] == ("failing" if i == 3 else got["orders"]), expected
        assert got["catalog"] == "fresh"
    async with scoped_session(conn.org_id) as db:
        row = (
            await db.execute(
                text(
                    "SELECT consecutive_failures, last_error_class FROM integration_streams WHERE stream='orders'"
                )
            )
        ).one()
        failed_runs = await db.scalar(text("SELECT count(*) FROM sync_runs WHERE status='failed'"))
    assert tuple(row) == (3, "SquareTransientError") and failed_runs == 3

    monkeypatch.undo()  # the seller recovers
    monkeypatch.setattr(sync, "_RETRIES", 0)
    t1 = t0 + timedelta(seconds=300)
    await sync.sync_connection(conn.org_id, conn.connection_id, force=True, now=t1)
    assert (await statuses(conn.org_id, conn.location_id, t1 + timedelta(seconds=5)))[
        "orders"
    ] == "fresh"


async def test_auth_failure_marks_needs_reauth_and_pauses_sync(
    connect: Connect, clock: Clock, square: FakeSquareClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = await connect()
    await sync.sync_connection(conn.org_id, conn.connection_id, force=True, now=clock.now)

    def revoked(*_: object, **__: object) -> None:
        raise SquareAuthError("401")

    monkeypatch.setattr(square, "search_orders", revoked)
    await sync.sync_connection(conn.org_id, conn.connection_id, force=True, now=clock.now)
    async with scoped_session(conn.org_id) as db:
        status = await db.scalar(text("SELECT status FROM integration_connections"))
    assert status == "needs_reauth"
    assert set((await statuses(conn.org_id, conn.location_id, clock.now)).values()) == {"failing"}
    # scheduled syncs pause while the connection needs re-auth
    assert (
        await sync.sync_connection(conn.org_id, conn.connection_id, force=True, now=clock.now) == []
    )


async def test_location_without_a_connection_is_not_connected(
    connect: Connect, clock: Clock
) -> None:
    conn = await connect(complete=False)
    got = await statuses(conn.org_id, conn.location_id, clock.now)
    assert got == dict.fromkeys(("orders", "timecards", "catalog", "team_members"), "not_connected")


async def test_push_freshness_uses_daily_cadence(connect: Connect, clock: Clock) -> None:
    conn = await connect(complete=False)
    async with scoped_session(conn.org_id) as db:
        connection = await db.get_one(IntegrationConnection, conn.connection_id)
        connection.provider = "push"
        connection.status = "active"
        db.add(
            IntegrationLocationLink(
                org_id=conn.org_id,
                connection_id=connection.id,
                external_location_id=str(conn.location_id),
                external_name="Juniper House",
                location_id=conn.location_id,
                timezone="America/New_York",
            )
        )
        db.add(
            IntegrationStream(
                org_id=conn.org_id,
                connection_id=connection.id,
                stream="orders",
                last_success_at=clock.now,
                data_as_of=clock.now,
            )
        )
    assert (await statuses(conn.org_id, conn.location_id, clock.now + timedelta(hours=25)))[
        "orders"
    ] == "fresh"
    assert (await statuses(conn.org_id, conn.location_id, clock.now + timedelta(hours=27)))[
        "orders"
    ] == "stale"
    got = await statuses(conn.org_id, conn.location_id, clock.now)
    assert {name: got[name] for name in ("timecards", "catalog", "team_members")} == dict.fromkeys(
        ("timecards", "catalog", "team_members"), "not_connected"
    )


async def test_only_one_sync_runs_per_connection(connect: Connect, clock: Clock) -> None:
    conn = await connect()
    async with sync.connection_lock(conn.connection_id) as first:
        assert first
        async with sync.connection_lock(conn.connection_id) as second:
            assert not second
        assert (
            await sync.sync_connection(conn.org_id, conn.connection_id, force=True, now=clock.now)
            == []
        )
    stats = await sync.sync_connection(conn.org_id, conn.connection_id, force=True, now=clock.now)
    assert {s.stream for s in stats} == set(sync.STREAM_ORDER)
    assert asyncio.get_running_loop()
