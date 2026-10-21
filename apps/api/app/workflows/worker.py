"""Temporal worker entrypoint: `python -m app.workflows.worker`.

THE registry. To add workflows/activities from a new module, import them here and append
them to WORKFLOWS / ACTIVITIES. Every workflow's input must be a Pydantic model whose first
field is `org_id: UUID`, and activities open DB sessions only via `activity_org_session`.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import structlog
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from app.config import get_settings
from app.telemetry import setup_telemetry
from app.workflows import integrations, ping, team
from app.workflows.client import TASK_QUEUE, get_temporal_client

WORKFLOWS: list[type[Any]] = [
    ping.PingWorkflow,
    integrations.ConnectionSync,
    integrations.Backfill,
    integrations.WebhookFetch,
    integrations.TokenRefresh,
]

ACTIVITIES: list[Callable[..., Any]] = [
    ping.ping_activity,
    integrations.sync_connection_activity,
    integrations.begin_backfill_activity,
    integrations.finish_backfill_activity,
    integrations.webhook_fetch_activity,
    integrations.token_refresh_activity,
    team.attendance_check_activity,
]

# Backfill chunks run on their own queue with lower concurrency, so a 13-month backfill can
# never starve the incremental sync (spec: "Backfill yields to incremental sync").
BACKFILL_ACTIVITIES: list[Callable[..., Any]] = [
    integrations.backfill_chunk_activity,
]
BACKFILL_CONCURRENCY = 2

READY_FILE = Path("/tmp/worker.ready")  # noqa: S108  (container healthcheck marker)


def build_worker(client: Any) -> Worker:
    return Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=WORKFLOWS,
        activities=ACTIVITIES,
        interceptors=[TracingInterceptor()],
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules("app")
        ),
    )


def build_backfill_worker(client: Any) -> Worker:
    return Worker(
        client,
        task_queue=integrations.BACKFILL_QUEUE,
        activities=BACKFILL_ACTIVITIES,
        max_concurrent_activities=BACKFILL_CONCURRENCY,
        interceptors=[TracingInterceptor()],
    )


@contextlib.asynccontextmanager
async def run_workers(client: Any) -> AsyncIterator[None]:
    """Both workers (default queue and backfill queue), as the worker process runs them."""
    async with build_worker(client), build_backfill_worker(client):
        yield


async def main() -> None:
    setup_telemetry("chefos-worker")
    log = structlog.get_logger()
    await asyncio.to_thread(READY_FILE.unlink, missing_ok=True)
    client = await get_temporal_client()
    log.info("worker_started", task_queue=TASK_QUEUE, temporal=get_settings().temporal_address)
    async with run_workers(client):
        await asyncio.to_thread(READY_FILE.touch)
        await asyncio.Event().wait()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
