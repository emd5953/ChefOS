"""Push ingestion: an external totals-only sales client sends sales with an identity-owned API key,
and they land through the shared integration pipeline like any connector's orders.

The key decides the org, never the payload: the bearer secret's SHA-256 resolves to (key, org)
through a narrow SECURITY DEFINER lookup, and everything after runs in that org's RLS scope. A
key bound to a location may push only for it; an unbound key may push for any active location
in its org. Pushed totals become synthetic one-line tickets ("Unitemized sales") so read models,
item mix and freshness treat them exactly like synced orders.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Depends, Header, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.business_date import business_date, get_zone
from app.db import lookup_api_key, scoped_session
from app.errors import ProblemError, forbidden, not_found, validation_error
from app.identity.models import ApiKey, Location
from app.identity.service import hash_api_key
from app.integrations import pipeline
from app.integrations.links import Link
from app.integrations.models import (
    IntegrationConnection,
    IntegrationLocationLink,
    IntegrationStream,
)
from app.integrations.providers.square.types import RawItem
from app.integrations.readmodels import Pair, rebuild_daily_sales

router = APIRouter(prefix="/v1", tags=["push ingestion"])

PROVIDER = "push"
MAX_TICKETS = 5000  # synthetic tickets per request, after expanding order counts
MAX_MINOR = 10**11  # per record, in minor units
_FUTURE_SKEW = timedelta(minutes=10)
_ID_PATTERN = r"^[A-Za-z0-9._-]+$"  # no ':' so ticket-id prefixes cannot collide
_BAD_KEY = ProblemError(401, "unauthenticated", "Invalid API key")

Scope = Literal["orders:write", "daily_sales:write"]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[ -~]+$")
]


async def _csrf_for_cookie(request: Request) -> None:
    """When a browser session is present, honor the app-wide unsafe-method CSRF rule.

    External callers authenticate with bearer keys and do not carry a session cookie.
    """
    if request.cookies.get("chefos_session"):
        from app.security.sessions import require_auth

        await require_auth(request)


class PushOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Annotated[str, Field(min_length=1, max_length=120, pattern=_ID_PATTERN)]
    closed_at: AwareDatetime
    net_sales: Annotated[int, Field(ge=0, le=MAX_MINOR)]
    orders: Annotated[int, Field(ge=1, le=1000)] = 1
    currency: Currency = "USD"


class DailySales(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_date: date
    net_sales: Annotated[int, Field(ge=0, le=MAX_MINOR)]
    orders: Annotated[int, Field(ge=0, le=1000)] = 0
    currency: Currency = "USD"

    @model_validator(mode="after")
    def _orders_when_sales(self) -> Self:
        if self.orders == 0 and self.net_sales:
            raise ValueError("orders must be positive when net_sales is nonzero")
        return self


def _one_currency(currencies: set[str]) -> None:
    if len(currencies) > 1:
        raise ValueError("a batch must use one currency")


class PushOrders(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: uuid.UUID
    records: Annotated[list[PushOrder], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len({r.id for r in self.records}) != len(self.records):
            raise ValueError("record ids must be unique within a batch")
        _one_currency({r.currency for r in self.records})
        return self


class PushDaily(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: uuid.UUID
    totals: Annotated[list[DailySales], Field(min_length=1, max_length=400)]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len({t.business_date for t in self.totals}) != len(self.totals):
            raise ValueError("business dates must be unique within a batch")
        _one_currency({t.currency for t in self.totals})
        return self


@dataclass(frozen=True)
class _Group:
    """One pushed record: `count` tickets `<prefix><n>` that together carry `net_sales`.

    A re-push replaces the whole group, so tickets beyond the new count are removed."""

    prefix: str
    closed_at: datetime
    net_sales: int
    count: int
    currency: str


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise ProblemError(401, "unauthenticated", "API key required")
    return token


async def _key(request: Request) -> tuple[uuid.UUID, uuid.UUID]:
    hit = await lookup_api_key(hash_api_key(_bearer(request)))
    if hit is None:
        raise _BAD_KEY
    return hit


def _request_hash(scope: str, location_id: uuid.UUID, body: BaseModel) -> str:
    blob = json.dumps(
        {"scope": scope, "location_id": str(location_id), "body": body.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()


async def _authorize(
    db: AsyncSession, key_id: uuid.UUID, scope: str, location_id: uuid.UUID, now: datetime
) -> tuple[ApiKey, Location]:
    """Re-check the key inside the request's own transaction, then its scope and binding."""
    key = await db.get(ApiKey, key_id)
    if (
        key is None
        or key.revoked_at is not None
        or (key.expires_at is not None and key.expires_at <= now)
    ):
        raise _BAD_KEY
    if scope not in key.scopes:
        raise forbidden("API key does not grant this scope")
    if key.location_id is not None and key.location_id != location_id:
        raise not_found("Location not found")
    location = await db.scalar(
        select(Location).where(
            Location.org_id == key.org_id,
            Location.id == location_id,
            Location.archived_at.is_(None),
        )
    )
    if location is None:
        raise not_found("Location not found")
    return key, location


