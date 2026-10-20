import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db import scoped_session
from app.main import app
from app.security import passwords, throttle
from tests.conftest import PASSWORD, SignupFn


def _problem(r: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = r.json()
    assert body["code"] == code
    assert body["status"] == status
    assert body["request_id"] == r.headers["x-request-id"]
    return body


async def test_signup_login_me_logout_round_trip(
    client: httpx.AsyncClient, signup: SignupFn
) -> None:
    acct = await signup()
    assert acct["role"] == "owner"
    assert acct["location"]["business_day_start"] == "04:00"
    assert acct["org"]["name"] == "Test Org"
    c = acct["client"]

    me = await c.get("/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == acct["user"]["email"]
    assert me.json()["csrf_token"] == acct["csrf_token"]

    locs = await c.get("/v1/locations")
    assert [loc["name"] for loc in locs.json()["data"]] == ["Main"]

    # Logout requires CSRF, then the session is dead
    _problem(await c.post("/v1/auth/logout"), 403, "csrf_failed")
    out = await c.post("/v1/auth/logout", headers=acct["headers"])
    assert out.status_code == 204
    c.cookies.clear()
    _problem(await c.get("/v1/auth/me"), 401, "unauthenticated")

    # Log back in with a new client; a new token and CSRF token are issued
    login = await client.post(
        "/v1/auth/login", json={"email": acct["user"]["email"].upper(), "password": PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["role"] == "owner"
    assert login.json()["csrf_token"] != acct["csrf_token"]
    assert (await client.get("/v1/auth/me")).status_code == 200
    await c.aclose()


async def test_logged_out_token_is_revoked(signup: SignupFn) -> None:
    acct = await signup()
    c = acct["client"]
    token = c.cookies["chefos_session"]
    await c.post("/v1/auth/logout", headers=acct["headers"])
    c.cookies.set("chefos_session", token)
    _problem(await c.get("/v1/auth/me"), 401, "session_expired")


async def test_session_cookie_flags(signup: SignupFn, monkeypatch: pytest.MonkeyPatch) -> None:
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    payload = {
        "email": f"c-{uuid.uuid4().hex[:8]}@example.com",
        "password": PASSWORD,
        "display_name": "C",
        "org_name": "C",
        "location_name": "C",
        "timezone": "UTC",
    }
    r = await c.post("/v1/auth/signup", json=payload)
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("chefos_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie
    assert "Max-Age=1209600" in cookie

    monkeypatch.setattr(get_settings(), "session_cookie_secure", True)
    r = await c.post("/v1/auth/login", json={"email": payload["email"], "password": PASSWORD})
    assert "Secure" in r.headers["set-cookie"]
    await c.aclose()


async def test_login_issues_new_token_each_time(
    signup: SignupFn, client: httpx.AsyncClient
) -> None:
    acct = await signup()
    body = {"email": acct["user"]["email"], "password": PASSWORD}
    r1 = await client.post("/v1/auth/login", json=body)
    t1 = client.cookies["chefos_session"]
    r2 = await client.post("/v1/auth/login", json=body)
    assert r1.status_code == r2.status_code == 200
    assert client.cookies["chefos_session"] != t1


@pytest.mark.parametrize(
    ("column", "delta", "expected_code"),
    [
        ("last_seen_at", timedelta(hours=25), "session_expired"),  # idle expiry
        ("expires_at", timedelta(seconds=-1), "session_expired"),  # absolute expiry
    ],
)
async def test_session_expiry(
    signup: SignupFn, owner_engine: AsyncEngine, column: str, delta: timedelta, expected_code: str
) -> None:
    acct = await signup()
    c = acct["client"]
    assert (await c.get("/v1/auth/me")).status_code == 200
    new_value = datetime.now(UTC) - delta if column == "last_seen_at" else datetime.now(UTC) + delta
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(f"UPDATE sessions SET {column} = :v WHERE user_id = :u"),
            {"v": new_value, "u": uuid.UUID(acct["user"]["id"])},
        )
    _problem(await c.get("/v1/auth/me"), 401, expected_code)


async def test_session_lifetimes_are_14d_absolute_24h_idle(
    signup: SignupFn, owner_engine: AsyncEngine
) -> None:
    acct = await signup()
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT expires_at - created_at AS life FROM sessions WHERE user_id = :u"),
                {"u": uuid.UUID(acct["user"]["id"])},
            )
        ).one()
    assert row.life == timedelta(days=14)
    # Just inside the idle window still works
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE sessions SET last_seen_at = now() - interval '23 hours' WHERE user_id = :u"
            ),
            {"u": uuid.UUID(acct["user"]["id"])},
        )
    assert (await acct["client"].get("/v1/auth/me")).status_code == 200


