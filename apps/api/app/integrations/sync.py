"""Stream orchestration used by the Temporal activities: incremental sync, backfill chunks,
webhook fetches, and token refresh. Each page is fetched outside any transaction and applied
in its own short transaction (set-based), so retries are cheap and always idempotent."""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import activity_org_session, get_engine
from app.integrations import freshness, pipeline
from app.integrations.links import Link, load_links
from app.integrations.models import (
    STREAMS,
    IntegrationConnection,
    IntegrationStream,
    SyncRun,
    WebhookEvent,
)
from app.integrations.providers import get_connector
from app.integrations.providers.square.client import (
    SquareAuthError,
    SquareError,
    SquareRateLimited,
    SquareTransientError,
)
from app.integrations.providers.square.types import Credentials, RawItem, Window
from app.integrations.secrets import decrypt_credentials, encrypt_credentials

log = structlog.get_logger()

# Reference data first, so fact streams can resolve items and staff.
STREAM_ORDER = ("locations", "catalog", "team_members", "orders", "timecards")
FACT_STREAMS = ("orders", "timecards")
ORDER_OVERLAP = timedelta(minutes=10)
TIMECARD_TRAILING = timedelta(days=3)
REFRESH_WITHIN = timedelta(days=7)
_RETRIES = 3


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class StreamStats:
    stream: str
    fetched: int = 0
    changed: int = 0
    deferred: int = 0
    quarantined: int = 0
    error: str | None = None


@dataclass
class ConnState:
    status: str
    creds: Credentials
    links: dict[str, Link]
    streams: dict[str, IntegrationStream] = field(default_factory=dict)

    @property
    def linked_external_ids(self) -> list[str]:
        return [k for k, v in self.links.items() if v.location_id is not None]

    @property
    def zone(self) -> tuple[str, time]:
        for link in self.links.values():
            if link.location_id is not None:
                return link.timezone, link.business_day_start
        return "UTC", time(4, 0)


@contextlib.asynccontextmanager
async def connection_lock(connection_id: uuid.UUID, enabled: bool = True) -> AsyncIterator[bool]:
    """Session-level advisory lock so only one sync runs per connection at a time."""
    if not enabled:
        yield True
        return
    async with get_engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        key = {"k": f"sync:{connection_id}"}
        got = bool(
            await conn.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), key)
        )
        try:
            yield got
        finally:
            if got:
                await conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), key)


async def _load_state(db: AsyncSession, org_id: uuid.UUID, connection_id: uuid.UUID) -> ConnState:
    conn = await db.get(IntegrationConnection, connection_id)
    if conn is None or conn.credential_ciphertext is None:
        raise LookupError(f"connection {connection_id} not found")
    rows = await db.scalars(
        select(IntegrationStream).where(IntegrationStream.connection_id == connection_id)
    )
    return ConnState(
        status=conn.status,
        creds=decrypt_credentials(conn.credential_ciphertext, org_id, connection_id),
        links=await load_links(db, connection_id),
        streams={s.stream: s for s in rows},
    )


async def ensure_streams(db: AsyncSession, org_id: uuid.UUID, connection_id: uuid.UUID) -> None:
    await db.execute(
        pg_insert(IntegrationStream)
        .values([{"org_id": org_id, "connection_id": connection_id, "stream": s} for s in STREAMS])
        .on_conflict_do_nothing()
    )


async def _with_retry[T](fn: Callable[[], T]) -> T:
    """Run a blocking connector call in a thread, backing off on 429 and transient errors."""
    for attempt in range(_RETRIES + 1):
        try:
            return await asyncio.to_thread(fn)
        except SquareRateLimited as exc:
            if attempt == _RETRIES:
                raise
            await asyncio.sleep(min(exc.retry_after or 2.0 * (attempt + 1), 30.0))
        except SquareTransientError:
            if attempt == _RETRIES:
                raise
            await asyncio.sleep(0.5 * 2**attempt)
    raise AssertionError("unreachable")


async def _fetch_pages(
    state: ConnState, stream: str, window: Window
) -> AsyncIterator[list[RawItem]]:
    connector = get_connector()
    tz = state.zone[0]
    cursor: str | None = None
    ext_ids = state.linked_external_ids
    while True:
        page = await _with_retry(
            lambda c=cursor: connector.fetch_page(  # type: ignore[misc]
                state.creds, stream, window, c, ext_ids, tz=tz
            )
        )
        yield page.items
        if not page.next_cursor:
            return
        cursor = page.next_cursor


