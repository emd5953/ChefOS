"""Commerce queries. Read only `daily_sales`, `daily_labor`, and order lines/orders aggregates,
never raw records."""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_date import business_date
from app.commerce.schemas import (
    CHANNELS,
    CategoryMixOut,
    ChangeOut,
    ChannelShare,
    DailyOut,
    DayOut,
    FreshnessOut,
    HourOut,
    ItemOut,
    ItemsOut,
    SummaryOut,
    TodayOut,
    Totals,
)
from app.errors import not_found, validation_error
from app.identity.models import Location
from app.integrations.freshness import location_freshness

MAX_RANGE_DAYS = 800


def validate_range(first: date, last: date) -> None:
    if first > last:
        raise validation_error({"from": "Must be on or before `to`"})
    if (last - first).days >= MAX_RANGE_DAYS:
        raise validation_error({"to": f"Range is limited to {MAX_RANGE_DAYS} days"})


async def get_location(
    db: AsyncSession, allowed: list[uuid.UUID], location_id: uuid.UUID
) -> Location:
    loc = await db.get(Location, location_id) if location_id in allowed else None
    if loc is None:
        raise not_found("Location not found")
    return loc


@dataclass
class Agg:
    orders: int = 0
    net: int = 0
    gross: int = 0
    discounts: int = 0
    returns: int = 0
    tax: int = 0
    tips: int = 0
    labor_cost: int = 0
    labor_hours: Decimal = Decimal(0)
    channels: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    channel_orders: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def totals(self) -> Totals:
        return Totals(
            net_sales=self.net,
            gross_sales=self.gross,
            discounts=self.discounts,
            returns=self.returns,
            tax=self.tax,
            tips=self.tips,
            orders=self.orders,
            avg_check=(2 * self.net + self.orders) // (2 * self.orders)
            if self.orders > 0
            else None,
            labor_cost=self.labor_cost,
            labor_hours=round(float(self.labor_hours), 2),
            labor_pct=round(self.labor_cost / self.net * 100, 1) if self.net > 0 else None,
        )


_SALES = text("""
SELECT business_date, channel, orders, net_sales, gross_sales, discounts, returns, tax, tips
FROM daily_sales
WHERE org_id = :org AND location_id = :loc AND business_date BETWEEN :f AND :t
""")
_LABOR = text("""
SELECT business_date, actual_hours, actual_cost FROM daily_labor
WHERE org_id = :org AND location_id = :loc AND business_date BETWEEN :f AND :t
""")


async def _aggregate_by_day(
    db: AsyncSession, org_id: uuid.UUID, loc: uuid.UUID, first: date, last: date
) -> dict[date, Agg]:
    p = {"org": org_id, "loc": loc, "f": first, "t": last}
    days: dict[date, Agg] = defaultdict(Agg)
    for r in await db.execute(_SALES, p):
        a = days[r.business_date]
        a.orders += r.orders
        a.net += r.net_sales
        a.gross += r.gross_sales
        a.discounts += r.discounts
        a.returns += r.returns
        a.tax += r.tax
        a.tips += r.tips
        a.channels[r.channel] += r.net_sales
        a.channel_orders[r.channel] += r.orders
    for r in await db.execute(_LABOR, p):
        a = days[r.business_date]
        a.labor_hours += r.actual_hours
        a.labor_cost += r.actual_cost
    return days


def _sum(aggs: list[Agg]) -> Agg:
    total = Agg()
    for a in aggs:
        total.orders += a.orders
        total.net += a.net
        total.gross += a.gross
        total.discounts += a.discounts
        total.returns += a.returns
        total.tax += a.tax
        total.tips += a.tips
        total.labor_cost += a.labor_cost
        total.labor_hours += a.labor_hours
        for ch, v in a.channels.items():
            total.channels[ch] += v
        for ch, n in a.channel_orders.items():
            total.channel_orders[ch] += n
    return total


async def _freshness(db: AsyncSession, location_id: uuid.UUID) -> list[FreshnessOut]:
    return [
        FreshnessOut(
            stream=f.stream,
            location_id=f.location_id,
            data_as_of=f.data_as_of,
            status=f.status,
        )
        for f in await location_freshness(db, location_id)
    ]