PUBLIC_UNSAFE = {
    ("POST", "/v1/auth/signup"),
    ("POST", "/v1/auth/login"),
    ("POST", "/v1/webhooks/square"),  # HMAC-verified instead of session auth
}


def _unsafe_routes() -> list[tuple[str, str]]:
    found = []
    for path, item in app.openapi()["paths"].items():
        for method in item:
            if (
                method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
                and (
                    method.upper(),
                    path,
                )
                not in PUBLIC_UNSAFE
            ):
                found.append((method.upper(), path))
    return sorted(found)


async def test_csrf_required_on_every_unsafe_route(signup: SignupFn) -> None:
    """Enumerated from the router so new routes can't skip CSRF."""
    acct = await signup()
    c = acct["client"]
    routes = _unsafe_routes()
    assert ("POST", "/v1/locations") in routes
    for method, path in routes:
        url = path.replace("{location_id}", str(uuid.uuid4())).replace("{workflow_id}", "x")
        url = url.replace("{id}", str(uuid.uuid4()))
        r = await c.request(method, url, json={})
        _problem(r, 403, "csrf_failed")
        r = await c.request(method, url, json={}, headers={"X-CSRF-Token": "wrong"})
        _problem(r, 403, "csrf_failed")


async def test_unauthenticated_requests_are_401(client: httpx.AsyncClient) -> None:
    for method, path in [("GET", "/v1/auth/me"), ("GET", "/v1/locations"), ("GET", "/v1/org")]:
        _problem(await client.request(method, path), 401, "unauthenticated")


async def test_login_wrong_password_and_unknown_email_are_indistinguishable(
    signup: SignupFn, client: httpx.AsyncClient
) -> None:
    acct = await signup()
    a = await client.post(
        "/v1/auth/login", json={"email": acct["user"]["email"], "password": "nope-nope-nope"}
    )
    b = await client.post(
        "/v1/auth/login", json={"email": "nobody@example.com", "password": "nope-nope-nope"}
    )
    ba, bb = _problem(a, 401, "unauthenticated"), _problem(b, 401, "unauthenticated")
    assert ba["detail"] == bb["detail"] == "Invalid email or password"


