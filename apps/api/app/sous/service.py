"""Daily briefing (SOUS-2): a deterministic view of one location's day, built per request from the
same domain services Sous reads. No model, no persistence, so it works with the model offline.

Sections: yesterday's sales and labor (`commerce.daily`), today's published staffing (the
published schedule snapshot, never the draft), and items below par (inventory levels and pars).
Each section runs in its own savepoint; one that fails is reported as unavailable and the rest
still render. Freshness and limitations are computed here, never inferred from missing rows.
"""

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_date import business_date, get_zone
from app.commerce import service as commerce
from app.commerce.schemas import DayOut
from app.errors import validation_error
from app.formatting import local_timestamp
from app.identity.models import Location
from app.integrations.freshness import FreshnessItem, location_freshness
from app.sous.schemas import (
    BriefingFreshness,
    BriefingItem,
    BriefingLabor,
    BriefingOut,
    BriefingSales,
    BriefingShift,
    BriefingStaffing,
    BriefingStock,
    Evidence,
    Unit,
)
from app.team.models import Position, PublishedShift, Schedule, StaffMember
from app.team.scheduling.builder import label_time, paid_hours

log = structlog.get_logger()

PAST_DAYS = 400  # oldest briefing date, relative to today
FUTURE_DAYS = 14  # schedules are published ahead; sales and stock can't be
MAX_SHIFTS = 50
MAX_ITEMS = 20

SALES_SOURCE = "commerce.daily"
STAFFING_SOURCE = "team.published_schedule"
STOCK_SOURCE = "inventory.levels"
STREAM_LABEL = {"orders": "Sales", "timecards": "Labor"}


def _now() -> datetime:
    return datetime.now(UTC)


async def _guarded[T](
    db: AsyncSession, name: str, run: Callable[[], Awaitable[T]], failed: Callable[[], T]
) -> T:
    """Run one section in a savepoint so a failed query doesn't poison the others."""
    try:
        async with db.begin_nested():
            return await run()
    except Exception as exc:
        log.warning("briefing_section_error", section=name, error=type(exc).__name__)
        return failed()


def money(cents: int) -> str:
    """Matches the web's `formatMoney`: whole dollars from $1,000."""
    dollars = cents / 100
    return f"${dollars:,.0f}" if abs(dollars) >= 1000 else f"${dollars:,.2f}"


def _day_label(d: date) -> str:
    return f"{d.strftime('%a')} {d.strftime('%b')} {d.day}"


# --- sales and labor -------------------------------------------------------------------------


async def _results(
    db: AsyncSession,
    org_id: uuid.UUID,
    loc: Location,
    day: date,
    fresh: dict[str, FreshnessItem],
) -> tuple[BriefingSales, BriefingLabor]:
    compare = day - timedelta(days=7)
    out = await commerce.daily(db, org_id, loc, compare, day)
    cur, prev = out.days[-1], out.days[0]
    return _sales(cur, prev, fresh.get("orders")), _labor(cur, fresh.get("timecards"))


def _sales(cur: DayOut, prev: DayOut, f: FreshnessItem | None) -> BriefingSales:
    base = {"source": SALES_SOURCE, "business_date": cur.business_date}
    as_of = f.data_as_of if f else None
    if cur.orders == 0:
        if f is not None and f.status == "not_connected":
            return BriefingSales(
                status="unavailable", as_of=None, note="No sales source is connected.", **base
            )
        return BriefingSales(
            status="no_data", as_of=as_of, note="No sales were recorded for this day.", **base
        )
    change = (
        round((cur.net_sales - prev.net_sales) / prev.net_sales * 100, 1)
        if prev.orders > 0 and prev.net_sales > 0
        else None
    )
    return BriefingSales(
        status="ok",
        as_of=as_of,
        net_sales=cur.net_sales,
        orders=cur.orders,
        avg_check=cur.avg_check,
        compare_date=prev.business_date,
        compare_net_sales=prev.net_sales if prev.orders > 0 else None,
        net_sales_change_pct=change,
        **base,
    )


def _labor(cur: DayOut, f: FreshnessItem | None) -> BriefingLabor:
    base = {"source": SALES_SOURCE, "business_date": cur.business_date}
    as_of = f.data_as_of if f else None
    if cur.labor_hours == 0 and cur.labor_cost == 0:
        # No timecards is missing labor, not zero labor: never report 0 hours or 0%.
        if f is not None and f.status == "not_connected":
            return BriefingLabor(
                status="unavailable", as_of=None, note="No timecard source is connected.", **base
            )
        return BriefingLabor(
            status="no_data",
            as_of=as_of,
            note="No timecards were recorded for this day, so labor is unknown (not zero).",
            **base,
        )
    return BriefingLabor(
        status="ok",
        as_of=as_of,
        actual_hours=cur.labor_hours,
        actual_cost=cur.labor_cost,
        labor_pct=cur.labor_pct,
        **base,
    )


