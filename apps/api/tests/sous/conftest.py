"""Fixtures for Sous: the integrations fixtures (fake Square, clock) plus a seeded location."""

# ruff: noqa: F811  (re-exported fixtures are used as arguments below)

from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date

import pytest
import pytest_asyncio

from app.integrations import sync
from app.main import app
from app.sous import api as sous_api
from app.sous.agent import llm
from app.sous.agent.llm import LLMProvider
from tests.integrations.conftest import (  # noqa: F401
    Clock,
    Connect,
    Connected,
    clock,
    connect,
    load_days,
    square,
)

FIRST, LAST = date(2026, 2, 22), date(2026, 3, 7)  # the clock's business date is LAST


@pytest_asyncio.fixture
async def loaded(connect: Connect, clock: Clock) -> Connected:
    conn = await connect()
    await load_days(conn, clock, FIRST, LAST)
    await sync.sync_connection(conn.org_id, conn.connection_id, force=True, now=clock.now)
    return conn


@pytest_asyncio.fixture
async def bare(connect: Connect) -> Connected:
    """An org and location with no integration: cheap, for tests that don't need sales data."""
    return await connect(complete=False)


@pytest.fixture
def use_provider() -> Iterator[Callable[[LLMProvider], None]]:
    """Install a provider for the API for the duration of a test: `use_provider(fake)`."""

    def install(provider: LLMProvider) -> None:
        app.dependency_overrides[sous_api.provider_dep] = lambda: provider

    yield install
    app.dependency_overrides.pop(sous_api.provider_dep, None)


@pytest_asyncio.fixture(autouse=True)
async def fresh_rate_limits() -> AsyncIterator[None]:
    sous_api._asked.clear()
    llm.BREAKER.reset()
    yield
