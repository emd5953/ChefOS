"""Integrations HTTP API: OAuth flow, links, sync/backfill triggers, issues, disconnect, webhooks."""

import json
import uuid
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db import scoped_session
from app.integrations import providers
from app.integrations import service as integrations_service
from app.integrations.models import STREAMS
from app.integrations.providers.square import webhooks as sq_webhooks
from app.integrations.providers.square.connector import SquareConnector
from app.integrations.providers.square.fake_client import FakeSquareClient
from app.main import app
from tests.conftest import SignupFn
from tests.integrations import conftest as fx
from tests.integrations.conftest import Clock, Connect, Connected, load_days

CONNECTION_KEYS = {
    "id", "provider", "mode", "status", "external_account_id", "created_at", "location_links", "streams",
}  # fmt: skip
STREAM_KEYS = {
    "stream", "status", "data_as_of", "last_success_at", "last_error", "backfill_target_from",
    "backfill_completed_through",
}  # fmt: skip


def local(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path.removeprefix('/api')}?{parts.query}"


async def test_authorize_consent_callback_connects_and_auto_links(
    signup: SignupFn, clock: Clock
) -> None:
    acct = await signup(location_name="Main St")
    c, h = acct["client"], acct["headers"]
    r = await c.post("/v1/integrations/square/authorize", headers=h)
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert (
        set(r.json()) == {"authorize_url"} and "/v1/integrations/square/fake-consent?state=" in url
    )

    consent = await c.get(local(url), follow_redirects=False)
    assert consent.status_code == 302
    cb = await c.get(local(consent.headers["location"]), follow_redirects=False)
    assert cb.status_code == 302
    loc = urlsplit(cb.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "http://localhost:3000/app/integrations"
    connected = loc.query.removeprefix("connected=")
    uuid.UUID(connected)

    listed = (await c.get("/v1/integrations/connections")).json()
    assert set(listed) == {"data"} and len(listed["data"]) == 1
    conn = listed["data"][0]
    assert set(conn) == CONNECTION_KEYS and conn["id"] == connected
    assert (conn["provider"], conn["mode"], conn["status"]) == ("square", "fake", "active")
    assert conn["external_account_id"] == "MLJUNIPER"
    # one platform location + one Square location: linked automatically
    assert conn["location_links"] == [
        {
            "external_location_id": "L_JUNIPER_BK",
            "external_name": "Juniper House",
            "location_id": acct["location"]["id"],
        }
    ]
    assert [s["stream"] for s in conn["streams"]] == list(STREAMS)
    assert all(set(s) == STREAM_KEYS for s in conn["streams"])
    assert {s["status"] for s in conn["streams"]} == {"pending"}

    # connecting again while active is a conflict
    again = await c.post("/v1/integrations/square/authorize", headers=h)
    assert again.status_code == 409 and again.json()["code"] == "conflict"
    # the connection never exposes credentials
    assert "credential" not in json.dumps(listed) and "token" not in json.dumps(listed)
    async with scoped_session(uuid.UUID(acct["org"]["id"])) as db:
        blob = await db.scalar(text("SELECT credential_ciphertext FROM integration_connections"))
    assert b"fake-at-" not in bytes(blob)  # encrypted at rest


async def test_callback_errors_redirect_with_a_code(signup: SignupFn) -> None:
    acct = await signup()
    c = acct["client"]
    bad = await c.get(
        "/v1/integrations/square/callback",
        params={"code": "x", "state": "nope"},
        follow_redirects=False,
    )
    assert bad.status_code == 302 and bad.headers["location"].endswith(
        "/app/integrations?error=invalid_state"
    )
    denied = await c.get(
        "/v1/integrations/square/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert denied.headers["location"].endswith("?error=access_denied")
    r = await c.get("/v1/integrations/square/fake-consent", params={"state": "nope"})
    assert r.status_code == 422


async def test_credentials_are_bound_to_org_and_connection(connect: Connect) -> None:
    from app.integrations.secrets import decrypt_credentials
    from app.security.keys import DecryptionError

    conn = await connect()
    async with scoped_session(conn.org_id) as db:
        blob = bytes(
            await db.scalar(text("SELECT credential_ciphertext FROM integration_connections"))
        )
    assert decrypt_credentials(blob, conn.org_id, conn.connection_id).merchant_id == "MLJUNIPER"
    with pytest.raises(DecryptionError):
        decrypt_credentials(blob, conn.org_id, uuid.uuid4())  # AAD = org_id|connection_id
    with pytest.raises(DecryptionError):
        decrypt_credentials(blob, uuid.uuid4(), conn.connection_id)


async def test_connection_detail_and_runs(connect: Connect, clock: Clock) -> None:
    conn = await connect()
    await load_days(conn, clock, date(2026, 3, 5), date(2026, 3, 6))
    r = await conn.client.get(f"/v1/integrations/connections/{conn.connection_id}")
    body = r.json()
    assert set(body) == CONNECTION_KEYS | {"recent_runs"}
    orders = next(s for s in body["streams"] if s["stream"] == "orders")
    assert (
        orders["backfill_target_from"] == "2026-03-05"
        and orders["backfill_completed_through"] == "2026-03-05"
    )
    assert body["recent_runs"] and len(body["recent_runs"]) <= 20
    run = next(r for r in body["recent_runs"] if r["kind"] == "backfill")
    assert set(run) == {
        "id",
        "stream",
        "kind",
        "status",
        "started_at",
        "finished_at",
        "fetched_count",
        "changed_count",
        "error_detail",
    }
    assert run["kind"] == "backfill" and run["status"] == "running" and run["fetched_count"] > 0
    assert (
        await conn.client.get(f"/v1/integrations/connections/{uuid.uuid4()}")
    ).status_code == 404


async def test_patch_locations_links_and_validates(connect: Connect) -> None:
    conn = await connect(extra_locations=1, location_name="Main")
    c, h = conn.client, conn.headers
    path = f"/v1/integrations/connections/{conn.connection_id}/locations"
    locs = (await c.get("/v1/locations")).json()["data"]
    target = next(loc for loc in locs if loc["name"] == "Extra 0")
    assert (await c.get(f"/v1/integrations/connections/{conn.connection_id}")).json()[
        "location_links"
    ][0]["location_id"] is None

    async def noop(*_: object, **__: object) -> None:
        return None

    import app.integrations.api as api

    api.temporal.start_sync = noop  # type: ignore[assignment]
    api.service.maybe_start_backfill = noop  # type: ignore[assignment]
    r = await c.patch(
        path,
        json={"links": [{"external_location_id": "L_JUNIPER_BK", "location_id": target["id"]}]},
        headers=h,
    )
    assert r.status_code == 200 and set(r.json()) == CONNECTION_KEYS
    assert r.json()["location_links"][0]["location_id"] == target["id"]
    unlink = await c.patch(
        path,
        json={"links": [{"external_location_id": "L_JUNIPER_BK", "location_id": None}]},
        headers=h,
    )
    assert unlink.json()["location_links"][0]["location_id"] is None
    bad = await c.patch(
        path,
        json={"links": [{"external_location_id": "L_NOPE", "location_id": target["id"]}]},
        headers=h,
    )
    assert bad.status_code == 422
    missing = await c.patch(
        path,
        json={
            "links": [{"external_location_id": "L_JUNIPER_BK", "location_id": str(uuid.uuid4())}]
        },
        headers=h,
    )
    assert missing.status_code == 422


async def test_sync_and_backfill_endpoints(
    connect: Connect, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.integrations.api as api

    started: list[tuple[str, Any]] = []

    async def fake_sync(org_id: uuid.UUID, cid: uuid.UUID, streams: Any = None, **_: Any) -> str:
        started.append(("sync", streams))
        return f"sync-now-{cid}-x"

    async def fake_backfill(
        org_id: uuid.UUID, cid: uuid.UUID, first: date, last: date, **_: Any
    ) -> str:
        started.append(("backfill", (first, last)))
        return f"backfill-{cid}"

    monkeypatch.setattr(api.temporal, "start_sync", fake_sync)
    monkeypatch.setattr(api.temporal, "start_backfill", fake_backfill)
    conn = await connect()
    base = f"/v1/integrations/connections/{conn.connection_id}"
    r = await conn.client.post(f"{base}/sync", json={"streams": ["orders"]}, headers=conn.headers)
    assert r.status_code == 202 and r.json() == {"workflow_id": f"sync-now-{conn.connection_id}-x"}
    assert (
        await conn.client.post(f"{base}/sync", json={}, headers=conn.headers)
    ).status_code == 202
    r = await conn.client.post(
        f"{base}/backfill", json={"from": "2026-01-01", "to": "2026-02-01"}, headers=conn.headers
    )
    assert r.status_code == 202 and r.json() == {"workflow_id": f"backfill-{conn.connection_id}"}
    assert started == [
        ("sync", ["orders"]),
        ("sync", None),
        ("backfill", (date(2026, 1, 1), date(2026, 2, 1))),
    ]
    r = await conn.client.post(
        f"{base}/backfill", json={"from": "2026-02-01", "to": "2026-01-01"}, headers=conn.headers
    )
    assert r.status_code == 422
    r = await conn.client.post(f"{base}/sync", json={"streams": ["bogus"]}, headers=conn.headers)
    assert r.status_code == 422


async def test_mapping_issues_list_and_ignore(connect: Connect, clock: Clock) -> None:
    conn = await connect()
    await load_days(conn, clock, date(2026, 3, 5), date(2026, 3, 6))
    r = await conn.client.get("/v1/integrations/mapping-issues", params={"status": "open"})
    issues = r.json()["data"]
    assert issues and all(
        set(i)
        == {
            "id",
            "kind",
            "external_id",
            "label",
            "occurrences",
            "first_seen_at",
            "last_seen_at",
            "resolved_at",
            "resolution",
            "resolved_entity_id",
            "new_since_resolved",
            "mappable",
        }
        for i in issues
    )
    item = next(i for i in issues if i["kind"] == "unmapped_item")
    assert item["occurrences"] >= 1 and item["resolved_at"] is None
    res = await conn.client.post(
        f"/v1/integrations/mapping-issues/{item['id']}/resolve",
        json={"ignore": True},
        headers=conn.headers,
    )
    assert res.status_code == 200 and res.json()["resolved_at"] is not None
    open_ids = [
        i["id"] for i in (await conn.client.get("/v1/integrations/mapping-issues")).json()["data"]
    ]
    resolved_ids = [
        i["id"]
        for i in (
            await conn.client.get("/v1/integrations/mapping-issues", params={"status": "resolved"})
        ).json()["data"]
    ]
    assert item["id"] not in open_ids and item["id"] in resolved_ids
    bad = await conn.client.post(
        f"/v1/integrations/mapping-issues/{item['id']}/resolve",
        json={"ignore": False},
        headers=conn.headers,
    )
    assert bad.status_code == 422
    nf = await conn.client.post(
        f"/v1/integrations/mapping-issues/{uuid.uuid4()}/resolve",
        json={"ignore": True},
        headers=conn.headers,
    )
    assert nf.status_code == 404


ISSUES = "/v1/integrations/mapping-issues"


async def _issue(conn: Connected, kind: str, status: str = "open") -> dict[str, Any]:
    r = await conn.client.get(ISSUES, params={"status": status})
    return next(i for i in r.json()["data"] if i["kind"] == kind)


async def _candidate(conn: Connected, issue_id: str, label_prefix: str) -> str:
    r = await conn.client.get(f"{ISSUES}/{issue_id}/candidates")
    assert r.status_code == 200, r.text
    return str(next(c["id"] for c in r.json()["data"] if c["label"].startswith(label_prefix)))


async def _resolve(conn: Connected, issue_id: str, body: dict[str, Any]) -> httpx.Response:
    return await conn.client.post(f"{ISSUES}/{issue_id}/resolve", json=body, headers=conn.headers)


async def test_map_item_backfills_lines_and_stops_recurring(connect: Connect, clock: Clock) -> None:
    conn = await connect()
    await load_days(conn, clock, date(2026, 2, 20), date(2026, 2, 28))
    issue = await _issue(conn, "unmapped_item")
    assert issue["external_id"] == "ADHOC_OPEN_FOOD" and issue["mappable"]
    variant = await _candidate(conn, issue["id"], "Dry-Aged Burger")

    r = await _resolve(conn, issue["id"], {"entity_id": variant})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolution"] == "mapped" and body["resolved_entity_id"] == variant

    count = text(
        "SELECT count(*) FILTER (WHERE menu_item_variant_id IS NULL) AS unmapped, "
        "count(*) FILTER (WHERE menu_item_variant_id = :v) AS mapped "
        "FROM sales_order_lines WHERE external_item_ref = 'ADHOC_OPEN_FOOD'"
    )
    async with scoped_session(conn.org_id) as db:
        before = (await db.execute(count, {"v": variant})).one()
    assert before.unmapped == 0 and before.mapped > 0

    # Later syncs resolve the ref: new lines arrive mapped and the issue doesn't recur.
    await load_days(conn, clock, date(2026, 3, 1), date(2026, 3, 6))
    async with scoped_session(conn.org_id) as db:
        after = (await db.execute(count, {"v": variant})).one()
    assert after.unmapped == 0 and after.mapped > before.mapped
    resolved = await _issue(conn, "unmapped_item", "resolved")
    assert resolved["occurrences"] == issue["occurrences"] and resolved["new_since_resolved"] == 0
    assert all(i["kind"] != "unmapped_item" for i in (await conn.client.get(ISSUES)).json()["data"])
    assert (await _resolve(conn, issue["id"], {"entity_id": variant})).status_code == 409


async def test_map_team_member_backfills_timecards(connect: Connect, clock: Clock) -> None:
    conn = await connect()
    await load_days(conn, clock, date(2026, 2, 20), date(2026, 2, 28))
    issue = await _issue(conn, "unmapped_team_member")
    r = await conn.client.get(f"{ISSUES}/{issue['id']}/candidates")
    staff = str(r.json()["data"][0]["id"])

    assert (await _resolve(conn, issue["id"], {"entity_id": staff})).status_code == 200
    await load_days(conn, clock, date(2026, 3, 1), date(2026, 3, 6))
    async with scoped_session(conn.org_id) as db:
        rows = (
            await db.execute(
                text("SELECT staff_member_id FROM timecards WHERE external_staff_ref = :ext"),
                {"ext": issue["external_id"]},
            )
        ).all()
    assert len(rows) > issue["occurrences"] - 1 and {str(r.staff_member_id) for r in rows} == {
        staff
    }


async def test_ignored_issue_reports_new_occurrences(connect: Connect, clock: Clock) -> None:
    conn = await connect()
    await load_days(conn, clock, date(2026, 2, 20), date(2026, 2, 28))
    issue = await _issue(conn, "unmapped_item")
    r = await _resolve(conn, issue["id"], {"ignore": True})
    assert r.json()["resolution"] == "ignored" and r.json()["new_since_resolved"] == 0

    await load_days(conn, clock, date(2026, 3, 1), date(2026, 3, 6))
    ignored = await _issue(conn, "unmapped_item", "resolved")
    assert ignored["new_since_resolved"] == ignored["occurrences"] - issue["occurrences"] > 0
    # Still mappable after being ignored.
    variant = await _candidate(conn, issue["id"], "Dry-Aged Burger")
    assert (await _resolve(conn, issue["id"], {"entity_id": variant})).status_code == 200


async def test_resolve_rejects_bad_requests(connect: Connect, clock: Clock) -> None:
    conn = await connect()
    other = await connect()
    await load_days(conn, clock, date(2026, 2, 20), date(2026, 2, 28))
    await load_days(other, clock, date(2026, 2, 27), date(2026, 2, 27))
    issue = await _issue(conn, "unmapped_item")
    async with scoped_session(other.org_id) as db:
        foreign = str(await db.scalar(text("SELECT id FROM menu_item_variants LIMIT 1")))

    both = await _resolve(conn, issue["id"], {"ignore": True, "entity_id": foreign})
    assert both.status_code == 422
    assert (await _resolve(conn, issue["id"], {})).status_code == 422
    for entity in (foreign, str(uuid.uuid4())):  # another org's variant reads as missing (RLS)
        r = await _resolve(conn, issue["id"], {"entity_id": entity})
        assert r.status_code == 422 and r.json()["errors"] == {"entity_id": "Not found"}


async def test_disconnect_revokes_and_keeps_data(
    connect: Connect, clock: Clock, square: FakeSquareClient
) -> None:
    conn = await connect()
    await load_days(conn, clock, date(2026, 3, 5), date(2026, 3, 6))
    r = await conn.client.delete(
        f"/v1/integrations/connections/{conn.connection_id}", headers=conn.headers
    )
    assert r.status_code == 204
    assert (await conn.client.get("/v1/integrations/connections")).json() == {"data": []}
    assert (
        await conn.client.get(f"/v1/integrations/connections/{conn.connection_id}")
    ).status_code == 404
    assert len(square._revoked) == 1  # token revoked at the provider
    async with scoped_session(conn.org_id) as db:
        status, cipher, orders = (
            await db.execute(
                text(
                    "SELECT status, credential_ciphertext, (SELECT count(*) FROM sales_orders) FROM integration_connections"
                )
            )
        ).one()
    assert status == "disconnected" and cipher is None and orders > 0  # data stays
    summary = await conn.client.get(
        "/v1/commerce/daily",
        params={"location_id": str(conn.location_id), "from": "2026-03-05", "to": "2026-03-06"},
    )
    assert sum(d["orders"] for d in summary.json()["days"]) > 0
    assert {f["status"] for f in summary.json()["freshness"]} == {"not_connected"}
    assert (
        await conn.client.delete(
            f"/v1/integrations/connections/{conn.connection_id}", headers=conn.headers
        )
    ).status_code == 404
    # after disconnecting, the org can connect again
    assert (
        await conn.client.post("/v1/integrations/square/authorize", headers=conn.headers)
    ).status_code == 200


# ---------------------------------------------------------------- webhooks

KEY = "test-signature-key"


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch, square: FakeSquareClient) -> list[tuple[Any, ...]]:
    monkeypatch.setattr(get_settings(), "square_mode", "sandbox")
    providers.set_connector_override(SquareConnector(square, mode="sandbox", signature_key=KEY))
    calls: list[tuple[Any, ...]] = []

    async def fake_start(
        org_id: uuid.UUID, cid: uuid.UUID, stream: str, ids: list[str], event_id: str, **_: Any
    ) -> str:
        calls.append((stream, tuple(ids), event_id))
        return f"webhook-{event_id}"

    monkeypatch.setattr(integrations_service.temporal, "start_webhook_fetch", fake_start)
    return calls


def signed(body: dict[str, Any], key: str = KEY) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    sig = sq_webhooks.compute_signature(key, get_settings().webhook_url, raw)
    return raw, {"x-square-hmacsha256-signature": sig, "content-type": "application/json"}


def event(
    event_id: str, type_: str = "order.updated", obj: str | None = "fake-ord-20260306-0001"
) -> dict[str, Any]:
    return {"merchant_id": "MLJUNIPER", "type": type_, "event_id": event_id, "created_at": "2026-03-07T20:00:00Z",
            "data": {"type": "order", "id": obj, "object": {"order_updated": {"location_id": "L_JUNIPER_BK"}}}}  # fmt: skip


async def test_webhook_hmac_verified_and_deduplicated(
    connect: Connect, sandbox: list[tuple[Any, ...]], owner_engine: AsyncEngine
) -> None:
    conn = await connect()
    # Every fake seller is merchant MLJUNIPER; make this the only live connection for it.
    async with owner_engine.begin() as c:
        await c.execute(
            text("UPDATE integration_connections SET status = 'disconnected' WHERE id <> :i"),
            {"i": conn.connection_id},
        )
    anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    eid = uuid.uuid4().hex
    body, headers = signed(event(eid))
    for _ in range(3):  # at-least-once delivery
        r = await anon.post("/v1/webhooks/square", content=body, headers=headers)
        assert r.status_code == 200
    assert sandbox == [("orders", ("fake-ord-20260306-0001",), eid)]  # one fetch, not three
    async with scoped_session(conn.org_id) as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM webhook_events WHERE event_id = :e"), {"e": eid}
            )
            == 1
        )

    bad_body, bad_headers = signed(event(uuid.uuid4().hex), key="wrong-key")
    r = await anon.post("/v1/webhooks/square", content=bad_body, headers=bad_headers)
    assert r.status_code == 401
    r = await anon.post("/v1/webhooks/square", content=body)  # no signature
    assert r.status_code == 401
    garbage = b"not json"
    g_headers = {
        "x-square-hmacsha256-signature": sq_webhooks.compute_signature(
            KEY, get_settings().webhook_url, garbage
        )
    }
    assert (
        await anon.post("/v1/webhooks/square", content=garbage, headers=g_headers)
    ).status_code == 422
    unknown = event(uuid.uuid4().hex)
    unknown["merchant_id"] = "SOMEONE_ELSE"
    ub, uh = signed(unknown)
    assert (await anon.post("/v1/webhooks/square", content=ub, headers=uh)).status_code == 200
    assert len(sandbox) == 1
    await anon.aclose()


async def test_webhook_is_unavailable_in_fake_mode(signup: SignupFn) -> None:
    anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    body, headers = signed(event("e1"))
    assert get_settings().square_mode == "fake"
    r = await anon.post("/v1/webhooks/square", content=body, headers=headers)
    assert r.status_code == 404
    await anon.aclose()


def test_fixture_helpers_import() -> None:
    assert fx.T0.year == 2026 and Connected and STREAMS
