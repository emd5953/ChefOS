"""Starting and scheduling integration workflows from the API/CLI side."""

import contextlib
import uuid
from datetime import date, timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError

from app.config import get_settings
from app.workflows.client import TASK_QUEUE, get_temporal_client
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

DEFAULT_BACKFILL_DAYS = 395


def sync_schedule_id(connection_id: uuid.UUID) -> str:
    return f"sync-{connection_id}"


def token_schedule_id(connection_id: uuid.UUID) -> str:
    return f"token-refresh-{connection_id}"


def backfill_workflow_id(connection_id: uuid.UUID) -> str:
    return f"backfill-{connection_id}"


async def _ensure_schedule(client: Client, schedule_id: str, schedule: Schedule) -> None:
    with contextlib.suppress(ScheduleAlreadyRunningError):
        await client.create_schedule(schedule_id, schedule)


async def ensure_schedules(
    org_id: uuid.UUID, connection_id: uuid.UUID, client: Client | None = None
) -> None:
    """Idempotently create the per-connection sync and daily token-refresh schedules."""
    client = client or await get_temporal_client()
    interval = timedelta(seconds=get_settings().sync_interval_seconds)
    await _ensure_schedule(
        client,
        sync_schedule_id(connection_id),
        Schedule(
            action=ScheduleActionStartWorkflow(
                ConnectionSync.run,
                SyncInput(org_id=org_id, connection_id=connection_id),
                id=sync_schedule_id(connection_id),
                task_queue=TASK_QUEUE,
                execution_timeout=timedelta(minutes=10),
            ),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=interval)]),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        ),
    )
    await _ensure_schedule(
        client,
        token_schedule_id(connection_id),
        Schedule(
            action=ScheduleActionStartWorkflow(
                TokenRefresh.run,
                TokenRefreshInput(org_id=org_id, connection_id=connection_id),
                id=token_schedule_id(connection_id),
                task_queue=TASK_QUEUE,
            ),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(days=1))]),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        ),
    )


async def delete_schedules(connection_id: uuid.UUID, client: Client | None = None) -> None:
    client = client or await get_temporal_client()
    for sid in (sync_schedule_id(connection_id), token_schedule_id(connection_id)):
        with contextlib.suppress(RPCError):  # already gone
            await client.get_schedule_handle(sid).delete()


async def start_sync(
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    streams: list[str] | None = None,
    *,
    deferred: bool = False,
    client: Client | None = None,
) -> str:
    """Run a forced sync now. Concurrent runs are harmless: the activity takes a per-connection
    lock and skips if another pass is running."""
    client = client or await get_temporal_client()
    workflow_id = f"sync-now-{connection_id}-{uuid.uuid4().hex[:8]}"
    await client.start_workflow(
        ConnectionSync.run,
        SyncInput(
            org_id=org_id,
            connection_id=connection_id,
            streams=streams,
            force=True,
            deferred=deferred,
        ),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    return workflow_id


async def start_backfill(
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    first: date,
    last: date,
    *,
    reuse_running: bool = False,
    client: Client | None = None,
) -> str:
    """Start the Backfill workflow. With `reuse_running`, an already-running one is returned
    instead of raising (used by connect and the demo, which must be idempotent)."""
    client = client or await get_temporal_client()
    workflow_id = backfill_workflow_id(connection_id)
    await client.start_workflow(
        Backfill.run,
        BackfillInput(org_id=org_id, connection_id=connection_id, from_date=first, to_date=last),
        id=workflow_id,
        task_queue=TASK_QUEUE,
        id_conflict_policy=(
            WorkflowIDConflictPolicy.USE_EXISTING
            if reuse_running
            else WorkflowIDConflictPolicy.FAIL
        ),
    )
    return workflow_id


async def start_webhook_fetch(
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    stream: str,
    ids: list[str],
    event_id: str,
    client: Client | None = None,
) -> str:
    client = client or await get_temporal_client()
    workflow_id = f"webhook-{event_id}"
    await client.start_workflow(
        WebhookFetch.run,
        WebhookFetchInput(
            org_id=org_id, connection_id=connection_id, stream=stream, ids=ids, event_id=event_id
        ),
        id=workflow_id,
        task_queue=TASK_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    return workflow_id


async def terminate_backfill(connection_id: uuid.UUID, client: Client | None = None) -> None:
    client = client or await get_temporal_client()
    with contextlib.suppress(RPCError):
        await client.get_workflow_handle(backfill_workflow_id(connection_id)).terminate(
            "connection disconnected"
        )