async def _check_source(db: AsyncSession, location_id: uuid.UUID) -> None:
    """One location has one sales source: refuse when a live connector feeds it."""
    other = await db.scalar(
        select(IntegrationConnection.provider)
        .join(
            IntegrationLocationLink,
            IntegrationLocationLink.connection_id == IntegrationConnection.id,
        )
        .where(
            IntegrationLocationLink.location_id == location_id,
            IntegrationLocationLink.active.is_(True),
            IntegrationConnection.provider != PROVIDER,
            IntegrationConnection.status != "disconnected",
        )
        .limit(1)
    )
    if other is not None:
        raise ProblemError(
            409, "conflict", f"This location's sales already come from {other}; push is refused"
        )


async def _push_connection(
    db: AsyncSession, org_id: uuid.UUID, location: Location
) -> IntegrationConnection:
    """The org's push connection (reactivated if it was disconnected) with a link for the
    location, so freshness and connection-scoped ticket ids work like any connector's."""
    conn = await db.scalar(
        select(IntegrationConnection)
        .where(IntegrationConnection.org_id == org_id, IntegrationConnection.provider == PROVIDER)
        .order_by(
            (IntegrationConnection.status == "disconnected").asc(),
            IntegrationConnection.created_at.desc(),
        )
        .limit(1)
    )
    if conn is None:
        conn = IntegrationConnection(org_id=org_id, provider=PROVIDER, mode=PROVIDER)
        db.add(conn)
    # Reusing a disconnected connection keeps ticket ids stable, so totals are never doubled.
    conn.status = "active"
    conn.disconnected_at = None
    await db.flush()
    link = await db.scalar(
        select(IntegrationLocationLink).where(
            IntegrationLocationLink.connection_id == conn.id,
            IntegrationLocationLink.external_location_id == str(location.id),
        )
    )
    if link is None:
        link = IntegrationLocationLink(
            org_id=org_id, connection_id=conn.id, external_location_id=str(location.id)
        )
        db.add(link)
    link.external_name = location.name
    link.location_id = location.id
    link.timezone = location.timezone
    link.business_day_start = location.business_day_start
    link.active = True
    if await db.get(IntegrationStream, (conn.id, "orders")) is None:
        db.add(IntegrationStream(org_id=org_id, connection_id=conn.id, stream="orders"))
    await db.flush()
    return conn


def _tickets(location_id: uuid.UUID, groups: list[_Group], now: datetime) -> list[RawItem]:
    version = int(now.timestamp() * 1_000_000)
    items: list[RawItem] = []
    for g in groups:
        quotient, remainder = divmod(g.net_sales, max(g.count, 1))
        for n in range(g.count):
            amount = {"amount": quotient + (n < remainder), "currency": g.currency}
            # The push connection is shared by all locations in the org. A date or caller's
            # record ID alone lets a location-bound key overwrite another location's sales.
            external_id = f"{location_id}:{g.prefix}{n + 1}"
            payload = {
                "id": external_id,
                "location_id": str(location_id),
                "updated_at": now.isoformat(),
                "closed_at": g.closed_at.isoformat(),
                "created_at": g.closed_at.isoformat(),
                "version": version,
                "source": {"name": "ChefOS Push"},
                "fulfillments": [],
                "line_items": [
                    {
                        "uid": "total",
                        "name": "Unitemized sales",
                        "quantity": "1",
                        "gross_sales_money": amount,
                        "base_price_money": amount,
                    }
                ],
                "total_money": amount,
            }
            items.append(RawItem(external_id, now, str(version), payload))
    return items