# --- staffing --------------------------------------------------------------------------------


async def _staffing(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, day: date
) -> BriefingStaffing:
    week_start = day - timedelta(days=day.weekday())
    sched = await db.scalar(
        select(Schedule).where(
            Schedule.org_id == org_id,
            Schedule.location_id == loc.id,
            Schedule.week_start == week_start,
        )
    )
    base = {"source": STAFFING_SOURCE, "business_date": day}
    if sched is None or sched.published_version is None:
        return BriefingStaffing(
            status="no_data", as_of=None, note="No schedule is published for this week.", **base
        )
    rows = (
        await db.execute(
            select(PublishedShift, Position.name, StaffMember.display_name)
            .join(Position, Position.id == PublishedShift.position_id)
            .outerjoin(StaffMember, StaffMember.id == PublishedShift.employee_id)
            .where(
                PublishedShift.org_id == org_id,
                PublishedShift.schedule_id == sched.id,
                PublishedShift.business_date == day,
            )
            .order_by(PublishedShift.starts_at, PublishedShift.id)
        )
    ).all()
    if not rows:
        return BriefingStaffing(
            status="no_data",
            as_of=sched.published_at,
            published_at=sched.published_at,
            note="The published schedule has no shifts on this day.",
            **base,
        )
    shifts = [
        BriefingShift(
            employee_name=name,
            position_name=position,
            starts_at=s.starts_at,
            ends_at=s.ends_at,
            paid_hours=round(float(paid_hours(s.starts_at, s.ends_at, s.break_minutes)), 2),
        )
        for s, position, name in rows
    ]
    return BriefingStaffing(
        status="ok",
        as_of=sched.published_at,
        published_at=sched.published_at,
        shifts=len(shifts),
        scheduled_hours=round(sum(s.paid_hours for s in shifts), 2),
        unassigned_shifts=sum(1 for s, _, _ in rows if s.employee_id is None),
        first_start=min(s.starts_at for s in shifts),
        last_end=max(s.ends_at for s in shifts),
        by_position=dict(Counter(s.position_name for s in shifts).most_common()),
        shift_list=shifts[:MAX_SHIFTS],
        **base,
    )


# --- stock -----------------------------------------------------------------------------------

# Same below-par rule as GET /v1/inventory/items: a par is set and on hand (0 if never stocked)
# is under it. Inactive items don't alert.
_BELOW_PAR = text("""
SELECT i.id, i.name, i.category, i.base_unit,
       COALESCE(l.on_hand_base_quantity, 0) AS on_hand, p.par_base_quantity AS par,
       l.updated_at AS as_of
FROM inventory_items i
JOIN inventory_pars p ON p.org_id = i.org_id AND p.item_id = i.id AND p.location_id = :loc
LEFT JOIN inventory_levels l ON l.org_id = i.org_id AND l.item_id = i.id AND l.location_id = :loc
WHERE i.org_id = :org AND i.active AND COALESCE(l.on_hand_base_quantity, 0) < p.par_base_quantity
ORDER BY (p.par_base_quantity - COALESCE(l.on_hand_base_quantity, 0))
         / NULLIF(p.par_base_quantity, 0) DESC NULLS LAST, i.name
""")
_PAR_COUNTS = text("""
SELECT count(*) FILTER (WHERE p.item_id IS NOT NULL) AS with_par,
       count(*) FILTER (WHERE p.item_id IS NULL) AS without_par,
       (SELECT max(updated_at) FROM inventory_levels WHERE org_id = :org AND location_id = :loc)
         AS levels_as_of
FROM inventory_items i
LEFT JOIN inventory_pars p ON p.org_id = i.org_id AND p.item_id = i.id AND p.location_id = :loc
WHERE i.org_id = :org AND i.active
""")


async def _stock(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, day: date, today: date
) -> BriefingStock:
    p = {"org": org_id, "loc": loc.id}
    counts = (await db.execute(_PAR_COUNTS, p)).one()
    # Stock is current state: it is never "as of" a past or future briefing date.
    base = {"source": STOCK_SOURCE, "business_date": None, "as_of": counts.levels_as_of}
    if counts.with_par == 0:
        return BriefingStock(
            status="no_data",
            note="No pars are set at this location.",
            items_without_par=counts.without_par,
            below_par_count=None,
            **base,
        )
    rows = (await db.execute(_BELOW_PAR, p)).all()
    items = [
        BriefingItem(
            item_id=r.id,
            name=r.name,
            category=r.category,
            base_unit=r.base_unit,
            on_hand=float(r.on_hand),
            par=float(r.par),
            shortfall=float(r.par - r.on_hand),
            as_of=r.as_of,
        )
        for r in rows
    ]
    return BriefingStock(
        status="ok",
        below_par_count=len(items),
        items=items[:MAX_ITEMS],
        items_without_par=counts.without_par,
        note=None if day == today else "Stock levels are current, not as of the briefing date.",
        **base,
    )