async def _ingest(
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    stream: str,
    items: list[RawItem],
    run_id: uuid.UUID | None,
    now: datetime,
) -> pipeline.BatchResult:
    async with activity_org_session(org_id) as db:
        ctx = pipeline.ConnCtx(org_id, connection_id, await load_links(db, connection_id))
        return await pipeline.ingest(db, ctx, stream, items, run_id, now)


async def run_stream(
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    state: ConnState,
    stream: str,
    window: Window,
    run_id: uuid.UUID | None,
    now: datetime,
) -> pipeline.BatchResult:
    """Fetch all pages of `stream` in `window` and apply them. Fact streams commit per page;
    reference streams are small and applied whole."""
    total = pipeline.BatchResult()
    if stream in FACT_STREAMS:
        async for items in _fetch_pages(state, stream, window):
            total.merge(await _ingest(org_id, connection_id, stream, items, run_id, now))
    else:
        collected: list[RawItem] = []
        async for items in _fetch_pages(state, stream, window):
            collected.extend(items)
        total.merge(await _ingest(org_id, connection_id, stream, collected, run_id, now))
    return total


def _stream_window(stream: str, row: IntegrationStream | None, now: datetime) -> Window:
    if stream == "orders":
        start = (
            row.watermark if row and row.watermark else now - timedelta(days=1)
        ) - ORDER_OVERLAP
        return Window(start, now)
    if stream == "timecards":
        return Window(now - TIMECARD_TRAILING, now)
    return Window(now - timedelta(days=1), now)


async def _record_success(
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    stream: str,
    window: Window,
    res: pipeline.BatchResult,
    kind: str,
    started: datetime,
    now: datetime,
    advance: bool,
) -> None:
    async with activity_org_session(org_id) as db:
        if advance:
            values: dict[str, Any] = {
                "last_attempt_at": started,
                "last_success_at": now,
                "last_error_class": None,
                "last_error_detail": None,
                "consecutive_failures": 0,
                "data_as_of": window.end,
            }
            if stream in FACT_STREAMS:
                values["watermark"] = window.end
            await db.execute(
                update(IntegrationStream)
                .where(
                    IntegrationStream.connection_id == connection_id,
                    IntegrationStream.stream == stream,
                )
                .values(**values)
            )
        if res.fetched > 0 or kind != "incremental":
            db.add(
                SyncRun(
                    org_id=org_id,
                    connection_id=connection_id,
                    stream=stream,
                    kind=kind,
                    status="succeeded",
                    window_from=window.start,
                    window_to=window.end,
                    started_at=started,
                    finished_at=utcnow(),
                    fetched_count=res.fetched,
                    changed_count=res.applied,
                )
            )


async def _record_failure(
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    stream: str,
    window: Window,
    exc: Exception,
    kind: str,
    started: datetime,
    update_stream: bool = True,
) -> None:
    detail = f"{type(exc).__name__}: {exc}"[:1000]
    async with activity_org_session(org_id) as db:
        if update_stream:
            await db.execute(
                text(
                    "UPDATE integration_streams SET last_attempt_at = :t, last_error_class = :c, "
                    "last_error_detail = :d, consecutive_failures = consecutive_failures + 1 "
                    "WHERE connection_id = :conn AND stream = :s"
                ),
                {
                    "t": started,
                    "c": type(exc).__name__,
                    "d": detail,
                    "conn": connection_id,
                    "s": stream,
                },
            )
        if isinstance(exc, SquareAuthError):
            await db.execute(
                text(
                    "UPDATE integration_connections SET status = 'needs_reauth' "
                    "WHERE id = :c AND status = 'active'"
                ),
                {"c": connection_id},
            )
        db.add(
            SyncRun(
                org_id=org_id,
                connection_id=connection_id,
                stream=stream,
                kind=kind,
                status="failed",
                window_from=window.start,
                window_to=window.end,
                started_at=started,
                finished_at=utcnow(),
                error_class=type(exc).__name__,
                error_detail=detail,
            )
        )


