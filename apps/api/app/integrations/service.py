"""Connection management: OAuth, linking, disconnect, mapping issues, serialization."""

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import lookup_connection_owners, scoped_session
from app.errors import ProblemError, not_found, validation_error
from app.identity.models import Location
from app.integrations import freshness, links, pipeline, sync, temporal
from app.integrations.models import (
    STREAMS,
    IntegrationConnection,
    IntegrationLocationLink,
    IntegrationStream,
    MappingIssue,
    SyncRun,
    WebhookEvent,
)
from app.integrations.providers import get_connector
from app.integrations.providers.square.business_date import business_date
from app.integrations.providers.square.client import SquareError
from app.integrations.schemas import (
    ConnectionDetailOut,
    ConnectionOut,
    LocationLinkOut,
    MappingIssueOut,
    StreamHealthOut,
    SyncRunOut,
)
from app.integrations.secrets import encrypt_credentials

log = structlog.get_logger()
PROVIDER = "square"
STATE_TTL = 600
BACKFILL_DAYS = temporal.DEFAULT_BACKFILL_DAYS


# ---------------------------------------------------------------- OAuth state


def _sign(data: bytes) -> str:
    key = get_settings().master_key_bytes
    mac = hmac.new(key, b"oauth-state:" + data, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def sign_state(org_id: uuid.UUID, connection_id: uuid.UUID, user_id: uuid.UUID) -> str:
    body = json.dumps(
        {
            "o": str(org_id),
            "c": str(connection_id),
            "u": str(user_id),
            "n": secrets.token_urlsafe(8),
            "e": int(time.time()) + STATE_TTL,
        }
    ).encode()
    return base64.urlsafe_b64encode(body).decode().rstrip("=") + "." + _sign(body)


@dataclass(frozen=True)
class OAuthState:
    org_id: uuid.UUID
    connection_id: uuid.UUID
    user_id: uuid.UUID


def verify_state(token: str) -> OAuthState | None:
    try:
        body_b64, sig = token.split(".", 1)
        body = base64.urlsafe_b64decode(body_b64 + "=" * (-len(body_b64) % 4))
        if not hmac.compare_digest(_sign(body), sig):
            return None
        d = json.loads(body)
        if d["e"] < time.time():
            return None
        return OAuthState(uuid.UUID(d["o"]), uuid.UUID(d["c"]), uuid.UUID(d["u"]))
    except ValueError, KeyError, TypeError:
        return None


def redirect_uri() -> str:
    return f"{get_settings().api_base}/v1/integrations/square/callback"


# ---------------------------------------------------------------- authorize / callback


async def start_authorize(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> str:
    settings = get_settings()
    conn = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.provider == PROVIDER,
            IntegrationConnection.status != "disconnected",
        )
    )
    if conn is not None and conn.status == "active":
        raise ProblemError(409, "conflict", "Square is already connected for this organization")
    if conn is None:
        conn = IntegrationConnection(
            org_id=org_id, provider=PROVIDER, mode=settings.square_mode, created_by=user_id
        )
        db.add(conn)
        await db.flush()
    state = sign_state(org_id, conn.id, user_id)
    url = get_connector().authorize_url(state, redirect_uri())
    return f"{settings.api_base}{url}" if url.startswith("/") else url


async def complete_oauth(state_token: str, code: str) -> uuid.UUID:
    """Exchange the code, store encrypted credentials, import locations, auto-link, activate,
    then schedule syncs and start the backfill. Raises ProblemError with a short code."""
    state = verify_state(state_token)
    if state is None:
        raise ProblemError(400, "validation_error", "invalid_state")
    connector = get_connector()
    async with scoped_session(state.org_id) as db:
        conn = await db.get(IntegrationConnection, state.connection_id)
        if conn is None or conn.status == "disconnected":
            raise ProblemError(400, "validation_error", "invalid_state")
    try:
        creds = await _blocking(lambda: connector.exchange_code(code, redirect_uri()))
        raw_locations = await _blocking(lambda: connector.list_locations(creds))
    except SquareError as exc:
        log.warning("oauth_exchange_failed", error=str(exc))
        raise ProblemError(400, "validation_error", "exchange_failed") from exc

    now = datetime.now(UTC)
    async with scoped_session(state.org_id) as db:
        conn = await db.get_one(IntegrationConnection, state.connection_id)
        conn.credential_ciphertext = encrypt_credentials(creds, state.org_id, conn.id)
        conn.credential_expires_at = creds.expires_at
        conn.external_account_id = creds.merchant_id
        conn.scopes = list(_scopes())
        conn.api_version = get_settings().square_version if conn.mode == "sandbox" else "fake"
        conn.status = "active"
        await sync.ensure_streams(db, state.org_id, conn.id)
        ctx = pipeline.ConnCtx(state.org_id, conn.id, {})
        await pipeline.ingest(db, ctx, "locations", raw_locations, None, now)
        await links.auto_link(db, state.org_id, conn.id)
    await kick_off(state.org_id, state.connection_id)
    return state.connection_id


