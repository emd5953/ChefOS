"""A second org must not see, change, or write the first org's integration data."""

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import scoped_session, unscoped_session
from tests.integrations.conftest import Clock, Connect, Connected, load_days

TABLES = [
    "integration_connections",
    "integration_location_links",
    "integration_streams",
    "sync_runs",
    "raw_records",
    "webhook_events",
    "external_refs",
    "mapping_issues",
    "menu_items",
    "menu_item_variants",
    "staff_members",
    "sales_orders",
    "sales_order_lines",
    "sales_order_returns",
    "timecards",
    "daily_sales",
    "daily_labor",
]


@pytest.fixture
async def two(connect: Connect, clock: Clock) -> tuple[Connected, Connected]:
    a, b = await connect(), await connect()
    for c in (a, b):
        await load_days(c, clock, date(2026, 3, 5), date(2026, 3, 6))
    async with scoped_session(a.org_id) as db:
        await db.execute(
            text(
                "INSERT INTO webhook_events (org_id, provider, event_id, connection_id, event_type, received_at) "
                "VALUES (:o, 'square', :e, :c, 'order.updated', now())"
            ),
            {"o": a.org_id, "e": uuid.uuid4().hex, "c": a.connection_id},
        )
    return a, b


async def test_other_org_sees_nothing_and_changes_nothing(two: tuple[Connected, Connected]) -> None:
    a, b = two
    for table in TABLES:
        async with scoped_session(a.org_id) as db:
            assert await db.scalar(text(f"SELECT count(*) FROM {table}")) >= 1, table  # has data
        async with scoped_session(b.org_id) as db:
            seen = await db.scalar(
                text(f"SELECT count(*) FROM {table} WHERE org_id = :o"), {"o": a.org_id}
            )
            assert seen == 0, table
            deleted = await db.execute(
                text(f"DELETE FROM {table} WHERE org_id = :o"), {"o": a.org_id}
            )
            assert deleted.rowcount == 0, table
        async with scoped_session(a.org_id) as db:  # still intact
            kept = await db.scalar(
                text(f"SELECT count(*) FROM {table} WHERE org_id = :o"), {"o": a.org_id}
            )
            assert kept >= 1, table


async def test_no_scope_sees_nothing(two: tuple[Connected, Connected]) -> None:
    for table in TABLES:
        async with unscoped_session() as db:
            assert await db.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table


async def test_cannot_write_into_another_org(two: tuple[Connected, Connected]) -> None:
    a, b = two
    with pytest.raises(DBAPIError, match="row-level security"):
        async with scoped_session(b.org_id) as db:
            await db.execute(
                text(
                    "INSERT INTO mapping_issues (id, org_id, connection_id, kind, external_id, label, "
                    "first_seen_at, last_seen_at) VALUES (:i, :o, :c, 'unmapped_item', 'x', 'x', now(), now())"
                ),
                {"i": uuid.uuid4(), "o": a.org_id, "c": a.connection_id},
            )


async def test_api_hides_other_orgs_connection_and_data(two: tuple[Connected, Connected]) -> None:
    a, b = two
    r = await b.client.get(f"/v1/integrations/connections/{a.connection_id}")
    assert r.status_code == 404
    listed = (await b.client.get("/v1/integrations/connections")).json()["data"]
    assert [c["id"] for c in listed] == [str(b.connection_id)]
    r = await b.client.get(
        "/v1/commerce/daily",
        params={"location_id": str(a.location_id), "from": "2026-03-05", "to": "2026-03-06"},
    )
    assert r.status_code == 404
    assert (
        await b.client.delete(f"/v1/integrations/connections/{a.connection_id}", headers=b.headers)
    ).status_code == 404