async def sync_connection(
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    streams: list[str] | None = None,
    force: bool = False,
    kind: str = "incremental",
    now: datetime | None = None,
    lock: bool = True,
) -> list[StreamStats]:
    """One incremental pass over the due streams. A no-op if another pass holds the lock."""
    now = now or utcnow()
    async with connection_lock(connection_id, enabled=lock) as got:
        if not got:
            log.info("sync_skipped_locked", connection_id=str(connection_id))
            return []
        async with activity_org_session(org_id) as db:
            await ensure_streams(db, org_id, connection_id)
        try:
            async with activity_org_session(org_id) as db:
                state = await _load_state(db, org_id, connection_id)
        except LookupError:
            return []  # connection is gone (orphaned schedule): nothing to do, nothing to retry
        if state.status != "active":
            return []
        out: list[StreamStats] = []
        for stream in STREAM_ORDER:
            if streams is not None and stream not in streams:
                continue
            row = state.streams.get(stream)
            if not force and not _is_due(stream, row, now):
                continue
            if stream in FACT_STREAMS and not state.linked_external_ids:
                continue
            window = _stream_window(stream, row, now)
            stats = StreamStats(stream)
            try:
                res = await run_stream(org_id, connection_id, state, stream, window, None, now)
            except (SquareError, OSError, TimeoutError) as exc:
                stats.error = f"{type(exc).__name__}: {exc}"
                await _record_failure(org_id, connection_id, stream, window, exc, kind, now)
                out.append(stats)
                if isinstance(exc, SquareAuthError):
                    break
                continue
            await _record_success(
                org_id, connection_id, stream, window, res, kind, now, now, advance=True
            )
            stats.fetched, stats.changed = res.fetched, res.applied
            stats.deferred, stats.quarantined = res.deferred, res.quarantined
            out.append(stats)
            if stream == "locations":  # new links change what fact streams may fetch
                async with activity_org_session(org_id) as db:
                    state.links = await load_links(db, connection_id)
        return out


def _is_due(stream: str, row: IntegrationStream | None, now: datetime) -> bool:
    if row is None or row.last_attempt_at is None:
        return True
    interval = freshness.stream_interval_seconds(stream)
    return (now - row.last_attempt_at).total_seconds() >= interval * 0.5


# ---------------------------------------------------------------- backfill


def day_window(tz: str, day_start: time, first: date, last: date) -> Window:
    zone = ZoneInfo(tz)
    start = datetime.combine(first, day_start, tzinfo=zone)
    end = datetime.combine(last + timedelta(days=1), day_start, tzinfo=zone)
    return Window(start.astimezone(UTC), end.astimezone(UTC))


def backfill_run_id(workflow_id: str, stream: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"backfill:{workflow_id}:{stream}")


async def begin_backfill(
    org_id: uuid.UUID, connection_id: uuid.UUID, first: date, last: date, workflow_id: str
) -> None:
    async with activity_org_session(org_id) as db:
        await ensure_streams(db, org_id, connection_id)
        for stream in FACT_STREAMS:
            await db.execute(
                text(
                    "UPDATE integration_streams SET backfill_target_from = "
                    "LEAST(COALESCE(backfill_target_from, :f), :f) "
                    "WHERE connection_id = :c AND stream = :s"
                ),
                {"f": first, "c": connection_id, "s": stream},
            )
            await db.execute(
                pg_insert(SyncRun)
                .values(
                    id=backfill_run_id(workflow_id, stream),
                    org_id=org_id,
                    connection_id=connection_id,
                    stream=stream,
                    kind="backfill",
                    status="running",
                    window_from=datetime.combine(first, time(), tzinfo=UTC),
                    window_to=datetime.combine(last, time(), tzinfo=UTC),
                    started_at=utcnow(),
                )
                .on_conflict_do_nothing()
            )