# --- assembly --------------------------------------------------------------------------------


def _evidence(
    sales: BriefingSales, labor: BriefingLabor, staffing: BriefingStaffing, stock: BriefingStock
) -> list[Evidence]:
    ev: list[Evidence] = []

    def add(
        label: str, value: float | None, unit: Unit, source: str, as_of: datetime | None
    ) -> None:
        if value is not None:
            ev.append(Evidence(label=label, value=value, unit=unit, source=source, as_of=as_of))

    if sales.status == "ok" and sales.business_date:
        tag = f" ({sales.business_date.isoformat()})"
        add(f"Net sales{tag}", sales.net_sales, "usd_cents", SALES_SOURCE, sales.as_of)
        add(f"Orders{tag}", sales.orders, "count", SALES_SOURCE, sales.as_of)
        add(f"Average check{tag}", sales.avg_check, "usd_cents", SALES_SOURCE, sales.as_of)
        if sales.compare_date:
            add(
                f"Net sales ({sales.compare_date.isoformat()})",
                sales.compare_net_sales,
                "usd_cents",
                SALES_SOURCE,
                sales.as_of,
            )
            add(
                f"Net sales change{tag} vs {sales.compare_date.isoformat()}",
                sales.net_sales_change_pct,
                "percent",
                SALES_SOURCE,
                sales.as_of,
            )
    if labor.status == "ok" and labor.business_date:
        tag = f" ({labor.business_date.isoformat()})"
        add(f"Labor hours{tag}", labor.actual_hours, "hours", SALES_SOURCE, labor.as_of)
        add(f"Labor cost{tag}", labor.actual_cost, "usd_cents", SALES_SOURCE, labor.as_of)
        add(f"Labor %{tag}", labor.labor_pct, "percent", SALES_SOURCE, labor.as_of)
    if staffing.status == "ok" and staffing.business_date:
        tag = f" ({staffing.business_date.isoformat()})"
        at = staffing.as_of
        add(f"Published shifts{tag}", staffing.shifts, "count", STAFFING_SOURCE, at)
        add(f"Scheduled hours{tag}", staffing.scheduled_hours, "hours", STAFFING_SOURCE, at)
        add(f"Open shifts{tag}", staffing.unassigned_shifts, "count", STAFFING_SOURCE, at)
    if stock.status == "ok":
        add("Items below par", stock.below_par_count, "count", STOCK_SOURCE, stock.as_of)
    return ev


def _highlights(
    loc: Location,
    sales: BriefingSales,
    labor: BriefingLabor,
    staffing: BriefingStaffing,
    stock: BriefingStock,
) -> list[str]:
    out: list[str] = []
    if sales.status == "ok" and sales.business_date and sales.net_sales is not None:
        line = (
            f"{_day_label(sales.business_date)}: {money(sales.net_sales)} net sales "
            f"from {sales.orders:,} orders"
        )
        if sales.net_sales_change_pct is not None and sales.compare_date:
            direction = "up" if sales.net_sales_change_pct >= 0 else "down"
            line += (
                f", {direction} {abs(sales.net_sales_change_pct):.1f}% on "
                f"{sales.compare_date.strftime('%A')} {_day_label(sales.compare_date)[4:]}"
            )
        out.append(line + ".")
    if labor.status == "ok" and labor.actual_cost is not None:
        line = f"Labor was {money(labor.actual_cost)} over {labor.actual_hours:.1f} hours"
        if labor.labor_pct is not None:
            line += f", {labor.labor_pct:.1f}% of sales"
        out.append(line + ".")
    if staffing.status == "ok" and staffing.first_start and staffing.last_end:
        zone = get_zone(loc.timezone)
        line = (
            f"{staffing.shifts} published shifts, {staffing.scheduled_hours:.1f} scheduled hours, "
            f"{label_time(staffing.first_start, zone)} to {label_time(staffing.last_end, zone)}"
        )
        if staffing.unassigned_shifts:
            line += f"; {staffing.unassigned_shifts} open"
        out.append(line + ".")
    if stock.status == "ok" and stock.below_par_count is not None:
        if stock.below_par_count == 0:
            out.append("Every item with a par is at or above it.")
        else:
            names = ", ".join(i.name for i in stock.items[:3])
            more = stock.below_par_count - min(3, len(stock.items))
            out.append(
                f"{stock.below_par_count} item{'s' if stock.below_par_count != 1 else ''} "
                f"below par: {names}{f' and {more} more' if more > 0 else ''}."
            )
    return out


