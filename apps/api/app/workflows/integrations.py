"""Temporal workflows for the integration layer.

ConnectionSync  scheduled incremental pull, one at a time per connection (advisory lock + the
                schedule's SKIP overlap policy)
Backfill        newest -> oldest, one chunk per activity, on its own task queue so it can never
                starve incremental sync
WebhookFetch    targeted fetch triggered by a verified webhook
TokenRefresh    daily; refreshes credentials expiring within 7 days
"""

import uuid
from datetime import date, timedelta

from pydantic import BaseModel
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from app.integrations import sync

BACKFILL_QUEUE = "chefos-backfill"
REFERENCE_STREAMS = ["locations", "catalog", "team_members"]
_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=10),
    maximum_attempts=5,
)


class SyncInput(BaseModel):
    org_id: uuid.UUID
    connection_id: uuid.UUID
    streams: list[str] | None = None
    force: bool = False
    deferred: bool = False  # apply raw records that were waiting for a location link
    lock: bool = True


class SyncResult(BaseModel):
    fetched: int = 0
    changed: int = 0
    errors: list[str] = []


class BackfillInput(BaseModel):
    org_id: uuid.UUID
    connection_id: uuid.UUID
    from_date: date
    to_date: date
    chunk_days: int = 1


class ChunkInput(BaseModel):
    org_id: uuid.UUID
    connection_id: uuid.UUID
    first: date
    last: date
    workflow_id: str


class ChunkResult(BaseModel):
    fetched: int = 0
    inactive: bool = False


class BackfillResult(BaseModel):
    chunks: int
    fetched: int


class FinishInput(BaseModel):
    org_id: uuid.UUID
    connection_id: uuid.UUID
    workflow_id: str
    error: str | None = None


class WebhookFetchInput(BaseModel):
    org_id: uuid.UUID
    connection_id: uuid.UUID
    stream: str
    ids: list[str] = []
    event_id: str | None = None


class TokenRefreshInput(BaseModel):
    org_id: uuid.UUID
    connection_id: uuid.UUID


# ---------------------------------------------------------------- activities


@activity.defn
async def sync_connection_activity(data: SyncInput) -> SyncResult:
    if data.deferred:
        await sync.process_deferred(data.org_id, data.connection_id)
    stats = await sync.sync_connection(
        data.org_id, data.connection_id, streams=data.streams, force=data.force, lock=data.lock
    )
    return SyncResult(
        fetched=sum(s.fetched for s in stats),
        changed=sum(s.changed for s in stats),
        errors=[s.error for s in stats if s.error],
    )


@activity.defn
async def begin_backfill_activity(data: BackfillInput) -> None:
    await sync.begin_backfill(
        data.org_id,
        data.connection_id,
        data.from_date,
        data.to_date,
        activity.info().workflow_run_id or "",
    )


@activity.defn
async def backfill_chunk_activity(data: ChunkInput) -> ChunkResult:
    counts = await sync.backfill_chunk(
        data.org_id, data.connection_id, data.first, data.last, data.workflow_id
    )
    return ChunkResult(fetched=sum(counts.values()), inactive=not counts)


@activity.defn
async def finish_backfill_activity(data: FinishInput) -> None:
    await sync.finish_backfill(data.org_id, data.connection_id, data.workflow_id, data.error)


@activity.defn
async def webhook_fetch_activity(data: WebhookFetchInput) -> int:
    return await sync.webhook_fetch(
        data.org_id, data.connection_id, data.stream, data.ids, data.event_id
    )


@activity.defn
async def token_refresh_activity(data: TokenRefreshInput) -> bool:
    return await sync.refresh_token(data.org_id, data.connection_id)


# ---------------------------------------------------------------- workflows


@workflow.defn
class ConnectionSync:
    @workflow.run
    async def run(self, data: SyncInput) -> SyncResult:
        return await workflow.execute_activity(
            sync_connection_activity,
            data,
            start_to_close_timeout=timedelta(minutes=9),
            retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)),
        )


@workflow.defn
class Backfill:
    @workflow.run
    async def run(self, data: BackfillInput) -> BackfillResult:
        wid = workflow.info().run_id  # unique per execution: keys the sync_runs rows
        await workflow.execute_activity(
            begin_backfill_activity, data, start_to_close_timeout=timedelta(minutes=1)
        )
        chunks = fetched = 0
        try:
            # Reference data first: orders resolve items and staff against it.
            await workflow.execute_activity(
                sync_connection_activity,
                SyncInput(
                    org_id=data.org_id,
                    connection_id=data.connection_id,
                    streams=REFERENCE_STREAMS,
                    force=True,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_RETRY,
            )
            day = data.to_date
            while day >= data.from_date:
                first = max(data.from_date, day - timedelta(days=data.chunk_days - 1))
                result = await workflow.execute_activity(
                    backfill_chunk_activity,
                    ChunkInput(
                        org_id=data.org_id,
                        connection_id=data.connection_id,
                        first=first,
                        last=day,
                        workflow_id=wid,
                    ),
                    task_queue=BACKFILL_QUEUE,
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=_RETRY,
                )
                if result.inactive:
                    break
                chunks += 1
                fetched += result.fetched
                day = first - timedelta(days=1)
        except ActivityError as exc:
            await workflow.execute_activity(
                finish_backfill_activity,
                FinishInput(
                    org_id=data.org_id,
                    connection_id=data.connection_id,
                    workflow_id=wid,
                    error=str(exc.cause or exc)[:500],
                ),
                start_to_close_timeout=timedelta(minutes=1),
            )
            raise
        await workflow.execute_activity(
            finish_backfill_activity,
            FinishInput(org_id=data.org_id, connection_id=data.connection_id, workflow_id=wid),
            start_to_close_timeout=timedelta(minutes=1),
        )
        return BackfillResult(chunks=chunks, fetched=fetched)


@workflow.defn
class WebhookFetch:
    @workflow.run
    async def run(self, data: WebhookFetchInput) -> int:
        return await workflow.execute_activity(
            webhook_fetch_activity,
            data,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_RETRY,
        )


@workflow.defn
class TokenRefresh:
    @workflow.run
    async def run(self, data: TokenRefreshInput) -> bool:
        return await workflow.execute_activity(
            token_refresh_activity,
            data,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
        )