async def backfill_chunk(
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    first: date,
    last: date,
    workflow_id: str,
    now: datetime | None = None,
) -> dict[str, int]:
    """Load business dates [first, last] for orders and timecards."""
    now = now or utcnow()
    async with activity_org_session(org_id) as db:
        state = await _load_state(db, org_id, connection_id)
    if state.status != "active" or not state.linked_external_ids:
        return {}
    tz, day_start = state.zone
    window = day_window(tz, day_start, first, last)
    counts: dict[str, int] = {}
    for stream in FACT_STREAMS:
        run_id = backfill_run_id(workflow_id, stream)
        try:
            res = await run_stream(org_id, connection_id, state, stream, window, run_id, now)
        except SquareError as exc:
            await _record_failure(
                org_id, connection_id, stream, window, exc, "backfill", now, update_stream=False
            )
            raise
        counts[stream] = res.fetched
        async with activity_org_session(org_id) as db:
            await db.execute(
                text(
                    "UPDATE integration_streams SET backfill_completed_through = "
                    "LEAST(COALESCE(backfill_completed_through, :d), :d) "
                    "WHERE connection_id = :c AND stream = :s"
                ),
                {"d": first, "c": connection_id, "s": stream},
            )
            await db.execute(
                text(
                    "UPDATE sync_runs SET fetched_count = fetched_count + :f, "
                    "changed_count = changed_count + :a WHERE id = :id"
                ),
                {"f": res.fetched, "a": res.applied, "id": run_id},
            )
    return counts


async def finish_backfill(
    org_id: uuid.UUID, connection_id: uuid.UUID, workflow_id: str, error: str | None
) -> None:
    async with activity_org_session(org_id) as db:
        for stream in FACT_STREAMS:
            await db.execute(
                text(
                    "UPDATE sync_runs SET status = :s, finished_at = now(), error_detail = :e "
                    "WHERE id = :id AND status = 'running'"
                ),
                {
                    "s": "failed" if error else "succeeded",
                    "e": error,
                    "id": backfill_run_id(workflow_id, stream),
                },
            )


async def process_deferred(
    org_id: uuid.UUID, connection_id: uuid.UUID, now: datetime | None = None
) -> int:
    """Apply raw records that waited for a location link."""
    now = now or utcnow()
    total = 0
    for stream in FACT_STREAMS:
        while True:
            async with activity_org_session(org_id) as db:
                ctx = pipeline.ConnCtx(org_id, connection_id, await load_links(db, connection_id))
                res = await pipeline.process_pending(db, ctx, stream, now)
            total += res.applied
            if res.fetched == 0 or res.applied == 0:
                break
    return total


# ---------------------------------------------------------------- webhooks, tokens


async def webhook_fetch(
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    stream: str,
    ids: list[str],
    event_id: str | None = None,
    now: datetime | None = None,
) -> int:
    now = now or utcnow()
    if stream not in ("orders",):
        stats = await sync_connection(
            org_id, connection_id, streams=[stream], force=True, kind="webhook", now=now
        )
        changed = sum(s.changed for s in stats)
    else:
        async with activity_org_session(org_id) as db:
            state = await _load_state(db, org_id, connection_id)
        connector = get_connector()
        locs = state.linked_external_ids
        items = (
            await _with_retry(lambda: connector.fetch_by_ids(state.creds, stream, ids, locs))
            if state.status == "active" and locs
            else []
        )
        res = await _ingest(org_id, connection_id, stream, items, None, now)
        window = Window(now, now)
        await _record_success(
            org_id, connection_id, stream, window, res, "webhook", now, now, advance=False
        )
        changed = res.applied
    if event_id:
        async with activity_org_session(org_id) as db:
            await db.execute(
                update(WebhookEvent)
                .where(WebhookEvent.provider == "square", WebhookEvent.event_id == event_id)
                .values(processed_at=utcnow())
            )
    return changed


async def refresh_token(
    org_id: uuid.UUID, connection_id: uuid.UUID, now: datetime | None = None
) -> bool:
    """Refresh credentials expiring within 7 days. On auth failure, mark needs_reauth."""
    now = now or utcnow()
    async with activity_org_session(org_id) as db:
        conn = await db.get(IntegrationConnection, connection_id)
        if conn is None or conn.status != "active" or conn.credential_ciphertext is None:
            return False
        creds = decrypt_credentials(conn.credential_ciphertext, org_id, connection_id)
        if creds.expires_at - now > REFRESH_WITHIN:
            return False
    try:
        new = await _with_retry(lambda: get_connector().refresh(creds))
    except SquareAuthError:
        async with activity_org_session(org_id) as db:
            await db.execute(
                text("UPDATE integration_connections SET status = 'needs_reauth' WHERE id = :c"),
                {"c": connection_id},
            )
        return False
    async with activity_org_session(org_id) as db:
        await db.execute(
            update(IntegrationConnection)
            .where(IntegrationConnection.id == connection_id)
            .values(
                credential_ciphertext=encrypt_credentials(new, org_id, connection_id),
                credential_expires_at=new.expires_at,
            )
        )
    return True