def _limitations(
    loc: Location,
    freshness: list[FreshnessItem] | None,
    sections: list[tuple[str, BriefingSales | BriefingLabor | BriefingStaffing | BriefingStock]],
) -> tuple[list[str], list[str]]:
    limits: list[str] = []
    unavailable: list[str] = []
    zone = get_zone(loc.timezone)
    if freshness is None:
        limits.append("Data freshness couldn't be checked; figures may be out of date.")
        unavailable.append("integrations.freshness")
    else:
        for f in freshness:
            label = STREAM_LABEL.get(f.stream)
            if label is None:
                continue
            if f.status == "stale":
                when = local_timestamp(f.data_as_of.astimezone(zone)) if f.data_as_of else "never"
                limits.append(f"{label} data is stale (last updated {when}).")
            elif f.status == "failing":
                limits.append(f"{label} sync is failing; figures may be incomplete.")
    for title, s in sections:
        if s.status == "unavailable":
            unavailable.append(s.source)
            limits.append(f"{title} unavailable ({s.source}): {s.note or 'the source failed.'}")
        elif s.note and (s.status == "no_data" or isinstance(s, BriefingStock)):
            limits.append(f"{title}: {s.note}")
    return limits, list(dict.fromkeys(unavailable))


def resolve_day(loc: Location, day: date | None, now: datetime) -> tuple[date, date]:
    """(briefing date, today's business date). A missing date is today at the location, so
    results cover the last completed business day."""
    today = business_date(now, loc.timezone, loc.business_day_start)
    if day is None:
        return today, today
    if day > today + timedelta(days=FUTURE_DAYS):
        raise validation_error({"date": f"At most {FUTURE_DAYS} days ahead"})
    if day < today - timedelta(days=PAST_DAYS):
        raise validation_error({"date": f"At most {PAST_DAYS} days back"})
    return day, today


async def briefing(
    db: AsyncSession,
    org_id: uuid.UUID,
    loc: Location,
    day: date | None,
    now: datetime | None = None,
) -> BriefingOut:
    now = now or _now()
    day, today = resolve_day(loc, day, now)
    results_day = day - timedelta(days=1)

    async def fresh() -> list[FreshnessItem] | None:
        return await location_freshness(db, loc.id, now)

    freshness = await _guarded(db, "freshness", fresh, lambda: None)
    by_stream = {f.stream: f for f in freshness or []}

    def results_failed() -> tuple[BriefingSales, BriefingLabor]:
        base = {"status": "unavailable", "source": SALES_SOURCE, "business_date": results_day}
        note = "Sales and labor couldn't be loaded."
        return (
            BriefingSales(as_of=None, note=note, **base),
            BriefingLabor(as_of=None, note=note, **base),
        )

    sales, labor = await _guarded(
        db,
        "results",
        lambda: _results(db, org_id, loc, results_day, by_stream),
        results_failed,
    )
    staffing = await _guarded(
        db,
        "staffing",
        lambda: _staffing(db, org_id, loc, day),
        lambda: BriefingStaffing(
            status="unavailable",
            source=STAFFING_SOURCE,
            business_date=day,
            as_of=None,
            note="The published schedule couldn't be loaded.",
        ),
    )
    stock = await _guarded(
        db,
        "stock",
        lambda: _stock(db, org_id, loc, day, today),
        lambda: BriefingStock(
            status="unavailable",
            source=STOCK_SOURCE,
            business_date=None,
            as_of=None,
            note="Stock levels couldn't be loaded.",
        ),
    )
    limitations, unavailable = _limitations(
        loc,
        freshness,
        [("Sales", sales), ("Labor", labor), ("Staffing", staffing), ("Stock", stock)],
    )
    return BriefingOut(
        location_id=loc.id,
        location_name=loc.name,
        timezone=loc.timezone,
        business_date=day,
        results_date=results_day,
        generated_at=now,
        highlights=_highlights(loc, sales, labor, staffing, stock),
        sales=sales,
        labor=labor,
        staffing=staffing,
        stock=stock,
        evidence=_evidence(sales, labor, staffing, stock),
        freshness=[
            BriefingFreshness(stream=f.stream, data_as_of=f.data_as_of, status=f.status)
            for f in freshness or []
        ],
        limitations=limitations,
        unavailable=unavailable,
    )