def _scopes() -> list[str]:
    from app.integrations.providers.square.connector import SCOPES

    return SCOPES


async def _blocking[T](fn: Callable[[], T]) -> T:
    return await asyncio.to_thread(fn)


async def kick_off(org_id: uuid.UUID, connection_id: uuid.UUID) -> None:
    """Idempotent: schedules, an immediate sync, and the default backfill if never started."""
    await temporal.ensure_schedules(org_id, connection_id)
    await temporal.start_sync(org_id, connection_id, deferred=True)
    await maybe_start_backfill(org_id, connection_id)


async def maybe_start_backfill(org_id: uuid.UUID, connection_id: uuid.UUID) -> str | None:
    async with scoped_session(org_id) as db:
        state = await sync._load_state(db, org_id, connection_id)
    if not state.linked_external_ids:
        return None
    started = state.streams.get("orders")
    if started is not None and started.backfill_target_from is not None:
        return None
    tz, day_start = state.zone
    today = business_date(datetime.now(UTC), tz, day_start)
    return await temporal.start_backfill(
        org_id,
        connection_id,
        today - timedelta(days=BACKFILL_DAYS - 1),
        today,
        reuse_running=True,
    )


# ---------------------------------------------------------------- serialization


async def get_live_connection(db: AsyncSession, connection_id: uuid.UUID) -> IntegrationConnection:
    conn = await db.get(IntegrationConnection, connection_id)
    if conn is None or conn.status == "disconnected":
        raise not_found("Connection not found")
    return conn


async def connection_out(
    db: AsyncSession, conn: IntegrationConnection, now: datetime | None = None
) -> ConnectionOut:
    now = now or datetime.now(UTC)
    link_rows = list(
        await db.scalars(
            select(IntegrationLocationLink)
            .where(IntegrationLocationLink.connection_id == conn.id)
            .order_by(IntegrationLocationLink.external_name)
        )
    )
    stream_rows = {
        s.stream: s
        for s in await db.scalars(
            select(IntegrationStream).where(IntegrationStream.connection_id == conn.id)
        )
    }
    any_linked = any(link.location_id for link in link_rows)
    health: list[StreamHealthOut] = []
    for name in STREAMS:
        s = stream_rows.get(name)
        status = freshness.stream_health(s, conn.status, now)
        if name in ("orders", "timecards") and not any_linked:
            status = "pending"
        health.append(
            StreamHealthOut(
                stream=name,
                status=status,
                data_as_of=s.data_as_of if s else None,
                last_success_at=s.last_success_at if s else None,
                last_error=s.last_error_detail if s else None,
                backfill_target_from=s.backfill_target_from if s else None,
                backfill_completed_through=s.backfill_completed_through if s else None,
            )
        )
    return ConnectionOut(
        id=conn.id,
        provider="square",
        mode=conn.mode,
        status=conn.status,
        external_account_id=conn.external_account_id,
        created_at=conn.created_at,
        location_links=[
            LocationLinkOut(
                external_location_id=link.external_location_id,
                external_name=link.external_name,
                location_id=link.location_id,
            )
            for link in link_rows
        ],
        streams=health,
    )


async def connection_detail(db: AsyncSession, conn: IntegrationConnection) -> ConnectionDetailOut:
    base = await connection_out(db, conn)
    runs = await db.scalars(
        select(SyncRun)
        .where(SyncRun.connection_id == conn.id)
        .order_by(SyncRun.started_at.desc())
        .limit(20)
    )
    return ConnectionDetailOut(
        **base.model_dump(),
        recent_runs=[
            SyncRunOut(
                id=r.id,
                stream=r.stream,
                kind=r.kind,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                fetched_count=r.fetched_count,
                changed_count=r.changed_count,
                error_detail=r.error_detail,
            )
            for r in runs
        ],
    )


# ---------------------------------------------------------------- links, disconnect, issues


