"""Row-level security tests, connecting as chefos_app."""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import get_engine, scoped_session, unscoped_session
from app.identity.schemas import SignupRequest
from app.identity.service import Registration, register_account

RLS_TABLES = ["organizations", "memberships", "locations", "location_access", "audit_events"]
# Integration layer and commerce tables (behavior is covered in tests/integrations/).
INTEGRATION_RLS_TABLES = {
    "integration_connections", "integration_location_links", "integration_streams", "sync_runs",
    "raw_records", "webhook_events", "external_refs", "mapping_issues", "menu_items",
    "menu_item_variants", "staff_members", "sales_orders", "sales_order_lines",
    "sales_order_returns", "timecards", "daily_sales", "daily_labor",
    "suppliers", "inventory_items", "item_unit_conversions", "item_packs", "storage_areas",
    "item_storage_assignments", "inventory_pars", "inventory_levels",
    "count_sessions", "count_lines", "stock_movements", "purchase_orders",
    "purchase_order_lines", "receipts", "receipt_lines", "recipes", "recipe_ingredients",
    "food_cost_baselines", "recipe_versions", "recipe_version_ingredients", "menu_recipe_assignments",
}  # fmt: skip
# Team tables (behavior is covered in tests/team/test_rls_team.py).
TEAM_RLS_TABLES = {
    "positions", "employee_positions", "stations", "employee_locations", "invitations",
    "schedules", "shifts", "published_shifts", "notifications", "availability_sets",
    "availability_blocks", "time_off_requests", "shift_coverage_requests", "shift_trade_requests",
    "sous_schedule_proposals",
}  # fmt: skip
# Private conversation behavior is covered in tests/sous/test_conversations.py.
SOUS_RLS_TABLES = {"sous_conversations", "sous_turns"}
# Tables with an org_id column that are intentionally NOT under RLS (accessed only by auth code
# before org scope exists). Adding to this set requires a reason.
NO_RLS_ALLOWED = {"sessions"}
ORG_COLUMN = {"organizations": "id"}


def _col(table: str) -> str:
    return ORG_COLUMN.get(table, "org_id")


async def _register(tag: str) -> Registration:
    return await register_account(
        SignupRequest(
            email=f"{tag}-{uuid.uuid4().hex[:8]}@example.com",
            password="correct-horse-battery-9",
            display_name=tag,
            org_name=f"Org {tag}",
            location_name=f"Loc {tag}",
            timezone="UTC",
        )
    )


@pytest_asyncio.fixture
async def two_orgs(owner_engine: AsyncEngine) -> AsyncIterator[tuple[Registration, Registration]]:
    a, b = await _register("a"), await _register("b")
    async with owner_engine.begin() as conn:  # location_access has no API yet
        for reg in (a, b):
            await conn.execute(
                text(
                    "INSERT INTO location_access (org_id, user_id, location_id) VALUES (:o,:u,:l)"
                ),
                {"o": reg.org.id, "u": reg.user.id, "l": reg.location.id},
            )
    yield a, b


async def test_app_role_cannot_bypass_rls() -> None:
    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
        assert (await conn.scalar(text("SELECT current_user"))) == "chefos_app"
    assert row.rolbypassrls is False
    assert row.rolsuper is False


@pytest.mark.parametrize("table", RLS_TABLES)
async def test_cannot_read_update_delete_other_org(
    table: str, two_orgs: tuple[Registration, Registration]
) -> None:
    a, b = two_orgs
    col = _col(table)
    async with scoped_session(a.org.id) as db:
        assert (
            await db.scalar(text(f"SELECT count(*) FROM {table} WHERE {col} = :o"), {"o": b.org.id})
            == 0
        )
        assert (
            await db.scalar(text(f"SELECT count(*) FROM {table} WHERE {col} = :o"), {"o": a.org.id})
            >= 1
        )
        if table != "audit_events":  # audit is append-only; covered below
            upd = {
                "organizations": "name = 'x'",
                "memberships": "role = 'admin'",
                "locations": "name = 'x'",
                "location_access": "location_id = location_id",
            }[table]
            r = await db.execute(
                text(f"UPDATE {table} SET {upd} WHERE {col} = :o"), {"o": b.org.id}
            )
            assert r.rowcount == 0
            r = await db.execute(text(f"DELETE FROM {table} WHERE {col} = :o"), {"o": b.org.id})
            assert r.rowcount == 0
    # B's data is intact
    async with scoped_session(b.org.id) as db:
        assert (
            await db.scalar(text(f"SELECT count(*) FROM {table} WHERE {col} = :o"), {"o": b.org.id})
            >= 1
        )