async def test_login_for_unknown_email_still_runs_argon2(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real = passwords.verify_password_sync

    def spy(password_hash: str, password: str) -> bool:
        seen.append(password_hash)
        return real(password_hash, password)

    monkeypatch.setattr(passwords, "verify_password_sync", spy)
    r = await client.post(
        "/v1/auth/login", json={"email": "ghost@example.com", "password": "whatever-pass-1"}
    )
    assert r.status_code == 401
    assert seen == [passwords.DUMMY_HASH]
    assert passwords.DUMMY_HASH.startswith("$argon2id$")


async def _fail_login(client: httpx.AsyncClient, email: str, times: int) -> None:
    for _ in range(times):
        r = await client.post("/v1/auth/login", json={"email": email, "password": "nope-nope-nope"})
        _problem(r, 401, "unauthenticated")


async def test_login_locks_email_after_too_many_failures(
    signup: SignupFn, client: httpx.AsyncClient
) -> None:
    email = (await signup())["user"]["email"]
    await _fail_login(client, email, throttle.EMAIL_LIMIT)

    # Even the right password is refused until the window ends, and Argon2 isn't run.
    r = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    body = _problem(r, 429, "rate_limited")
    assert body["detail"] == "Too many failed sign-in attempts. Try again later."
    assert 0 < int(r.headers["retry-after"]) <= throttle.WINDOW.total_seconds()


async def test_unknown_email_locks_the_same_way(client: httpx.AsyncClient) -> None:
    await _fail_login(client, "ghost-lock@example.com", throttle.EMAIL_LIMIT)
    r = await client.post(
        "/v1/auth/login", json={"email": "ghost-lock@example.com", "password": "whatever-pass-1"}
    )
    _problem(r, 429, "rate_limited")


async def test_successful_login_resets_the_email_counter(
    signup: SignupFn, client: httpx.AsyncClient
) -> None:
    email = (await signup())["user"]["email"]
    await _fail_login(client, email, throttle.EMAIL_LIMIT - 1)
    ok = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert ok.status_code == 200
    await _fail_login(client, email, throttle.EMAIL_LIMIT - 1)


async def test_lock_expires_with_the_window(
    signup: SignupFn, client: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    email = (await signup())["user"]["email"]
    await _fail_login(client, email, throttle.EMAIL_LIMIT)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE login_throttle SET window_started_at = window_started_at - CAST(:d AS interval)"
            ),
            {"d": throttle.WINDOW + timedelta(seconds=1)},
        )
    r = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text


