"""Bounded history maintenance, run on worker startup and hourly thereafter."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog

from app.db import purge_expired_sous_conversations

INTERVAL_SECONDS = 3600
BATCH_SIZE = 100
MAX_BATCHES = 10
log = structlog.get_logger()


async def cleanup() -> int:
    """Cap work per sweep, with a separate short transaction for every batch."""
    removed = 0
    for _ in range(MAX_BATCHES):
        count = await purge_expired_sous_conversations(BATCH_SIZE)
        removed += count
        if count < BATCH_SIZE:
            break
    return removed


async def _loop() -> None:
    while True:
        try:
            removed = await cleanup()
            log.info("sous_history_cleanup", removed=removed)
        except Exception:
            # A maintenance failure must not stop sync/backfill workers. Retry next sweep.
            log.exception("sous_history_cleanup_failed")
        await asyncio.sleep(INTERVAL_SECONDS)


@asynccontextmanager
async def run_cleanup() -> AsyncIterator[None]:
    task = asyncio.create_task(_loop(), name="sous-history-cleanup")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