async def daily(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, first: date, last: date
) -> DailyOut:
    validate_range(first, last)
    by_day = await _aggregate_by_day(db, org_id, loc.id, first, last)
    days: list[DayOut] = []
    d = first
    while d <= last:
        a = by_day.get(d) or Agg()
        days.append(
            DayOut(
                business_date=d,
                channels={c: a.channels.get(c, 0) for c in CHANNELS},
                **a.totals().model_dump(),
            )
        )
        d += timedelta(days=1)
    return DailyOut(location_id=loc.id, days=days, freshness=await _freshness(db, loc.id))


def _pct_change(cur: int | float, prev: int | float) -> float | None:
    if prev == 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def compare_range(first: date, last: date, compare: str) -> tuple[date, date]:
    if compare == "previous_year":
        shift = timedelta(days=364)  # weekdays line up
        return first - shift, last - shift
    length = (last - first).days + 1
    prev_last = first - timedelta(days=1)
    return prev_last - timedelta(days=length - 1), prev_last


async def summary(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, first: date, last: date, compare: str
) -> SummaryOut:
    validate_range(first, last)
    p_first, p_last = compare_range(first, last, compare)
    cur_days = await _aggregate_by_day(db, org_id, loc.id, first, last)
    prev_days = await _aggregate_by_day(db, org_id, loc.id, p_first, p_last)
    cur, prev = _sum(list(cur_days.values())), _sum(list(prev_days.values()))
    ct, pt = cur.totals(), prev.totals()
    shares = [
        ChannelShare(
            channel=c,
            net_sales=cur.channels.get(c, 0),
            share_pct=round(cur.channels.get(c, 0) / cur.net * 100, 1) if cur.net > 0 else 0.0,
        )
        for c in CHANNELS
        if cur.channels.get(c, 0) != 0 or cur.channel_orders.get(c, 0) > 0
    ]
    shares.sort(key=lambda s: -s.net_sales)
    return SummaryOut(
        period={"from": first, "to": last},
        compare_period={"from": p_first, "to": p_last},
        current=ct,
        previous=pt,
        change=ChangeOut(
            net_sales_pct=_pct_change(ct.net_sales, pt.net_sales),
            orders_pct=_pct_change(ct.orders, pt.orders),
            avg_check_pct=(
                _pct_change(ct.avg_check, pt.avg_check)
                if ct.avg_check is not None and pt.avg_check is not None
                else None
            ),
            labor_pct_pts=(
                round(ct.labor_pct - pt.labor_pct, 1)
                if ct.labor_pct is not None and pt.labor_pct is not None
                else None
            ),
        ),
        channels=shares,
        freshness=await _freshness(db, loc.id),
    )


_ITEMS_TOTAL = text("""
SELECT COALESCE(sum(net), 0) AS total,
       COALESCE(sum(net) FILTER (WHERE menu_item_variant_id IS NULL), 0) AS unmapped
FROM sales_order_lines
WHERE org_id = :org AND location_id = :loc AND business_date BETWEEN :f AND :t
""")
_ITEMS_TOP = text("""
SELECT COALESCE(mi.name, l.name) AS name,
       COALESCE(NULLIF(mi.category, ''), 'Uncategorized') AS category,
       sum(l.quantity) AS quantity, sum(l.net) AS net
FROM sales_order_lines l
LEFT JOIN menu_item_variants v ON v.id = l.menu_item_variant_id
LEFT JOIN menu_items mi ON mi.id = v.menu_item_id
WHERE l.org_id = :org AND l.location_id = :loc AND l.business_date BETWEEN :f AND :t
GROUP BY COALESCE(mi.id::text, 'ad-hoc:' || l.name), 1, 2
ORDER BY net DESC, name
LIMIT :n
""")

_ITEMS_CATEGORIES = text("""
SELECT COALESCE(NULLIF(mi.category, ''), 'Uncategorized') AS category, sum(l.net) AS net
FROM sales_order_lines l
JOIN menu_item_variants v ON v.id = l.menu_item_variant_id
JOIN menu_items mi ON mi.id = v.menu_item_id
WHERE l.org_id = :org AND l.location_id = :loc AND l.business_date BETWEEN :f AND :t
GROUP BY 1
ORDER BY net DESC, category ASC
""")