_GROUP_ORDERS = text("""
SELECT o.id, o.external_id, o.location_id, o.business_date FROM sales_orders o
WHERE o.connection_id = :conn
  AND o.location_id = :loc
  AND EXISTS (SELECT 1 FROM unnest(CAST(:prefixes AS text[])) p WHERE starts_with(o.external_id, p))
""")


async def _drop_stale(
    db: AsyncSession,
    conn_id: uuid.UUID,
    location_id: uuid.UUID,
    groups: list[_Group],
    keep: set[str],
) -> set[Pair]:
    """Delete earlier tickets of re-pushed groups that the new counts no longer include."""
    rows = await db.execute(
        _GROUP_ORDERS,
        {
            "conn": conn_id,
            "loc": location_id,
            # Replace legacy IDs too, but only inside the authorized location. Re-pushing
            # a group upgrades it atomically without counting both old and new tickets.
            "prefixes": [p for g in groups for p in (g.prefix, f"{location_id}:{g.prefix}")],
        },
    )
    stale = [r for r in rows if r.external_id not in keep]
    if not stale:
        return set()
    ids = [r.id for r in stale]
    await db.execute(text("DELETE FROM sales_order_lines WHERE order_id = ANY(:ids)"), {"ids": ids})
    await db.execute(
        text("DELETE FROM sales_order_returns WHERE order_id = ANY(:ids)"), {"ids": ids}
    )
    await db.execute(text("DELETE FROM sales_orders WHERE id = ANY(:ids)"), {"ids": ids})
    return {(r.location_id, r.business_date) for r in stale}