async def patch_links(
    org_id: uuid.UUID, connection_id: uuid.UUID, patch: list[tuple[str, uuid.UUID | None]]
) -> bool:
    """Apply link changes. Returns True if any location became newly linked."""
    newly = False
    async with scoped_session(org_id) as db:
        await get_live_connection(db, connection_id)
        rows = {
            r.external_location_id: r
            for r in await db.scalars(
                select(IntegrationLocationLink).where(
                    IntegrationLocationLink.connection_id == connection_id
                )
            )
        }
        wanted = {ext: loc for ext, loc in patch}
        unknown = sorted(set(wanted) - set(rows))
        if unknown:
            raise validation_error({"links": f"Unknown Square location: {unknown[0]}"})
        final = {ext: wanted.get(ext, r.location_id) for ext, r in rows.items()}
        targets = [v for v in final.values() if v is not None]
        if len(targets) != len(set(targets)):
            raise validation_error({"links": "A location can be linked to one Square location"})
        for ext, loc_id in wanted.items():
            loc = None
            if loc_id is not None:
                loc = await db.get(Location, loc_id)
                if loc is None or loc.archived_at is not None:
                    raise validation_error({"links": "Location not found"})
            if rows[ext].location_id is None and loc is not None:
                newly = True
            await links.apply_link(db, rows[ext], loc)
    return newly


async def disconnect(org_id: uuid.UUID, connection_id: uuid.UUID) -> None:
    async with scoped_session(org_id) as db:
        conn = await get_live_connection(db, connection_id)
        creds = None
        if conn.credential_ciphertext is not None and conn.status != "pending":
            from app.integrations.secrets import decrypt_credentials

            creds = decrypt_credentials(conn.credential_ciphertext, org_id, conn.id)
        conn.status = "disconnected"
        conn.disconnected_at = datetime.now(UTC)
        conn.credential_ciphertext = None  # tokens are revoked; data stays, labeled by source
    if creds is not None:
        try:
            await _blocking(lambda: get_connector().revoke(creds))
        except SquareError as exc:
            log.warning("revoke_failed", error=str(exc))
    try:
        await temporal.delete_schedules(connection_id)
        await temporal.terminate_backfill(connection_id)
    except (OSError, RuntimeError) as exc:
        log.warning("disconnect_cleanup_failed", error=str(exc))


async def list_issues(db: AsyncSession, status: str) -> list[MappingIssueOut]:
    stmt = select(MappingIssue).order_by(MappingIssue.last_seen_at.desc(), MappingIssue.id)
    stmt = stmt.where(
        MappingIssue.resolved_at.is_(None)
        if status == "open"
        else MappingIssue.resolved_at.isnot(None)
    )
    return [_issue_out(i) for i in await db.scalars(stmt.limit(500))]


def _issue_out(i: MappingIssue) -> MappingIssueOut:
    return MappingIssueOut(
        id=i.id,
        kind=i.kind,
        external_id=i.external_id,
        label=i.label,
        occurrences=i.occurrences,
        first_seen_at=i.first_seen_at,
        last_seen_at=i.last_seen_at,
        resolved_at=i.resolved_at,
    )


async def resolve_issue_ignore(
    db: AsyncSession, issue_id: uuid.UUID, user_id: uuid.UUID
) -> MappingIssueOut:
    issue = await db.get(MappingIssue, issue_id)
    if issue is None:
        raise not_found("Mapping issue not found")
    if issue.resolved_at is None:
        issue.resolved_at = datetime.now(UTC)
        issue.resolved_by = user_id
    await db.flush()
    return _issue_out(issue)


# ---------------------------------------------------------------- webhooks


async def handle_webhook(headers: Mapping[str, str], body: bytes) -> None:
    """Verify, dedupe, and enqueue a targeted fetch. Webhooks only trigger pulls."""
    settings = get_settings()
    if settings.square_mode != "sandbox":
        raise not_found()
    connector = get_connector()
    if not connector.verify_webhook(headers, body, settings.webhook_url):
        raise ProblemError(401, "unauthenticated", "Invalid webhook signature")
    try:
        event = connector.parse_webhook(body)
    except ValueError as exc:
        raise validation_error({"body": "Malformed webhook"}) from exc
    stream = connector.stream_for_event(event)
    for connection_id, org_id in await lookup_connection_owners(PROVIDER, event.merchant_id):
        async with scoped_session(org_id) as db:
            inserted = await db.scalar(
                pg_insert(WebhookEvent)
                .values(
                    org_id=org_id,
                    provider=PROVIDER,
                    event_id=event.event_id,
                    connection_id=connection_id,
                    event_type=event.type,
                    received_at=datetime.now(UTC),
                    processed_at=None if stream else datetime.now(UTC),
                )
                .on_conflict_do_nothing()
                .returning(WebhookEvent.event_id)
            )
            if inserted is not None and stream is not None:
                ids = [event.object_id] if event.object_id else []
                try:
                    await temporal.start_webhook_fetch(
                        org_id, connection_id, stream, ids, event.event_id
                    )
                except (OSError, RuntimeError) as exc:  # rolls back the dedupe row: Square retries
                    raise ProblemError(503, "unavailable", "Workflow engine unavailable") from exc