_ITEMS_IN_CATEGORY = text("""
SELECT COALESCE(mi.name, l.name) AS name,
       COALESCE(NULLIF(mi.category, ''), 'Uncategorized') AS category,
       sum(l.quantity) AS quantity, sum(l.net) AS net
FROM sales_order_lines l
LEFT JOIN menu_item_variants v ON v.id = l.menu_item_variant_id
LEFT JOIN menu_items mi ON mi.id = v.menu_item_id
WHERE l.org_id = :org AND l.location_id = :loc AND l.business_date BETWEEN :f AND :t
  AND COALESCE(NULLIF(mi.category, ''), 'Uncategorized') = :category
GROUP BY COALESCE(mi.id::text, 'ad-hoc:' || l.name), 1, 2
ORDER BY net DESC, name ASC
LIMIT :n
""")


async def items(
    db: AsyncSession,
    org_id: uuid.UUID,
    loc: Location,
    first: date,
    last: date,
    limit: int,
    category: str | None = None,
) -> ItemsOut:
    validate_range(first, last)
    p = {"org": org_id, "loc": loc.id, "f": first, "t": last}
    totals = (await db.execute(_ITEMS_TOTAL, p)).one()
    total = int(totals.total)
    category_rows = await db.execute(_ITEMS_CATEGORIES, p)
    categories = [
        CategoryMixOut(
            category=r.category,
            net_sales=int(r.net),
            share_pct=round(int(r.net) / total * 100, 1) if total > 0 else 0.0,
        )
        for r in category_rows
    ]
    item_query = _ITEMS_TOP if category is None else _ITEMS_IN_CATEGORY
    rows = await db.execute(item_query, {**p, "n": limit, "category": category})
    return ItemsOut(
        total_net_sales=total,
        categories=categories,
        items=[
            ItemOut(
                name=r.name,
                category=r.category,
                quantity=float(r.quantity),
                net_sales=int(r.net),
                share_pct=round(int(r.net) / total * 100, 1) if total > 0 else 0.0,
            )
            for r in rows
        ],
        unmapped_net_sales=int(totals.unmapped),
    )


_HOURLY = text("""
SELECT h AS hour, sum(net) AS net, sum(orders) AS orders FROM (
  SELECT extract(hour FROM closed_at AT TIME ZONE :tz)::int AS h,
         gross_sales - discounts AS net, 1 AS orders
  FROM sales_orders
  WHERE org_id = :org AND location_id = :loc AND business_date = :d AND state = 'completed'
  UNION ALL
  SELECT extract(hour FROM processed_at AT TIME ZONE :tz)::int, -amount, 0
  FROM sales_order_returns
  WHERE org_id = :org AND location_id = :loc AND business_date = :d
) x GROUP BY h
""")
_LAST_ORDER = text(
    "SELECT max(closed_at) FROM sales_orders "
    "WHERE org_id = :org AND location_id = :loc AND business_date = :d AND state = 'completed'"
)


async def today(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, now: datetime | None = None
) -> TodayOut:
    now = now or datetime.now(UTC)
    d = business_date(now, loc.timezone, loc.business_day_start)
    agg = (await _aggregate_by_day(db, org_id, loc.id, d, d)).get(d) or Agg()
    p = {"org": org_id, "loc": loc.id, "d": d}
    hours = {
        r.hour: (int(r.net), int(r.orders))
        for r in await db.execute(_HOURLY, {**p, "tz": loc.timezone})
    }
    last_order = await db.scalar(_LAST_ORDER, p)
    t = agg.totals()
    return TodayOut(
        business_date=d,
        net_sales=t.net_sales,
        orders=t.orders,
        avg_check=t.avg_check,
        last_order_at=last_order,
        by_hour=[
            HourOut(hour=h, net_sales=hours.get(h, (0, 0))[0], orders=hours.get(h, (0, 0))[1])
            for h in range(24)
        ],
        freshness=await _freshness(db, loc.id),
    )
