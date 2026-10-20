"""Integration workflows in the Temporal time-skipping test environment."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from temporalio.client import ScheduleAlreadyRunningError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from app.db import scoped_session
from app.integrations import sync, temporal
from app.integrations.providers.square.fake_client import FakeSquareClient
from app.integrations.secrets import decrypt_credentials, encrypt_credentials
from app.workflows import worker
from app.workflows.client import TASK_QUEUE
from app.workflows.integrations import (
    Backfill,
    BackfillInput,
    ConnectionSync,
    SyncInput,
    TokenRefresh,
    TokenRefreshInput,
    WebhookFetch,
    WebhookFetchInput,
)
from tests.integrations.conftest import Clock, Connect, Connected, load_days


@pytest.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    e = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    yield e
    await e.shutdown()


async def count(conn: Connected, sql: str) -> int:
    async with scoped_session(conn.org_id) as db:
        return int(await db.scalar(text(sql)) or 0)


def test_backfill_runs_on_its_own_queue_so_it_cannot_starve_incremental_sync() -> None:
    assert worker.integrations.backfill_chunk_activity in worker.BACKFILL_ACTIVITIES
    assert worker.integrations.backfill_chunk_activity not in worker.ACTIVITIES
    assert worker.integrations.BACKFILL_QUEUE != TASK_QUEUE
    assert worker.BACKFILL_CONCURRENCY < 100  # lower concurrency than the default worker


async def test_connection_sync_workflow(
    env: WorkflowEnvironment, connect: Connect, clock: Clock
) -> None:
    conn = await connect()
    async with worker.build_worker(env.client):  # the default queue alone is enough for sync
        result = await env.client.execute_workflow(
            ConnectionSync.run,
            SyncInput(org_id=conn.org_id, connection_id=conn.connection_id, force=True),
            id=f"sync-{conn.connection_id}",
            task_queue=TASK_QUEUE,
        )
    assert result.fetched > 0 and result.errors == []
    assert (
        await count(
            conn, "SELECT count(*) FROM integration_streams WHERE last_success_at IS NOT NULL"
        )
        == 5
    )
    assert await count(conn, "SELECT count(*) FROM menu_items") > 30


async def test_backfill_workflow_goes_newest_to_oldest_in_day_chunks(
    env: WorkflowEnvironment, connect: Connect, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = await connect()
    order: list[tuple[date, date]] = []
    original = sync.backfill_chunk

    async def spy(
        org: uuid.UUID, cid: uuid.UUID, first: date, last: date, wid: str, now: Any = None
    ) -> Any:
        order.append((first, last))
        return await original(org, cid, first, last, wid, clock.now)

    monkeypatch.setattr(sync, "backfill_chunk", spy)
    first, last = date(2026, 3, 1), date(2026, 3, 5)
    async with worker.run_workers(env.client):
        result = await env.client.execute_workflow(
            Backfill.run,
            BackfillInput(
                org_id=conn.org_id,
                connection_id=conn.connection_id,
                from_date=first,
                to_date=last,
                chunk_days=2,
            ),
            id=f"backfill-{conn.connection_id}",
            task_queue=TASK_QUEUE,
        )
    assert order == [
        (date(2026, 3, 4), date(2026, 3, 5)),
        (date(2026, 3, 2), date(2026, 3, 3)),
        (date(2026, 3, 1), date(2026, 3, 1)),
    ]
    assert result.chunks == 3 and result.fetched > 0
    async with scoped_session(conn.org_id) as db:
        rows = (
            await db.execute(
                text(
                    "SELECT stream, backfill_target_from, backfill_completed_through FROM integration_streams "
                    "WHERE stream IN ('orders', 'timecards') ORDER BY stream"
                )
            )
        ).all()
        runs = (
            await db.execute(
                text(
                    "SELECT stream, status, fetched_count FROM sync_runs WHERE kind = 'backfill' ORDER BY stream"
                )
            )
        ).all()
    assert [tuple(r) for r in rows] == [("orders", first, first), ("timecards", first, first)]
    assert [(r.stream, r.status) for r in runs] == [
        ("orders", "succeeded"),
        ("timecards", "succeeded"),
    ]
    assert all(r.fetched_count > 0 for r in runs)
    assert (
        await count(
            conn,
            "SELECT count(*) FROM daily_sales WHERE business_date BETWEEN '2026-03-01' AND '2026-03-05'",
        )
        > 0
    )


async def test_backfill_survives_a_failed_chunk_and_retries(
    env: WorkflowEnvironment, connect: Connect, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = await connect()
    original = sync.backfill_chunk
    calls = {"n": 0}

    async def flaky(
        org: uuid.UUID, cid: uuid.UUID, first: date, last: date, wid: str, now: Any = None
    ) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("worker died mid-chunk")  # Temporal retries the activity
        return await original(org, cid, first, last, wid, clock.now)

    monkeypatch.setattr(sync, "backfill_chunk", flaky)
    async with worker.run_workers(env.client):
        result = await env.client.execute_workflow(
            Backfill.run,
            BackfillInput(
                org_id=conn.org_id,
                connection_id=conn.connection_id,
                from_date=date(2026, 3, 3),
                to_date=date(2026, 3, 5),
            ),
            id=f"backfill-{conn.connection_id}",
            task_queue=TASK_QUEUE,
        )
    assert result.chunks == 3 and calls["n"] == 4
    before = await count(conn, "SELECT count(*) FROM sales_orders")
    assert before == await count(
        conn, "SELECT count(DISTINCT external_id) FROM sales_orders"
    )  # no duplicates


async def test_webhook_fetch_workflow_is_idempotent(
    env: WorkflowEnvironment, connect: Connect, clock: Clock
) -> None:
    conn = await connect()
    await load_days(conn, clock, date(2026, 3, 5), date(2026, 3, 6))
    before = await count(conn, "SELECT count(*) FROM raw_records")
    async with async_worker(env):
        for i in range(2):
            changed = await env.client.execute_workflow(
                WebhookFetch.run,
                WebhookFetchInput(
                    org_id=conn.org_id,
                    connection_id=conn.connection_id,
                    stream="orders",
                    ids=["fake-ord-20260306-0001", "fake-ord-20260306-0002"],
                ),
                id=f"webhook-{conn.connection_id}-{i}",
                task_queue=TASK_QUEUE,
            )
            assert changed == 0
    assert await count(conn, "SELECT count(*) FROM raw_records") == before


def async_worker(env: WorkflowEnvironment) -> Any:
    return worker.build_worker(env.client)


async def test_token_refresh_workflow(
    env: WorkflowEnvironment, connect: Connect, clock: Clock, square: FakeSquareClient
) -> None:
    conn = await connect()
    async with scoped_session(conn.org_id) as db:
        cipher = bytes(
            await db.scalar(text("SELECT credential_ciphertext FROM integration_connections"))
        )
        creds = decrypt_credentials(cipher, conn.org_id, conn.connection_id)
    async with async_worker(env):
        run = lambda: env.client.execute_workflow(  # noqa: E731
            TokenRefresh.run,
            TokenRefreshInput(org_id=conn.org_id, connection_id=conn.connection_id),
            id=f"token-{uuid.uuid4().hex}",
            task_queue=TASK_QUEUE,
        )
        real_now = datetime.now(UTC)  # the workflow checks expiry against the real clock
        fresh = type(creds)(
            creds.access_token,
            creds.refresh_token,
            real_now + timedelta(days=30),
            creds.merchant_id,
        )
        async with scoped_session(conn.org_id) as db:
            await db.execute(
                text("UPDATE integration_connections SET credential_ciphertext = :c"),
                {"c": encrypt_credentials(fresh, conn.org_id, conn.connection_id)},
            )
        # Credentials 30 days from expiry are left alone.
        assert await run() is False
        # Expiring within 7 days: refreshed and stored encrypted.
        soon = type(creds)(
            creds.access_token,
            creds.refresh_token,
            clock.now + timedelta(days=3),
            creds.merchant_id,
        )
        async with scoped_session(conn.org_id) as db:
            await db.execute(
                text("UPDATE integration_connections SET credential_ciphertext = :c"),
                {"c": encrypt_credentials(soon, conn.org_id, conn.connection_id)},
            )
        assert await run() is True
    async with scoped_session(conn.org_id) as db:
        cipher = bytes(
            await db.scalar(text("SELECT credential_ciphertext FROM integration_connections"))
        )
        status = await db.scalar(text("SELECT status FROM integration_connections"))
    assert decrypt_credentials(
        cipher, conn.org_id, conn.connection_id
    ).expires_at > clock.now + timedelta(days=20)
    assert status == "active"


async def test_token_refresh_failure_sets_needs_reauth(
    env: WorkflowEnvironment, connect: Connect, clock: Clock
) -> None:
    conn = await connect()
    async with scoped_session(conn.org_id) as db:
        cipher = bytes(
            await db.scalar(text("SELECT credential_ciphertext FROM integration_connections"))
        )
        creds = decrypt_credentials(cipher, conn.org_id, conn.connection_id)
        bad = type(creds)(
            creds.access_token,
            "not-a-refresh-token",
            clock.now + timedelta(days=1),
            creds.merchant_id,
        )
        await db.execute(
            text("UPDATE integration_connections SET credential_ciphertext = :c"),
            {"c": encrypt_credentials(bad, conn.org_id, conn.connection_id)},
        )
    async with async_worker(env):
        refreshed = await env.client.execute_workflow(
            TokenRefresh.run,
            TokenRefreshInput(org_id=conn.org_id, connection_id=conn.connection_id),
            id=f"token-{uuid.uuid4().hex}",
            task_queue=TASK_QUEUE,
        )
    assert refreshed is False
    assert (
        await count(
            conn, "SELECT count(*) FROM integration_connections WHERE status = 'needs_reauth'"
        )
        == 1
    )


class _Handle:
    def __init__(self) -> None:
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


class _FakeClient:
    def __init__(self) -> None:
        self.created: dict[str, Any] = {}
        self.handles: dict[str, _Handle] = {}

    async def create_schedule(self, sid: str, schedule: Any) -> None:
        if sid in self.created:
            raise ScheduleAlreadyRunningError()
        self.created[sid] = schedule

    def get_schedule_handle(self, sid: str) -> _Handle:
        return self.handles.setdefault(sid, _Handle())


async def test_schedules_are_per_connection_idempotent_and_removable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()  # the autouse fixture stubs schedule helpers; this test needs the real ones
    client = _FakeClient()
    org, cid = uuid.uuid4(), uuid.uuid4()
    await temporal.ensure_schedules(org, cid, client=client)  # type: ignore[arg-type]
    await temporal.ensure_schedules(org, cid, client=client)  # re-running is harmless
    sync_s = client.created[f"sync-{cid}"]
    token_s = client.created[f"token-refresh-{cid}"]
    assert sync_s.spec.intervals[0].every == timedelta(seconds=60)  # SYNC_INTERVAL_SECONDS
    assert token_s.spec.intervals[0].every == timedelta(days=1)
    assert sync_s.policy.overlap.name == "SKIP"  # one run at a time per connection
    assert sync_s.action.args[0].connection_id == cid
    await temporal.delete_schedules(cid, client=client)  # type: ignore[arg-type]
    assert all(h.deleted for h in client.handles.values()) and len(client.handles) == 2