async def test_login_locks_ip_across_emails(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(throttle, "IP_LIMIT", 3)
    for i in range(3):
        await _fail_login(client, f"spray-{i}@example.com", 1)
    r = await client.post(
        "/v1/auth/login", json={"email": "spray-new@example.com", "password": "nope-nope-nope"}
    )
    _problem(r, 429, "rate_limited")


async def test_signup_duplicate_email_is_409(signup: SignupFn, client: httpx.AsyncClient) -> None:
    acct = await signup()
    r = await client.post(
        "/v1/auth/signup", json={**acct["payload"], "email": acct["user"]["email"].upper()}
    )
    body = _problem(r, 409, "conflict")
    assert body["errors"] == {"email": "Already registered"}


@pytest.mark.parametrize("password", ["short1!", "password1234", "aaaaaaaaaaaaaaa"])
async def test_signup_weak_password_is_422(client: httpx.AsyncClient, password: str) -> None:
    r = await client.post(
        "/v1/auth/signup",
        json={
            "email": "w@example.com",
            "password": password,
            "display_name": "W",
            "org_name": "O",
            "location_name": "L",
            "timezone": "UTC",
        },
    )
    assert "password" in _problem(r, 422, "validation_error")["errors"]


async def test_signup_invalid_timezone_is_422(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/v1/auth/signup",
        json={
            "email": "t@example.com",
            "password": PASSWORD,
            "display_name": "T",
            "org_name": "O",
            "location_name": "L",
            "timezone": "Mars/Olympus",
        },
    )
    assert "timezone" in _problem(r, 422, "validation_error")["errors"]


async def test_org_and_location_management(acct: dict[str, Any]) -> None:
    c, h = acct["client"], acct["headers"]
    org = await c.patch(
        "/v1/org", json={"name": "Renamed", "timezone": "America/Chicago"}, headers=h
    )
    assert org.status_code == 200 and org.json()["name"] == "Renamed"
    assert (await c.get("/v1/org")).json()["timezone"] == "America/Chicago"

    created = await c.post(
        "/v1/locations",
        json={
            "name": "Second",
            "address": "1 Main St",
            "timezone": "America/Chicago",
            "business_day_start": "05:30",
        },
        headers=h,
    )
    assert created.status_code == 201
    loc = created.json()
    assert loc["business_day_start"] == "05:30" and loc["archived_at"] is None

    # duplicate names are allowed
    dup = await c.post("/v1/locations", json={"name": "Second", "timezone": "UTC"}, headers=h)
    assert dup.status_code == 201

    patched = await c.patch(f"/v1/locations/{loc['id']}", json={"address": "2 Elm St"}, headers=h)
    assert patched.json()["address"] == "2 Elm St" and patched.json()["name"] == "Second"

    archived = await c.post(f"/v1/locations/{loc['id']}/archive", headers=h)
    assert archived.status_code == 200 and archived.json()["archived_at"] is not None

    names = [x["id"] for x in (await c.get("/v1/locations")).json()["data"]]
    assert loc["id"] not in names
    all_ids = [x["id"] for x in (await c.get("/v1/locations?include_archived=true")).json()["data"]]
    assert loc["id"] in all_ids

    bad = await c.post(
        "/v1/locations",
        json={"name": "X", "timezone": "UTC", "business_day_start": "25:00"},
        headers=h,
    )
    assert "business_day_start" in _problem(bad, 422, "validation_error")["errors"]


async def test_location_of_another_org_is_404(signup: SignupFn) -> None:
    a, b = await signup(), await signup()
    foreign = a["location"]["id"]
    for r in (
        await b["client"].patch(
            f"/v1/locations/{foreign}", json={"name": "pwn"}, headers=b["headers"]
        ),
        await b["client"].post(f"/v1/locations/{foreign}/archive", headers=b["headers"]),
    ):
        _problem(r, 404, "not_found")
    assert (await a["client"].get("/v1/locations")).json()["data"][0]["name"] == "Main"


async def test_non_admin_gets_403_on_admin_routes(
    signup: SignupFn, owner_engine: AsyncEngine, client: httpx.AsyncClient
) -> None:
    owner = await signup()
    user_id, org_id = uuid.uuid4(), owner["org"]["id"]
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, display_name) VALUES (:i, :e, :p, 'Mgr')"
            ),
            {
                "i": user_id,
                "e": f"mgr-{user_id.hex[:8]}@example.com",
                "p": await passwords.hash_password(PASSWORD),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO memberships (id, org_id, user_id, role) VALUES (:i, :o, :u, 'manager')"
            ),
            {"i": uuid.uuid4(), "o": org_id, "u": user_id},
        )
        email = (
            await conn.execute(text("SELECT email FROM users WHERE id = :i"), {"i": user_id})
        ).scalar_one()
    login = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.json()["role"] == "manager"
    h = {"X-CSRF-Token": login.json()["csrf_token"]}
    _problem(await client.patch("/v1/org", json={"name": "x"}, headers=h), 403, "forbidden")
    _problem(
        await client.post("/v1/locations", json={"name": "x", "timezone": "UTC"}, headers=h),
        403,
        "forbidden",
    )
    assert (await client.get("/v1/locations")).status_code == 200  # reads are fine


async def test_mutations_write_audit_events(acct: dict[str, Any]) -> None:
    c, h = acct["client"], acct["headers"]
    await c.patch("/v1/org", json={"name": "Audited"}, headers=h)
    created = (
        await c.post("/v1/locations", json={"name": "A", "timezone": "UTC"}, headers=h)
    ).json()
    await c.post(f"/v1/locations/{created['id']}/archive", headers=h)
    async with scoped_session(uuid.UUID(acct["org"]["id"])) as db:
        actions = {r[0] for r in await db.execute(text("SELECT action FROM audit_events"))}
        with_request_id = await db.scalar(
            text("SELECT count(*) FROM audit_events WHERE request_id IS NOT NULL")
        )
    assert {
        "user.signup",
        "org.create",
        "location.create",
        "org.update",
        "location.archive",
    } <= actions
    assert with_request_id


async def test_request_id_header(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    assert client.cookies.get("chefos_session") is None
    r = await client.get("/nope")
    body = _problem(r, 404, "not_found")
    assert body["request_id"]
