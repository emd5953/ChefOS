import json
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from app.integrations.providers.square import (
    Credentials,
    HttpSquareClient,
    SquareAuthError,
    SquareClientError,
    SquareRateLimited,
    SquareTransientError,
)

CREDS = Credentials("tok", "rtok", datetime(2030, 1, 1, tzinfo=UTC), "M1")


def make(handler: Any, **kw: Any) -> HttpSquareClient:
    return HttpSquareClient("cid", "secret", transport=httpx.MockTransport(handler), **kw)


def test_headers_and_default_version_and_url() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"locations": [{"id": "L1"}]})

    out = make(handler).list_locations(CREDS)
    assert out == [{"id": "L1"}]
    r = seen[0]
    assert r.headers["Authorization"] == "Bearer tok"
    assert r.headers["Square-Version"] == "2025-10-16"
    assert str(r.url) == "https://connect.squareupsandbox.com/v2/locations"


def test_custom_square_version() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={})

    make(handler, square_version="2026-01-22").list_locations(CREDS)
    assert seen[0].headers["Square-Version"] == "2026-01-22"


def test_search_orders_body_and_pagination_passthrough() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        bodies.append(body)
        assert req.url.path == "/v2/orders/search"
        if "cursor" not in body:
            return httpx.Response(
                200,
                json={
                    "orders": [
                        {"id": "O1", "updated_at": "2026-01-01T00:00:00.000Z", "version": 2}
                    ],
                    "cursor": "C2",
                },
            )
        return httpx.Response(
            200,
            json={"orders": [{"id": "O2", "updated_at": "2026-01-02T00:00:00.000Z", "version": 1}]},
        )

    c = make(handler)
    a = datetime(2026, 1, 1, tzinfo=UTC)
    b = datetime(2026, 2, 1, tzinfo=UTC)
    p1 = c.search_orders(CREDS, ["L1"], a, b, None, limit=1)
    assert p1.next_cursor == "C2" and p1.items[0].external_id == "O1" and p1.items[0].version == "2"
    p2 = c.search_orders(CREDS, ["L1"], a, b, p1.next_cursor, limit=1)
    assert p2.next_cursor is None and p2.max_updated_at == datetime(2026, 1, 2, tzinfo=UTC)
    q = bodies[0]
    assert q["location_ids"] == ["L1"] and q["limit"] == 1
    assert q["query"]["filter"]["state_filter"] == {"states": ["COMPLETED"]}
    assert q["query"]["filter"]["date_time_filter"]["updated_at"] == {
        "start_at": "2026-01-01T00:00:00.000Z",
        "end_at": "2026-02-01T00:00:00.000Z",
    }
    assert q["query"]["sort"] == {"sort_field": "UPDATED_AT", "sort_order": "ASC"}
    assert bodies[1]["cursor"] == "C2"


def test_timecard_search_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={"timecards": [{"id": "TC1", "updated_at": "2026-01-01T00:00:00Z", "version": 1}]},
        )

    page = make(handler).search_timecards(CREDS, ["L1"], date(2026, 1, 1), date(2026, 1, 3), None)
    assert seen[0].url.path == "/v2/labor/timecards/search"
    assert json.loads(seen[0].content) == {
        "query": {
            "filter": {
                "location_ids": ["L1"],
                "workday": {
                    "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-03"},
                    "match_timecards_by": "START_AT",
                },
            },
            "sort": {"field": "START_AT", "order": "ASC"},
        },
        "limit": 200,
    }
    assert page.items[0].external_id == "TC1"


def test_catalog_begin_time_includes_deleted() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"objects": []})

    c = make(handler)
    c.search_catalog(CREDS, None, None)
    c.search_catalog(CREDS, datetime(2026, 1, 1, tzinfo=UTC), "cur")
    assert "include_deleted_objects" not in bodies[0]
    assert bodies[1]["include_deleted_objects"] is True and bodies[1]["cursor"] == "cur"
    assert bodies[1]["begin_time"] == "2026-01-01T00:00:00.000Z"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors(status: int) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, json={"errors": [{"code": "UNAUTHORIZED", "detail": "bad token"}]}
        )

    with pytest.raises(SquareAuthError, match="bad token"):
        make(handler).list_locations(CREDS)


def test_rate_limited_with_retry_after() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"}, json={"errors": []})

    with pytest.raises(SquareRateLimited) as ei:
        make(handler).list_locations(CREDS)
    assert ei.value.retry_after == 7.0


def test_rate_limited_without_header() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    with pytest.raises(SquareRateLimited) as ei:
        make(handler).list_locations(CREDS)
    assert ei.value.retry_after is None


@pytest.mark.parametrize("status", [500, 502, 503])
def test_server_errors_transient(status: int) -> None:
    with pytest.raises(SquareTransientError):
        make(lambda req: httpx.Response(status, text="boom")).list_locations(CREDS)


def test_timeout_and_network_errors_transient() -> None:
    def timeout(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    def refused(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=req)

    with pytest.raises(SquareTransientError):
        make(timeout).list_locations(CREDS)
    with pytest.raises(SquareTransientError):
        make(refused).list_locations(CREDS)


def test_other_4xx_client_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errors": [{"code": "BAD_REQUEST", "detail": "nope"}]})

    with pytest.raises(SquareClientError) as ei:
        make(handler).list_locations(CREDS)
    assert ei.value.status_code == 400


def test_oauth_flow() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/oauth2/revoke":
            return httpx.Response(200, json={"success": True})
        body = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "access_token": "new-" + body["grant_type"],
                "refresh_token": "rt2",
                "expires_at": "2030-01-01T00:00:00Z",
                "merchant_id": "M1",
            },
        )

    c = make(handler)
    url = c.authorize_url("st", "https://app/cb", ["ORDERS_READ", "ITEMS_READ"])
    assert url.startswith("https://connect.squareupsandbox.com/oauth2/authorize?")
    assert "state=st" in url and "scope=ORDERS_READ+ITEMS_READ" in url and "client_id=cid" in url
    creds = c.obtain_token("code1", "https://app/cb")
    assert creds.access_token == "new-authorization_code" and creds.merchant_id == "M1"
    assert json.loads(seen[0].content)["client_secret"] == "secret"
    assert "Authorization" not in seen[0].headers
    refreshed = c.refresh_token(creds)
    assert refreshed.access_token == "new-refresh_token"
    assert json.loads(seen[1].content)["refresh_token"] == "rt2"
    c.revoke(creds)
    assert seen[2].url.path == "/oauth2/revoke"
    assert seen[2].headers["Authorization"] == "Client secret"
