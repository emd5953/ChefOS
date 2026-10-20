"""Test setup. Tests run as chefos_app against the compose Postgres, in the chefos_test database,
so they never touch dev data. Environment is forced before any app import."""

import asyncio
import base64
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

_HOST = os.environ.get("TEST_DB_HOST", "localhost:5432")
os.environ["DATABASE_URL"] = f"postgresql+asyncpg://chefos_app:chefos_app@{_HOST}/chefos_test"
os.environ["MIGRATIONS_DATABASE_URL"] = (
    f"postgresql+asyncpg://chefos_owner:chefos_owner@{_HOST}/chefos_test"
)
os.environ["ENV"] = "test"
os.environ["OTEL_ENABLED"] = "false"
os.environ["CHEFOS_MASTER_KEY"] = base64.b64encode(b"k" * 32).decode()
os.environ["CHEFOS_MASTER_KEY_ID"] = "test-1"
os.environ["SESSION_COOKIE_SECURE"] = "false"

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine  # noqa: E402

from app.main import app  # noqa: E402

API_ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct-horse-battery-9"


def _alembic_config() -> Config:
    return Config(str(API_ROOT / "alembic.ini"))


@pytest_asyncio.fixture(scope="session", autouse=True)
async def migrated_db() -> AsyncIterator[None]:
    """Fresh schema for the run (also proves migrations are reversible)."""
    cfg = _alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield


@pytest_asyncio.fixture(scope="session")
async def owner_engine(migrated_db: None) -> AsyncIterator[AsyncEngine]:
    """chefos_owner connection for test setup and inspection (owner is not forced through RLS)."""
    engine = create_async_engine(os.environ["MIGRATIONS_DATABASE_URL"])
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _reset_login_throttle(owner_engine: AsyncEngine) -> None:
    """Every test client shares one IP, so failed-login counters must not leak between tests."""
    async with owner_engine.begin() as conn:
        await conn.execute(text("DELETE FROM login_throttle"))


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


SignupFn = Callable[..., Awaitable[dict[str, Any]]]


@pytest.fixture
def signup() -> SignupFn:
    """Sign up a fresh user+org through the API. Returns the body plus a client with cookies."""

    async def _signup(**overrides: Any) -> dict[str, Any]:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        payload = {
            "email": f"user-{uuid.uuid4().hex[:10]}@example.com",
            "password": PASSWORD,
            "display_name": "Test User",
            "org_name": "Test Org",
            "location_name": "Main",
            "timezone": "America/New_York",
            **overrides,
        }
        r = await c.post("/v1/auth/signup", json=payload)
        assert r.status_code == 201, r.text
        body = r.json()
        body["client"] = c
        body["headers"] = {"X-CSRF-Token": body["csrf_token"]}
        body["payload"] = payload
        return body

    return _signup


@pytest_asyncio.fixture
async def acct(signup: SignupFn) -> AsyncIterator[dict[str, Any]]:
    account = await signup()
    yield account
    await account["client"].aclose()


@pytest.fixture
def db_text() -> Callable[[str], Any]:
    return text
