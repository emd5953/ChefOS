"""B5 push API: key lifecycle, scoped bearer auth, idempotency, source conflicts, validation, RLS."""

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import scoped_session
from app.errors import ProblemError
from app.integrations import service
from app.integrations.freshness import location_freshness
from app.integrations.models import IntegrationConnection, IntegrationLocationLink
from app.main import app
from tests.conftest import SignupFn

DAY = "2026-03-07"
CLOSED = "2026-03-07T12:00:00-05:00"


@pytest_asyncio.fixture
async def external() -> AsyncIterator[httpx.AsyncClient]:
    """A push client: no session cookie, only a bearer key."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _key(acct: dict[str, Any], scopes: list[str], **body: Any) -> dict[str, Any]:
    body.setdefault("location_id", acct["location"]["id"])
    r = await acct["client"].post(
        "/v1/api-keys", headers=acct["headers"], json={"scopes": scopes, **body}
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def _auth(key: dict[str, Any], idem: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key['secret']}",
        "Idempotency-Key": idem or uuid.uuid4().hex,
    }


def _daily(location_id: str, net: int = 15250, orders: int = 12, **extra: Any) -> dict[str, Any]:
    total = {"business_date": DAY, "net_sales": net, "orders": orders, **extra}
    return {"location_id": location_id, "totals": [total]}


def _orders(location_id: str, **record: Any) -> dict[str, Any]:
    return {
        "location_id": location_id,
        "records": [{"id": "t-1", "closed_at": CLOSED, "net_sales": 1200, **record}],
    }


async def _day_totals(org_id: str, location_id: str) -> tuple[int | None, int | None]:
    async with scoped_session(uuid.UUID(org_id)) as db:
        row = (
            await db.execute(
                text(
                    "SELECT sum(net_sales), sum(orders) FROM daily_sales "
                    "WHERE location_id = :l AND business_date = :d"
                ),
                {"l": location_id, "d": datetime.fromisoformat(DAY).date()},
            )
        ).one()
    return row[0], row[1]


async def test_per_scope_authorization_and_location_binding(
    acct: dict[str, Any], external: httpx.AsyncClient, signup: SignupFn, owner_engine: AsyncEngine
) -> None:
    loc = acct["location"]["id"]
    daily_only = await _key(acct, ["daily_sales:write"])
    orders_only = await _key(acct, ["orders:write"])

    r = await external.post("/v1/ingest/orders", headers=_auth(daily_only), json=_orders(loc))
    assert r.status_code == 403
    r = await external.post("/v1/ingest/daily-sales", headers=_auth(orders_only), json=_daily(loc))
    assert r.status_code == 403
    r = await external.post("/v1/ingest/orders", headers=_auth(orders_only), json=_orders(loc))
    assert r.status_code == 200, r.text
    r = await external.post("/v1/ingest/daily-sales", headers=_auth(daily_only), json=_daily(loc))
    assert r.status_code == 200, r.text

    # A bound key cannot push for another location of its own org; an unbound key can.
    other = await acct["client"].post(
        "/v1/locations",
        headers=acct["headers"],
        json={"name": "Second", "timezone": "America/New_York"},
    )
    assert other.status_code == 201, other.text
    other_id = other.json()["id"]
    r = await external.post(
        "/v1/ingest/daily-sales", headers=_auth(daily_only), json=_daily(other_id)
    )
    assert r.status_code == 404
    org_wide = await _key(acct, ["daily_sales:write"], location_id=None)
    r = await external.post(
        "/v1/ingest/daily-sales", headers=_auth(org_wide), json=_daily(other_id)
    )
    assert r.status_code == 200, r.text
    async with scoped_session(uuid.UUID(acct["org"]["id"])) as db:
        fresh = {f.stream: f.status for f in await location_freshness(db, uuid.UUID(other_id))}
    assert fresh["orders"] == "fresh"  # the second location got its own push link

    # Missing or malformed credentials.
    payload = _daily(loc)
    for headers in (
        {"Idempotency-Key": "x"},
        {"Authorization": "Basic abc", "Idempotency-Key": "x"},
        {"Authorization": "Bearer chef_not-a-real-key", "Idempotency-Key": "x"},
    ):
        r = await external.post("/v1/ingest/daily-sales", headers=headers, json=payload)
        assert r.status_code == 401

    # Key lifecycle is owner/admin only.
    gm = await signup()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE memberships SET role = 'manager' WHERE org_id = :o"),
            {"o": gm["org"]["id"]},
        )
    r = await gm["client"].post(
        "/v1/api-keys",
        headers=gm["headers"],
        json={"scopes": ["orders:write"], "location_id": gm["location"]["id"]},
    )
    assert r.status_code == 403
    assert (await gm["client"].get("/v1/api-keys")).status_code == 403
    await gm["client"].aclose()


async def test_revoked_and_expired_keys(
    acct: dict[str, Any], external: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    loc = acct["location"]["id"]
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    r = await acct["client"].post(
        "/v1/api-keys",
        headers=acct["headers"],
        json={"scopes": ["daily_sales:write"], "location_id": loc, "expires_at": past},
    )
    assert r.status_code == 422

    revoked = await _key(acct, ["daily_sales:write"])
    r = await acct["client"].delete(f"/v1/api-keys/{revoked['id']}", headers=acct["headers"])
    assert r.status_code == 200 and r.json()["revoked_at"] is not None
    r = await external.post("/v1/ingest/daily-sales", headers=_auth(revoked), json=_daily(loc))
    assert r.status_code == 401

    soon = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    expiring = await _key(acct, ["daily_sales:write"], expires_at=soon)
    r = await external.post("/v1/ingest/daily-sales", headers=_auth(expiring), json=_daily(loc))
    assert r.status_code == 200, r.text
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE api_keys SET expires_at = now() - interval '1 second' WHERE id = :i"),
            {"i": expiring["id"]},
        )
    r = await external.post("/v1/ingest/daily-sales", headers=_auth(expiring), json=_daily(loc))
    assert r.status_code == 401

    async with scoped_session(uuid.UUID(acct["org"]["id"])) as db:
        actions = list(
            await db.scalars(
                text(
                    "SELECT action FROM audit_events WHERE entity_type = 'api_key' "
                    "AND entity_id = :i ORDER BY created_at"
                ),
                {"i": revoked["id"]},
            )
        )
    assert actions == ["api_key.create", "api_key.revoke"]


async def test_keys_are_hashed_and_shown_once(
    acct: dict[str, Any], owner_engine: AsyncEngine
) -> None:
    key = await _key(acct, ["orders:write", "daily_sales:write"])
    secret = key["secret"]
    assert secret.startswith("chef_") and len(secret) >= 45
    assert key["prefix"] == secret[:12]
    async with owner_engine.begin() as conn:
        row = (
            await conn.execute(text("SELECT * FROM api_keys WHERE id = :i"), {"i": key["id"]})
        ).one()
        audit = await conn.scalar(
            text("SELECT string_agg(details::text, ' ') FROM audit_events WHERE entity_id = :i"),
            {"i": key["id"]},
        )
    assert row.key_hash == hashlib.sha256(secret.encode()).hexdigest()
    assert all(secret not in str(v) for v in row)
    assert secret not in (audit or "")
    listing = await acct["client"].get("/v1/api-keys", headers=acct["headers"])
    assert listing.status_code == 200
    assert secret not in listing.text and row.key_hash not in listing.text
    assert "secret" not in listing.json()["data"][0]


async def test_idempotent_replay(acct: dict[str, Any], external: httpx.AsyncClient) -> None:
    org, loc = acct["org"]["id"], acct["location"]["id"]
    key = await _key(acct, ["daily_sales:write", "orders:write"])
    headers = _auth(key, "b5-daily-1")
    first = await external.post("/v1/ingest/daily-sales", headers=headers, json=_daily(loc))
    assert first.status_code == 200, first.text
    assert first.json()["freshness"]["status"] == "fresh"
    assert await _day_totals(org, loc) == (15250, 12)

    replay = await external.post("/v1/ingest/daily-sales", headers=headers, json=_daily(loc))
    assert replay.status_code == 200 and replay.json() == first.json()
    assert await _day_totals(org, loc) == (15250, 12)

    changed = await external.post(
        "/v1/ingest/daily-sales", headers=headers, json=_daily(loc, net=16000)
    )
    assert changed.status_code == 409
    other_endpoint = await external.post("/v1/ingest/orders", headers=headers, json=_orders(loc))
    assert other_endpoint.status_code == 409

    # A correction under a new key replaces the day, including fewer orders than before.
    fixed = await external.post(
        "/v1/ingest/daily-sales", headers=_auth(key), json=_daily(loc, net=9000, orders=5)
    )
    assert fixed.status_code == 200, fixed.text
    assert await _day_totals(org, loc) == (9000, 5)

    # Push goes through the canonical pipeline and is labeled as its own source.
    async with scoped_session(uuid.UUID(org)) as db:
        sources = set(
            await db.scalars(
                text("SELECT DISTINCT source FROM sales_orders WHERE location_id = :l"), {"l": loc}
            )
        )
        open_issues = await db.scalar(
            text("SELECT count(*) FROM mapping_issues WHERE resolved_at IS NULL")
        )
    assert sources == {"push"} and open_issues == 0
    # The push connection never breaks or masquerades in the Square connection list.
    conns = await acct["client"].get("/v1/integrations/connections")
    assert conns.status_code == 200 and conns.json()["data"] == []


async def test_source_conflict_with_active_connector(
    acct: dict[str, Any], external: httpx.AsyncClient
) -> None:
    org_id, loc = uuid.UUID(acct["org"]["id"]), acct["location"]["id"]
    key = await _key(acct, ["daily_sales:write"], location_id=None)
    async with scoped_session(org_id) as db:
        square = IntegrationConnection(org_id=org_id, provider="square", status="active")
        db.add(square)
        await db.flush()
        db.add(
            IntegrationLocationLink(
                org_id=org_id,
                connection_id=square.id,
                external_location_id="SQ-MAIN",
                external_name="Main",
                location_id=uuid.UUID(loc),
                timezone="America/New_York",
            )
        )
        db.add(
            IntegrationLocationLink(
                org_id=org_id,
                connection_id=square.id,
                external_location_id="SQ-OTHER",
                external_name="Other",
                timezone="America/New_York",
            )
        )
    r = await external.post("/v1/ingest/daily-sales", headers=_auth(key), json=_daily(loc))
    assert r.status_code == 409

    # A location with no connector accepts push, and Square cannot then be linked to it.
    other = await acct["client"].post(
        "/v1/locations",
        headers=acct["headers"],
        json={"name": "Second", "timezone": "America/New_York"},
    )
    other_id = other.json()["id"]
    r = await external.post("/v1/ingest/daily-sales", headers=_auth(key), json=_daily(other_id))
    assert r.status_code == 200, r.text
    with pytest.raises(ProblemError) as exc:
        await service.patch_links(org_id, square.id, [("SQ-OTHER", uuid.UUID(other_id))])
    assert exc.value.status == 409


@pytest.mark.parametrize(
    ("path", "body", "headers"),
    [
        ("daily-sales", lambda loc: _daily(loc, net=-1), {}),
        ("daily-sales", lambda loc: _daily(loc, net=10**12), {}),
        ("daily-sales", lambda loc: _daily(loc, net=500, orders=0), {}),
        ("daily-sales", lambda loc: _daily(loc, currency="usd"), {}),
        ("daily-sales", lambda loc: _daily(loc, business_date="2999-01-01"), {}),
        ("daily-sales", lambda loc: {**_daily(loc), "org_id": str(uuid.uuid4())}, {}),
        (
            "daily-sales",
            lambda loc: {"location_id": loc, "totals": _daily(loc)["totals"] * 2},
            {},
        ),
        (
            "daily-sales",
            lambda loc: {
                "location_id": loc,
                "totals": [
                    {"business_date": f"2025-01-{d:02d}", "net_sales": 1, "orders": 1000}
                    for d in range(1, 7)
                ],
            },
            {},
        ),
        ("daily-sales", lambda loc: _daily(loc), {"Idempotency-Key": ""}),
        ("orders", lambda loc: _orders(loc, closed_at="2026-03-07T12:00:00"), {}),
        ("orders", lambda loc: _orders(loc, closed_at="2999-01-01T00:00:00Z"), {}),
        ("orders", lambda loc: _orders(loc, id="a:b"), {}),
        (
            "orders",
            lambda loc: {
                "location_id": loc,
                "records": [
                    {"id": str(i), "closed_at": CLOSED, "net_sales": 1} for i in range(501)
                ],
            },
            {},
        ),
        (
            "orders",
            lambda loc: {
                "location_id": loc,
                "records": [
                    {"id": "a", "closed_at": CLOSED, "net_sales": 1, "currency": "USD"},
                    {"id": "b", "closed_at": CLOSED, "net_sales": 1, "currency": "EUR"},
                ],
            },
            {},
        ),
    ],
)
async def test_invalid_payload(
    acct: dict[str, Any],
    external: httpx.AsyncClient,
    path: str,
    body: Any,
    headers: dict[str, str],
) -> None:
    key = await _key(acct, ["orders:write", "daily_sales:write"])
    r = await external.post(
        f"/v1/ingest/{path}", headers={**_auth(key), **headers}, json=body(acct["location"]["id"])
    )
    assert r.status_code == 422, r.text
    assert await _day_totals(acct["org"]["id"], acct["location"]["id"]) == (None, None)


async def test_rls_isolation_across_orgs(signup: SignupFn, external: httpx.AsyncClient) -> None:
    a, b = await signup(), await signup()
    key_a = await _key(a, ["daily_sales:write"], location_id=None)
    key_b = await _key(b, ["daily_sales:write"], location_id=None)
    r = await external.post(
        "/v1/ingest/daily-sales", headers=_auth(key_a, "shared"), json=_daily(a["location"]["id"])
    )
    assert r.status_code == 200, r.text

    # B's key cannot reach A's location, and the same Idempotency-Key in B is independent.
    r = await external.post(
        "/v1/ingest/daily-sales", headers=_auth(key_b, "shared"), json=_daily(a["location"]["id"])
    )
    assert r.status_code == 404
    r = await external.post(
        "/v1/ingest/daily-sales",
        headers=_auth(key_b, "shared"),
        json=_daily(b["location"]["id"], net=777, orders=1),
    )
    assert r.status_code == 200, r.text
    assert await _day_totals(a["org"]["id"], a["location"]["id"]) == (15250, 12)

    # B's admin cannot list, revoke, or bind keys to A's resources.
    listing = await b["client"].get("/v1/api-keys")
    assert [k["id"] for k in listing.json()["data"]] == [key_b["id"]]
    r = await b["client"].delete(f"/v1/api-keys/{key_a['id']}", headers=b["headers"])
    assert r.status_code == 404
    r = await b["client"].post(
        "/v1/api-keys",
        headers=b["headers"],
        json={"scopes": ["orders:write"], "location_id": a["location"]["id"]},
    )
    assert r.status_code == 404

    org_b = uuid.UUID(b["org"]["id"])
    for table in ("api_keys", "push_idempotency"):
        async with scoped_session(org_b) as db:
            seen = await db.scalar(
                text(f"SELECT count(*) FROM {table} WHERE org_id = :o"),
                {"o": a["org"]["id"]},
            )
            assert seen == 0, table
            updated = await db.execute(
                text(f"UPDATE {table} SET org_id = org_id WHERE org_id = :o"),
                {"o": a["org"]["id"]},
            )
            assert updated.rowcount == 0, table  # type: ignore[attr-defined]
    for c in (a["client"], b["client"]):
        await c.aclose()