async def _ingest(
    request: Request,
    scope: Scope,
    location_id: uuid.UUID,
    body: BaseModel,
    groups_for: Any,
    idempotency_key: str,
) -> dict[str, Any]:
    key_id, org_id = await _key(request)
    now = datetime.now(UTC)
    async with scoped_session(org_id) as db:
        key, location = await _authorize(db, key_id, scope, location_id, now)
        # Serialize pushes per org: idempotency check-then-insert and the push connection's
        # creation must not race.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"push:{org_id}"},
        )
        request_hash = _request_hash(scope, location_id, body)
        prior = (
            await db.execute(
                text(
                    "SELECT request_hash, response FROM push_idempotency "
                    "WHERE org_id = :o AND idempotency_key = :k"
                ),
                {"o": org_id, "k": idempotency_key},
            )
        ).first()
        if prior is not None:
            if prior.request_hash != request_hash:
                raise ProblemError(
                    409, "conflict", "Idempotency-Key was used with a different request"
                )
            return dict(prior.response)
        await _check_source(db, location.id)
        groups: list[_Group] = groups_for(location, now)
        if sum(g.count for g in groups) > MAX_TICKETS:
            raise validation_error({"body": f"A push may expand to at most {MAX_TICKETS} orders"})
        currency = await db.scalar(
            text(
                "SELECT currency FROM daily_sales WHERE location_id = :l "
                "ORDER BY business_date DESC LIMIT 1"
            ),
            {"l": location.id},
        )
        if currency is not None and groups[0].currency != currency:
            raise validation_error({"currency": f"This location reports sales in {currency}"})

        conn = await _push_connection(db, org_id, location)
        items = _tickets(location.id, groups, now)
        stale_pairs = await _drop_stale(
            db, conn.id, location.id, groups, {i.external_id for i in items}
        )
        ctx = pipeline.ConnCtx(
            org_id,
            conn.id,
            {
                str(location.id): Link(
                    str(location.id),
                    location.name,
                    location.id,
                    location.timezone,
                    location.business_day_start,
                )
            },
        )
        result = await pipeline.ingest(db, ctx, "orders", items, None, now)
        await db.execute(
            text("UPDATE sales_orders SET source = :p WHERE connection_id = :c AND source <> :p"),
            {"p": PROVIDER, "c": conn.id},
        )
        pairs = {
            (
                location.id,
                business_date(g.closed_at, location.timezone, location.business_day_start),
            )
            for g in groups
        }
        await rebuild_daily_sales(db, org_id, pairs | stale_pairs, now)
        # Synthetic tickets have no catalog item or known channel by design; those mapping
        # signals are expected, not work for an admin.
        await db.execute(
            text(
                "UPDATE mapping_issues SET resolution = 'ignored', resolved_at = :now "
                "WHERE connection_id = :c AND resolved_at IS NULL"
            ),
            {"now": now, "c": conn.id},
        )
        stream = await db.get_one(IntegrationStream, (conn.id, "orders"))
        stream.data_as_of = now
        stream.last_attempt_at = now
        stream.last_success_at = now
        stream.last_error_class = None
        stream.last_error_detail = None
        stream.consecutive_failures = 0
        response = {
            "accepted": len(items),
            "applied": result.applied,
            "location_id": str(location.id),
            "freshness": {
                "stream": "orders",
                "location_id": str(location.id),
                "data_as_of": now.isoformat(),
                "status": "fresh",
            },
        }
        await db.execute(
            text(
                "INSERT INTO push_idempotency "
                "(id, org_id, api_key_id, idempotency_key, request_hash, response) "
                "VALUES (gen_random_uuid(), :o, :kid, :key, :hash, CAST(:response AS jsonb))"
            ),
            {
                "o": org_id,
                "kid": key.id,
                "key": idempotency_key,
                "hash": request_hash,
                "response": json.dumps(response),
            },
        )
        await audit.record(
            db,
            org_id=org_id,
            actor_user_id=None,
            entity_type="api_key",
            entity_id=key.id,
            action="push.ingest",
            location_id=location.id,
            details={"prefix": key.prefix, "scope": scope, "accepted": len(items)},
        )
        return response


def _day_midpoint(day: date, tz: str, day_start: time) -> datetime:
    """An instant inside business day `day` (12h after it opens), whatever the day start."""
    opens = datetime.combine(day, day_start, tzinfo=get_zone(tz))
    return (opens + timedelta(hours=12)).astimezone(UTC)


@router.post("/ingest/orders", dependencies=[Depends(_csrf_for_cookie)])
async def push_orders(
    body: PushOrders, request: Request, idempotency_key: IdempotencyKey
) -> dict[str, Any]:
    def groups(location: Location, now: datetime) -> list[_Group]:
        late = [r.id for r in body.records if r.closed_at > now + _FUTURE_SKEW]
        if late:
            raise validation_error({"closed_at": f"Order {late[0]} closes in the future"})
        return [
            _Group(f"order:{r.id}:", r.closed_at.astimezone(UTC), r.net_sales, r.orders, r.currency)
            for r in body.records
        ]

    return await _ingest(request, "orders:write", body.location_id, body, groups, idempotency_key)


@router.post("/ingest/daily-sales", dependencies=[Depends(_csrf_for_cookie)])
async def push_daily_sales(
    body: PushDaily, request: Request, idempotency_key: IdempotencyKey
) -> dict[str, Any]:
    def groups(location: Location, now: datetime) -> list[_Group]:
        today = business_date(now, location.timezone, location.business_day_start)
        late = [t.business_date for t in body.totals if t.business_date > today]
        if late:
            raise validation_error({"business_date": f"{late[0]} is in the future"})
        return [
            _Group(
                f"daily:{t.business_date.isoformat()}:",
                _day_midpoint(t.business_date, location.timezone, location.business_day_start),
                t.net_sales,
                t.orders,
                t.currency,
            )
            for t in body.totals
        ]

    return await _ingest(
        request, "daily_sales:write", body.location_id, body, groups, idempotency_key
    )
