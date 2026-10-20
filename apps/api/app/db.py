"""Engine, declarative Base, and session helpers.

Every org-scoped query must run in a transaction where `app.org_id` is set (the equivalent of
`SET LOCAL app.org_id = '<uuid>'`). PostgreSQL RLS policies compare against it, so a missing
scope matches no rows.

Use `org_session` (request handlers; in `app.security.sessions`) or `activity_org_session`
(Temporal activities). `unscoped_session` is restricted to the auth code paths; a test
enforces that.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import lru_cache

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import UserDefinedType

from app.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """The one declarative base. Alembic autogenerate sees every model imported by app.models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CIText(UserDefinedType[str]):
    """PostgreSQL citext (case-insensitive text)."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "CITEXT"


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


def instrument_engine() -> None:
    """Attach SQL spans to the engine (called from telemetry setup)."""
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=get_engine().sync_engine)


async def set_scope(
    session: AsyncSession, *, org_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None
) -> None:
    """Set transaction-local RLS scope (set_config(..., true) == SET LOCAL)."""
    if org_id is not None:
        await session.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": str(org_id)})
    if user_id is not None:
        await session.execute(
            text("SELECT set_config('app.user_id', :v, true)"), {"v": str(user_id)}
        )


@asynccontextmanager
async def unscoped_session() -> AsyncIterator[AsyncSession]:
    """Transaction with no org scope. Only for auth code that runs before scope exists."""
    async with _sessionmaker()() as session, session.begin():
        yield session


@asynccontextmanager
async def scoped_session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """Transaction with `app.org_id` set. Commits on success, rolls back on error."""
    async with _sessionmaker()() as session, session.begin():
        await set_scope(session, org_id=org_id)
        yield session


def activity_org_session(org_id: uuid.UUID) -> AbstractAsyncContextManager[AsyncSession]:
    """The only way Temporal activities open a DB session: `async with activity_org_session(id)`."""
    return scoped_session(org_id)


async def lookup_connection_owners(
    provider: str, account_id: str
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Resolve a provider account id to [(connection_id, org_id)] for webhooks, which arrive
    without a session. Calls a narrow SECURITY DEFINER function; it is the only unscoped read."""
    async with _sessionmaker()() as session, session.begin():
        rows = (
            await session.execute(
                text(
                    "SELECT connection_id, org_id FROM integration_connection_for_account(:p, :a)"
                ),
                {"p": provider, "a": account_id},
            )
        ).all()
    return [(r.connection_id, r.org_id) for r in rows]


async def check_db() -> None:
    """Raises if the database is unreachable (used by /readyz)."""
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