@pytest.mark.parametrize("table", RLS_TABLES)
async def test_no_scope_returns_zero_rows(
    table: str, two_orgs: tuple[Registration, Registration]
) -> None:
    async with unscoped_session() as db:
        assert await db.scalar(text(f"SELECT count(*) FROM {table}")) == 0


async def test_insert_into_other_org_is_rejected(
    two_orgs: tuple[Registration, Registration],
) -> None:
    a, b = two_orgs
    with pytest.raises(DBAPIError, match="row-level security"):
        async with scoped_session(a.org.id) as db:
            await db.execute(
                text(
                    "INSERT INTO locations (id, org_id, name, timezone) VALUES (:i, :o, 'x', 'UTC')"
                ),
                {"i": uuid.uuid4(), "o": b.org.id},
            )


async def test_insert_without_scope_is_rejected(
    two_orgs: tuple[Registration, Registration],
) -> None:
    a, _ = two_orgs
    with pytest.raises(DBAPIError, match="row-level security"):
        async with unscoped_session() as db:
            await db.execute(
                text(
                    "INSERT INTO locations (id, org_id, name, timezone) VALUES (:i, :o, 'x', 'UTC')"
                ),
                {"i": uuid.uuid4(), "o": a.org.id},
            )


async def test_scope_does_not_leak_across_transactions(
    two_orgs: tuple[Registration, Registration],
) -> None:
    a, _ = two_orgs
    async with get_engine().connect() as conn:
        async with conn.begin():
            await conn.execute(
                text("SELECT set_config('app.org_id', :o, true)"), {"o": str(a.org.id)}
            )
            assert await conn.scalar(text("SELECT count(*) FROM locations")) >= 1
        async with conn.begin():  # same connection, new transaction: scope must be gone
            assert await conn.scalar(text("SELECT count(*) FROM locations")) == 0


async def test_audit_events_are_append_only(two_orgs: tuple[Registration, Registration]) -> None:
    a, _ = two_orgs
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with scoped_session(a.org.id) as db:
            await db.execute(text("UPDATE audit_events SET action = 'tampered'"))
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with scoped_session(a.org.id) as db:
            await db.execute(text("DELETE FROM audit_events"))


async def test_app_role_has_no_ddl() -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with unscoped_session() as db:
            await db.execute(text("CREATE TABLE should_not_exist (id int)"))


async def test_every_org_scoped_table_has_rls(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        tables = {
            r[0]
            for r in await conn.execute(
                text(
                    "SELECT DISTINCT table_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = 'org_id'"
                )
            )
        } | {"organizations"}
        rls = {
            r.relname: r.relrowsecurity
            for r in await conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                )
            )
        }
        policies = {
            r[0]
            for r in await conn.execute(
                text("SELECT DISTINCT tablename FROM pg_policies WHERE schemaname = 'public'")
            )
        }
    missing = {t for t in tables - NO_RLS_ALLOWED if not rls.get(t) or t not in policies}
    assert not missing, f"tables with org_id but no RLS/policy: {sorted(missing)}"
    assert set(RLS_TABLES) <= tables
    assert tables >= INTEGRATION_RLS_TABLES
    assert policies >= INTEGRATION_RLS_TABLES
    assert all(rls[t] for t in INTEGRATION_RLS_TABLES)
    assert tables >= TEAM_RLS_TABLES
    assert policies >= TEAM_RLS_TABLES
    assert all(rls[t] for t in TEAM_RLS_TABLES)
    assert tables >= SOUS_RLS_TABLES
    assert policies >= SOUS_RLS_TABLES
    assert all(rls[t] for t in SOUS_RLS_TABLES)
