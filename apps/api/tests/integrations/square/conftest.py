import collections
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.integrations.providers.square import (
    CanonicalReturn,
    CanonicalSalesOrder,
    CanonicalTimecard,
    Credentials,
    FakeSquareClient,
    MappingContext,
    MappingIssueSignal,
    RawItem,
    normalize_order,
    normalize_timecard,
)
from app.integrations.providers.square.fake_data import LOCATION_ID

# 01:00 local on Wed 2026-07-01: Tuesday 06-30 is fully closed, nobody is on shift.
NOW = datetime(2026, 7, 1, 5, 0, tzinfo=UTC)
TZ = "America/New_York"
LOC = [LOCATION_ID]


def midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def walk_orders(
    client: FakeSquareClient, creds: Credentials, start: datetime, end: datetime, limit: int = 500
) -> list[RawItem]:
    out: list[RawItem] = []
    cursor: str | None = None
    while True:
        page = client.search_orders(creds, LOC, start, end, cursor, limit)
        out.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            return out


def walk_timecards(
    client: FakeSquareClient, creds: Credentials, start: date, end: date
) -> list[RawItem]:
    out: list[RawItem] = []
    cursor: str | None = None
    while True:
        page = client.search_timecards(creds, LOC, start, end, cursor)
        out.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            return out


def catalog_ctx(client: FakeSquareClient, creds: Credentials) -> MappingContext:
    variants: dict[str, str] = {}
    cursor: str | None = None
    while True:
        page = client.search_catalog(creds, None, cursor)
        for it in page.items:
            if it.payload["type"] == "ITEM":
                for v in it.payload["item_data"]["variations"]:
                    variants[v["id"]] = v["id"]
        cursor = page.next_cursor
        if cursor is None:
            break
    members: set[str] = set()
    cursor = None
    while True:
        tpage = client.search_team_members(creds, LOC, cursor)
        members.update(i.external_id for i in tpage.items)
        cursor = tpage.next_cursor
        if cursor is None:
            break
    return MappingContext(
        TZ, resolve_catalog=variants.get, is_known_team_member=members.__contains__
    )


@dataclass
class History:
    client: FakeSquareClient
    creds: Credentials
    ctx: MappingContext
    raw_orders: list[RawItem]
    orders: list[CanonicalSalesOrder]
    returns: list[CanonicalReturn]
    timecards: list[CanonicalTimecard]
    signals: list[MappingIssueSignal]
    seconds: float
    first_day: date
    last_day: date
    net_by_date: dict[date, int] = field(default_factory=dict)
    labor_by_date: dict[date, int] = field(default_factory=dict)


@pytest.fixture(scope="session")
def now() -> datetime:
    return NOW


@pytest.fixture(scope="session")
def client() -> FakeSquareClient:
    return FakeSquareClient(clock=lambda: NOW)


@pytest.fixture(scope="session")
def creds(client: FakeSquareClient) -> Credentials:
    return client.obtain_token("fake-code", "https://example.com/cb")


@pytest.fixture(scope="session")
def history(client: FakeSquareClient, creds: Credentials) -> History:
    """Full 400-day backfill (30-day updated_at windows) + normalization, timed."""
    t0 = time.perf_counter()
    ctx = catalog_ctx(client, creds)
    raw: list[RawItem] = []
    start = midnight(client.epoch)
    while start < NOW:
        end = start + timedelta(days=30)
        raw.extend(walk_orders(client, creds, start, end))
        start = end
    orders: list[CanonicalSalesOrder] = []
    returns: list[CanonicalReturn] = []
    signals: list[MappingIssueSignal] = []
    for item in raw:
        res = normalize_order(item.payload, ctx)
        orders.append(res.order)
        returns.extend(res.returns)
        signals.extend(res.signals)
    timecards: list[CanonicalTimecard] = []
    for item in walk_timecards(client, creds, client.epoch, NOW.date()):
        tres = normalize_timecard(item.payload, ctx)
        timecards.append(tres.timecard)
        signals.extend(tres.signals)
    seconds = time.perf_counter() - t0

    net: dict[date, int] = collections.defaultdict(int)
    for o in orders:
        net[o.business_date] += o.net_sales
    for r in returns:
        net[r.business_date] -= r.net_cents
    labor: dict[date, int] = collections.defaultdict(int)
    for tc in timecards:
        labor[tc.business_date] += tc.cost_cents
    return History(
        client, creds, ctx, raw, orders, returns, timecards, signals, seconds,
        client.epoch, NOW.astimezone(ZoneInfo(TZ)).date() - timedelta(days=1), dict(net), dict(labor),
    )  # fmt: skip


def payload_hash(items: list[RawItem]) -> list[tuple[str, str]]:
    import hashlib
    import json

    return [
        (i.external_id, hashlib.sha256(json.dumps(i.payload, sort_keys=True).encode()).hexdigest())
        for i in items
    ]


def make_order(**overrides: Any) -> dict[str, Any]:
    """Minimal hand-built Square order for normalizer tests."""
    base: dict[str, Any] = {
        "id": "O1",
        "location_id": "L1",
        "created_at": "2026-03-10T23:00:00.000Z",
        "updated_at": "2026-03-11T00:00:00.000Z",
        "closed_at": "2026-03-11T00:00:00.000Z",
        "state": "COMPLETED",
        "version": 3,
        "source": {"name": "Square Point of Sale"},
        "line_items": [
            {
                "uid": "a", "name": "Burger", "quantity": "2", "item_type": "ITEM",
                "catalog_object_id": "VAR_BURGER",
                "base_price_money": {"amount": 2400, "currency": "USD"},
                "gross_sales_money": {"amount": 4800, "currency": "USD"},
                "total_discount_money": {"amount": 480, "currency": "USD"},
                "total_tax_money": {"amount": 383, "currency": "USD"},
                "total_money": {"amount": 4703, "currency": "USD"},
            }
        ],
        "total_tax_money": {"amount": 383, "currency": "USD"},
        "total_tip_money": {"amount": 800, "currency": "USD"},
        "total_service_charge_money": {"amount": 130, "currency": "USD"},
        "total_money": {"amount": 5633, "currency": "USD"},
    }  # fmt: skip
    base.update(overrides)
    return base
