"""Data freshness: per stream and location, derived from integration_streams.

fresh   : last successful run within 2x the stream's schedule interval
stale   : older than that, with no failing runs (a paused worker, say)
failing : 3+ consecutive failed runs, or the connection needs re-auth / is in error
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.models import (
    IntegrationConnection,
    IntegrationLocationLink,
    IntegrationStream,
)

FAILING_AFTER = 3
FRESHNESS_STREAMS = ("orders", "timecards", "catalog", "team_members")
HealthStatus = Literal["fresh", "stale", "failing", "pending"]


def stream_interval_seconds(stream: str) -> int:
    base = get_settings().sync_interval_seconds
    return {
        "orders": base,
        "timecards": base,
        "catalog": max(base, 900),
        "team_members": max(base, 6 * 3600),
        "locations": max(base, 3600),
    }[stream]


def stream_health(
    row: IntegrationStream | None, connection_status: str, now: datetime
) -> HealthStatus:
    if connection_status in ("needs_reauth", "error"):
        return "failing"
    if row is None:
        return "pending"
    if row.consecutive_failures >= FAILING_AFTER:
        return "failing"
    if row.last_success_at is None:
        return "pending"
    age = (now - row.last_success_at).total_seconds()
    return "fresh" if age <= 2 * stream_interval_seconds(row.stream) else "stale"


@dataclass(frozen=True)
class FreshnessItem:
    stream: str
    location_id: uuid.UUID
    data_as_of: datetime | None
    status: str  # fresh | stale | failing | not_connected


async def location_freshness(
    db: AsyncSession, location_id: uuid.UUID, now: datetime | None = None
) -> list[FreshnessItem]:
    now = now or datetime.now(UTC)
    row = (
        await db.execute(
            select(IntegrationConnection)
            .join(
                IntegrationLocationLink,
                IntegrationLocationLink.connection_id == IntegrationConnection.id,
            )
            .where(
                IntegrationLocationLink.location_id == location_id,
                IntegrationLocationLink.active.is_(True),
                IntegrationConnection.status != "disconnected",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return [FreshnessItem(s, location_id, None, "not_connected") for s in FRESHNESS_STREAMS]
    streams = {
        s.stream: s
        for s in await db.scalars(
            select(IntegrationStream).where(IntegrationStream.connection_id == row.id)
        )
    }
    out: list[FreshnessItem] = []
    for name in FRESHNESS_STREAMS:
        s = streams.get(name)
        health = stream_health(s, row.status, now)
        # Never-synced streams are reported stale: there is no data to call current.
        status = "stale" if health == "pending" else health
        out.append(FreshnessItem(name, location_id, s.data_as_of if s else None, status))
    return out
