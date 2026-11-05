"""Sous tools: a small allowlist of typed, read-only wrappers over the Commerce, Team and
Inventory read paths.

The model supplies dates and options only. The location comes from the API, which has already
authorized it, and `run_tool` checks it again against the caller's scope before any query.
Each tool returns JSON-safe `data` for the model (money in dollars, percents in percent units)
and deterministic `evidence` rows (money in cents). The model never builds evidence.
"""

import re
import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, cast

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_date import business_date, get_zone
from app.commerce import service
from app.commerce.models import DailyLabor
from app.commerce.schemas import FreshnessOut, Totals
from app.formatting import local_timestamp
from app.identity.models import Location
from app.integrations.freshness import FreshnessItem, location_freshness
from app.security.sessions import AuthContext
from app.sous import proposals as proposal_service
from app.sous.schemas import (
    Evidence,
    FactsPeriod,
    ItemFacts,
    LaborDay,
    LaborFacts,
    ParItemFacts,
    ScheduleDayFacts,
    ScheduleFacts,
    ScheduleShiftFacts,
    Unit,
)
from app.team.models import Position, PublishedShift, Schedule, Shift, StaffMember, Station
from app.team.scheduling.builder import count_changes, label_time, paid_hours
from app.team.schemas import ShiftIn

log = structlog.get_logger()

MAX_RANGE_DAYS = 400
MAX_DAILY_DAYS = 92
DAILY_EVIDENCE_DAYS = 14
MAX_LABOR_DAYS = 92
MAX_SCHEDULE_DAYS = 14
SCHEDULE_AHEAD_DAYS = 56  # the schedule tool may look this far past today
MAX_SCHEDULE_SHIFTS = 60
SHIFT_LIST_DAYS = 2  # longer ranges return per-day totals only, so answers stay totals
MAX_PAR_ITEMS = 50
LISTED_DATES = 31  # dates listed by name in a facts field


class ToolArgError(Exception):
    """Arguments the tool refuses. The message goes back to the model."""


@dataclass
class ToolResult:
    data: dict[str, Any]
    evidence: list[Evidence] = field(default_factory=list)
    ok: bool = True
    tool: str = ""
    period: tuple[date, date] | None = None
    freshness: list[FreshnessOut] = field(default_factory=list)
    streams: tuple[str, ...] = ()  # streams the result depends on, for limitations
    hidden_names: int = 0  # POS text fields withheld as instruction-like, for limitations
    notes: list[str] = field(default_factory=list)  # data gaps the tool found, for limitations


class DateRange(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: date = Field(alias="from", description="First business date, YYYY-MM-DD, inclusive")
    to: date = Field(description="Last business date, YYYY-MM-DD, inclusive")


class SalesSummaryArgs(DateRange):
    compare: Literal["previous_period", "previous_year"] | None = Field(
        default=None,
        description="Also compare with the previous period of equal length, or the same days "
        "a year earlier (weekdays line up)",
    )


class DailySalesArgs(DateRange):
    pass


class TopItemsArgs(DateRange):
    limit: int = Field(default=10, ge=1, le=20, description="How many items, 1 to 20")


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LaborArgs(DateRange):
    pass


class ScheduleArgs(DateRange):
    pass


class BelowParArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=25, ge=1, le=MAX_PAR_ITEMS, description="How many items to list, 1 to 50"
    )


class PrepareScheduleChangeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["add", "update", "remove"] = "add"
    week_start: date | None = Field(default=None, description="Monday date for an added shift")
    target_shift_id: uuid.UUID | None = Field(
        default=None, description="Shift ID from the published schedule tool result"
    )
    shift: ShiftIn | None = Field(
        default=None, description="Complete new shift data for an add or replacement"
    )

    @model_validator(mode="after")
    def _action_args(self) -> PrepareScheduleChangeArgs:
        if self.action == "add" and (
            self.week_start is None or self.shift is None or self.target_shift_id
        ):
            raise ValueError(
                "Adding a shift requires week_start and shift, with no target_shift_id"
            )
        if self.action == "update" and (self.target_shift_id is None or self.shift is None):
            raise ValueError(
                "Updating a shift requires target_shift_id and a full replacement shift"
            )
        if self.action == "remove" and (self.target_shift_id is None or self.shift is not None):
            raise ValueError("Removing a shift requires target_shift_id and no shift payload")
        return self


# --- helpers ---------------------------------------------------------------------------------


def usd(cents: int | None) -> float | None:
    return None if cents is None else cents / 100


# Phrases that read as instructions to an AI. Each needs several words so menu words don't match.
INSTRUCTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:ignore|disregard|forget)\s+(?:(?:all|the|any|of)\s+)*"
        r"(?:previous|prior|above|all|your)\s+(?:\w+\s+)?(?:instructions|rules|prompt)\b",
        r"\bsystem\s+prompt\b",
        r"\byou\s+are\s+now\b",
        r"\bnew\s+instructions\b",
        # Whitespace is flattened first, so a line start is the text start or a sentence start.
        r"(?:^|[.!?]\s)(?:assistant|system|user)\s*:",
        r"\b(?:tell|inform)\s+(?:the\s+)?(?:owner|manager|user)s?\b",
        r"\b(?:say|respond|reply)\s+(?:with|that)\b",
    )
]
HIDDEN_NAME = "[name hidden]"


def _text(value: str) -> tuple[str, bool]:
    """Free text from the POS is data: flatten whitespace, cap the length, and withhold anything
    that reads as an instruction. The check runs before the cap so padding can't hide a phrase."""
    flat = re.sub(r"\s+", " ", value).strip()
    if any(p.search(flat) for p in INSTRUCTION_PATTERNS):
        return HIDDEN_NAME, True
    return flat[:80], False


def _check_range(first: date, last: date, today: date, max_days: int) -> int:
    if last < first:
        raise ToolArgError("`to` must be on or after `from`.")
    if last > today:
        raise ToolArgError(f"Dates after today's business date ({today.isoformat()}) aren't valid.")
    days = (last - first).days + 1
    if days > max_days:
        raise ToolArgError(f"The range is {days} days; the limit is {max_days}.")
    return days


def _period(first: date, last: date) -> dict[str, Any]:
    return {"from": first.isoformat(), "to": last.isoformat(), "days": (last - first).days + 1}


def _as_of(freshness: list[FreshnessOut], stream: str) -> datetime | None:
    return next((f.data_as_of for f in freshness if f.stream == stream), None)


def _fresh_out(items: list[FreshnessItem]) -> list[FreshnessOut]:
    return [
        FreshnessOut(
            stream=f.stream,
            location_id=f.location_id,
            data_as_of=f.data_as_of,
            status=f.status,
        )
        for f in items
    ]


def _totals_data(t: Totals) -> dict[str, Any]:
    return {
        "net_sales_usd": usd(t.net_sales),
        "gross_sales_usd": usd(t.gross_sales),
        "discounts_usd": usd(t.discounts),
        "returns_usd": usd(t.returns),
        "tips_usd": usd(t.tips),
        "orders": t.orders,
        "avg_check_usd": usd(t.avg_check),
        "labor_cost_usd": usd(t.labor_cost),
        "labor_hours": t.labor_hours,
        "labor_pct": t.labor_pct,
    }


def _totals_evidence(
    t: Totals, tag: str, source: str, orders_at: datetime | None, labor_at: datetime | None
) -> list[Evidence]:
    def row(label: str, value: float, unit: Unit, as_of: datetime | None) -> Evidence:
        return Evidence(label=f"{label}{tag}", value=value, unit=unit, source=source, as_of=as_of)

    rows = [
        row("Net sales", t.net_sales, "usd_cents", orders_at),
        row("Orders", t.orders, "count", orders_at),
    ]
    if t.avg_check is not None:
        rows.append(row("Average check", t.avg_check, "usd_cents", orders_at))
    rows.append(row("Labor cost", t.labor_cost, "usd_cents", labor_at))
    if t.labor_pct is not None:
        rows.append(row("Labor %", t.labor_pct, "percent", labor_at))
    return rows


# --- tools -----------------------------------------------------------------------------------

Runner = Callable[[AsyncSession, uuid.UUID, Location, Any, date, datetime], Awaitable[ToolResult]]


async def _sales_summary(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, args: SalesSummaryArgs, today: date, _: Any
) -> ToolResult:
    _check_range(args.from_, args.to, today, MAX_RANGE_DAYS)
    out = await service.summary(
        db, org_id, loc, args.from_, args.to, args.compare or "previous_period"
    )
    orders_at, labor_at = _as_of(out.freshness, "orders"), _as_of(out.freshness, "timecards")
    tag = f" ({args.from_.isoformat()} to {args.to.isoformat()})"
    data: dict[str, Any] = {
        "period": _period(args.from_, args.to),
        **_totals_data(out.current),
        "channels": [
            {"channel": c.channel, "net_sales_usd": usd(c.net_sales), "share_pct": c.share_pct}
            for c in out.channels
        ],
    }
    if out.current.orders == 0:
        data["note"] = "No sales were recorded in this period."
    evidence = _totals_evidence(out.current, tag, "get_sales_summary", orders_at, labor_at)
    if args.compare:
        p, c = out.compare_period, out.change
        prev = out.previous
        data["comparison"] = {
            "kind": args.compare,
            "period": _period(p["from"], p["to"]),
            **_totals_data(prev),
            "change": {
                "net_sales_usd": usd(out.current.net_sales - prev.net_sales),
                "net_sales_pct": c.net_sales_pct,
                "orders": out.current.orders - prev.orders,
                "orders_pct": c.orders_pct,
                "avg_check_pct": c.avg_check_pct,
                "labor_pct_pts": c.labor_pct_pts,
            },
        }
        ctag = f"{tag} vs {p['from'].isoformat()} to {p['to'].isoformat()}"
        for label, value, unit in (
            ("Net sales change", c.net_sales_pct, "percent"),
            ("Orders change", c.orders_pct, "percent"),
            ("Average check change", c.avg_check_pct, "percent"),
            ("Labor % change", c.labor_pct_pts, "percent_pts"),
        ):
            if value is not None:
                evidence.append(
                    Evidence(
                        label=f"{label}{ctag}",
                        value=value,
                        unit=unit,
                        source="get_sales_summary",
                        as_of=orders_at,
                    )
                )
        evidence += _totals_evidence(
            prev,
            f" ({p['from'].isoformat()} to {p['to'].isoformat()})",
            "get_sales_summary",
            orders_at,
            labor_at,
        )
    return ToolResult(
        data=data,
        evidence=evidence,
        period=(args.from_, args.to),
        freshness=out.freshness,
        streams=("orders", "timecards"),
    )


async def _daily_sales(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, args: DailySalesArgs, today: date, _: Any
) -> ToolResult:
    n = _check_range(args.from_, args.to, today, MAX_DAILY_DAYS)
    out = await service.daily(db, org_id, loc, args.from_, args.to)
    orders_at = _as_of(out.freshness, "orders")
    days = [
        {
            "date": d.business_date.isoformat(),
            "weekday": d.business_date.strftime("%A"),
            "net_sales_usd": usd(d.net_sales),
            "orders": d.orders,
            "avg_check_usd": usd(d.avg_check),
            "labor_cost_usd": usd(d.labor_cost),
            "labor_pct": d.labor_pct,
        }
        for d in out.days
    ]
    net = sum(d.net_sales for d in out.days)
    orders = sum(d.orders for d in out.days)
    labor = sum(d.labor_cost for d in out.days)
    labor_pct = round(labor / net * 100, 1) if net > 0 else None
    trading = [d for d in out.days if d.orders > 0]
    data: dict[str, Any] = {
        "period": _period(args.from_, args.to),
        "total_net_sales_usd": usd(net),
        "total_orders": orders,
        "total_labor_cost_usd": usd(labor),
        "total_labor_pct": labor_pct,
        "days": days,
    }
    tag = f" ({args.from_.isoformat()} to {args.to.isoformat()})"
    evidence = [
        Evidence(
            label=f"Net sales{tag}", value=net, unit="usd_cents", source="get_daily_sales",
            as_of=orders_at,
        ),
        Evidence(
            label=f"Orders{tag}", value=orders, unit="count", source="get_daily_sales",
            as_of=orders_at,
        ),
    ]  # fmt: skip
    if labor_pct is not None:
        evidence.append(
            Evidence(
                label=f"Labor %{tag}", value=labor_pct, unit="percent",
                source="get_daily_sales", as_of=_as_of(out.freshness, "timecards"),
            )
        )  # fmt: skip
    if trading:
        best = max(trading, key=lambda d: d.net_sales)
        worst = min(trading, key=lambda d: d.net_sales)
        data["best_day"] = {
            "date": best.business_date.isoformat(),
            "net_sales_usd": usd(best.net_sales),
        }
        data["slowest_day"] = {
            "date": worst.business_date.isoformat(),
            "net_sales_usd": usd(worst.net_sales),
        }
    if n <= DAILY_EVIDENCE_DAYS:
        evidence += [
            Evidence(
                label=f"Net sales {d.business_date.isoformat()}", value=d.net_sales,
                unit="usd_cents", source="get_daily_sales", as_of=orders_at,
            )
            for d in out.days
        ]  # fmt: skip
    return ToolResult(
        data=data,
        evidence=evidence,
        period=(args.from_, args.to),
        freshness=out.freshness,
        streams=("orders", "timecards"),
    )


async def _top_items(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, args: TopItemsArgs, today: date, _: Any
) -> ToolResult:
    _check_range(args.from_, args.to, today, MAX_RANGE_DAYS)
    out = await service.items(db, org_id, loc, args.from_, args.to, args.limit)
    freshness = _fresh_out(await location_freshness(db, loc.id))
    orders_at = _as_of(freshness, "orders")
    hidden = 0
    items: list[dict[str, Any]] = []
    for row in out.items:
        name, name_hidden = _text(row.name)
        category, category_hidden = _text(row.category)
        hidden += name_hidden + category_hidden
        items.append(
            {
                "name": name,
                "category": category,
                "quantity": row.quantity,
                "net_sales_usd": usd(row.net_sales),
                "share_pct": row.share_pct,
            }
        )
    data: dict[str, Any] = {
        "period": _period(args.from_, args.to),
        "item_count": len(items),
        "items": items,
        "unmapped_net_sales_usd": usd(out.unmapped_net_sales),
    }
    if hidden:
        data["hidden_names"] = hidden
    if not items:
        data["note"] = "No item sales were recorded in this period."
    evidence: list[Evidence] = []
    for it, raw in zip(items, out.items, strict=True):
        evidence.append(
            Evidence(
                label=f"Net sales: {it['name']}",
                value=raw.net_sales,
                unit="usd_cents",
                source="get_top_items",
                as_of=orders_at,
            )
        )
        evidence.append(
            Evidence(
                label=f"Quantity: {it['name']}",
                value=it["quantity"],
                unit="count",
                source="get_top_items",
                as_of=orders_at,
            )
        )
    return ToolResult(
        data=data,
        evidence=evidence,
        period=(args.from_, args.to),
        freshness=freshness,
        streams=("orders", "catalog"),
        hidden_names=hidden,
    )


async def _today(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, _: NoArgs, today: date, now: datetime
) -> ToolResult:
    out = await service.today(db, org_id, loc, now)
    orders_at = _as_of(out.freshness, "orders")
    busy = [h for h in out.by_hour if h.orders > 0]
    data: dict[str, Any] = {
        "business_date": out.business_date.isoformat(),
        "net_sales_usd": usd(out.net_sales),
        "orders": out.orders,
        "avg_check_usd": usd(out.avg_check),
        "last_order_at": out.last_order_at.isoformat() if out.last_order_at else None,
        "by_hour": [
            {"hour": h.hour, "net_sales_usd": usd(h.net_sales), "orders": h.orders} for h in busy
        ],
    }
    if busy:
        data["busiest_hour"] = max(busy, key=lambda h: h.net_sales).hour
    else:
        data["note"] = "No orders have closed yet today."
    tag = f" ({out.business_date.isoformat()})"
    evidence = [
        Evidence(
            label=f"Net sales today{tag}", value=out.net_sales, unit="usd_cents",
            source="get_today", as_of=orders_at,
        ),
        Evidence(
            label=f"Orders today{tag}", value=out.orders, unit="count",
            source="get_today", as_of=orders_at,
        ),
    ]  # fmt: skip
    if out.avg_check is not None:
        evidence.append(
            Evidence(
                label=f"Average check today{tag}", value=out.avg_check, unit="usd_cents",
                source="get_today", as_of=orders_at,
            )
        )  # fmt: skip
    return ToolResult(
        data=data,
        evidence=evidence,
        period=(out.business_date, out.business_date),
        freshness=out.freshness,
        streams=("orders",),
    )


async def _freshness(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, _: NoArgs, today: date, now: datetime
) -> ToolResult:
    freshness = _fresh_out(await location_freshness(db, loc.id, now))
    tz = get_zone(loc.timezone)
    # Local wall-clock time and whole minutes, so the model never converts timestamps itself.
    return ToolResult(
        data={
            "streams": [
                {
                    "stream": f.stream,
                    "status": f.status,
                    "updated_at_local": (
                        local_timestamp(f.data_as_of.astimezone(tz)) if f.data_as_of else None
                    ),
                    "minutes_since_update": (
                        int((now - f.data_as_of).total_seconds() // 60) if f.data_as_of else None
                    ),
                }
                for f in freshness
            ]
        },
        freshness=freshness,
        streams=tuple(f.stream for f in freshness),
    )


# --- Team and Inventory tools ----------------------------------------------------------------


def _num(value: Decimal | float) -> float:
    """A JSON number without float noise: quantities are NUMERIC(18, 6) in the database."""
    return float(round(Decimal(str(value)), 4).normalize())


def _date_list(days: list[date]) -> str:
    shown = ", ".join(d.isoformat() for d in days[:5])
    return shown + (f" and {len(days) - 5} more" if len(days) > 5 else "")


async def _labor_summary(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, args: LaborArgs, today: date, now: datetime
) -> ToolResult:
    n = _check_range(args.from_, args.to, today, MAX_LABOR_DAYS)
    out = await service.daily(db, org_id, loc, args.from_, args.to)
    # daily_labor has a row only for days with timecards; a missing day is unknown, not zero.
    recorded = set(
        await db.scalars(
            select(DailyLabor.business_date).where(
                DailyLabor.org_id == org_id,
                DailyLabor.location_id == loc.id,
                DailyLabor.business_date.between(args.from_, args.to),
            )
        )
    )
    freshness = _fresh_out(await location_freshness(db, loc.id, now))
    labor_at = _as_of(freshness, "timecards")
    missing = [d.business_date for d in out.days if d.business_date not in recorded]
    net = sum(d.net_sales for d in out.days)
    cost = sum(d.labor_cost for d in out.days)
    hours = sum(d.labor_hours for d in out.days)
    worked = len(recorded)
    facts = LaborFacts(
        period=FactsPeriod(from_=args.from_, to=args.to, days=n),
        days_with_timecards=worked,
        days_without_timecards=missing[:LISTED_DATES],
        labor_hours=round(hours, 2) if worked else None,
        labor_cost_usd=usd(cost) if worked else None,
        net_sales_usd=usd(net) or 0.0,
        labor_pct=round(cost / net * 100, 1) if worked and net > 0 else None,
        days=[
            LaborDay(
                date=d.business_date,
                weekday=d.business_date.strftime("%A"),
                timecards=d.business_date in recorded,
                labor_hours=d.labor_hours if d.business_date in recorded else None,
                labor_cost_usd=usd(d.labor_cost) if d.business_date in recorded else None,
                net_sales_usd=usd(d.net_sales) or 0.0,
                labor_pct=d.labor_pct if d.business_date in recorded else None,
            )
            for d in out.days
        ]
        if n <= DAILY_EVIDENCE_DAYS
        else None,
    )
    notes: list[str] = []
    span = f"{args.from_.isoformat()} to {args.to.isoformat()}"
    if not worked:
        facts.note = "No timecards were recorded in this period, so actual labor is unknown."
        notes.append(f"No timecards were recorded for {span}; labor is unknown, not zero.")
    elif missing:
        facts.note = (
            "Some days have no timecards; their labor is unknown, so the totals cover only "
            "days with timecards."
        )
        notes.append(
            f"No timecards for {len(missing)} of {n} days ({_date_list(missing)}); labor for "
            "those days is unknown, not zero, so labor % is understated."
        )
    tag = f" ({span})"
    evidence = [
        Evidence(
            label=f"Days with timecards{tag}", value=worked, unit="count",
            source="get_labor_summary", as_of=labor_at,
        )
    ]  # fmt: skip
    if worked:
        evidence += [
            Evidence(
                label=f"Labor hours{tag}", value=round(hours, 2), unit="hours",
                source="get_labor_summary", as_of=labor_at,
            ),
            Evidence(
                label=f"Labor cost{tag}", value=cost, unit="usd_cents",
                source="get_labor_summary", as_of=labor_at,
            ),
        ]  # fmt: skip
        if facts.labor_pct is not None:
            evidence.append(
                Evidence(
                    label=f"Labor %{tag}", value=facts.labor_pct, unit="percent",
                    source="get_labor_summary", as_of=labor_at,
                )
            )  # fmt: skip
    return ToolResult(
        data=facts.model_dump(mode="json", by_alias=True),
        evidence=evidence,
        period=(args.from_, args.to),
        freshness=freshness,
        streams=("timecards", "orders"),
        notes=notes,
    )


def _check_schedule_range(first: date, last: date, today: date) -> int:
    if last < first:
        raise ToolArgError("`to` must be on or after `from`.")
    if last > today + timedelta(days=SCHEDULE_AHEAD_DAYS):
        raise ToolArgError(f"Schedules can be checked at most {SCHEDULE_AHEAD_DAYS} days ahead.")
    if first < today - timedelta(days=MAX_RANGE_DAYS):
        raise ToolArgError(f"Schedules can be checked at most {MAX_RANGE_DAYS} days back.")
    days = (last - first).days + 1
    if days > MAX_SCHEDULE_DAYS:
        raise ToolArgError(f"The range is {days} days; the limit is {MAX_SCHEDULE_DAYS}.")
    return days


async def _schedule(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, args: ScheduleArgs, today: date, _: Any
) -> ToolResult:
    """Published shifts only: drafts are a manager's work in progress, not who is working."""
    n = _check_schedule_range(args.from_, args.to, today)
    zone = get_zone(loc.timezone)
    monday = args.from_ - timedelta(days=args.from_.weekday())
    schedules = list(
        await db.scalars(
            select(Schedule).where(
                Schedule.org_id == org_id,
                Schedule.location_id == loc.id,
                Schedule.week_start.between(monday, args.to),
            )
        )
    )
    published = {s.id: s for s in schedules if s.published_version is not None}
    rows = (
        (
            await db.execute(
                select(PublishedShift, Position.name, Station.name, StaffMember.display_name)
                .join(Position, Position.id == PublishedShift.position_id)
                .outerjoin(Station, Station.id == PublishedShift.station_id)
                .outerjoin(StaffMember, StaffMember.id == PublishedShift.employee_id)
                .where(
                    PublishedShift.org_id == org_id,
                    PublishedShift.location_id == loc.id,
                    PublishedShift.schedule_id.in_(list(published)),
                    PublishedShift.business_date.between(args.from_, args.to),
                )
                .order_by(PublishedShift.starts_at, PublishedShift.id)
            )
        ).all()
        if published
        else []
    )
    # Whether the draft differs from what employees see, for a label only; drafts aren't shown.
    changed = False
    for sched in published.values():
        drafts = list(await db.scalars(select(Shift).where(Shift.schedule_id == sched.id)))
        live = list(
            await db.scalars(select(PublishedShift).where(PublishedShift.schedule_id == sched.id))
        )
        changed = changed or count_changes(drafts, live) > 0
    covered = {
        s.week_start + timedelta(days=i) for s in published.values() for i in range(7)
    }  # fmt: skip
    dates = [args.from_ + timedelta(days=i) for i in range(n)]
    hidden = 0
    shifts: list[ScheduleShiftFacts] = []
    proposal_targets: list[dict[str, Any]] = []
    hours_by_day: dict[date, Decimal] = {d: Decimal(0) for d in dates}
    count_by_day: dict[date, int] = dict.fromkeys(dates, 0)
    people: set[uuid.UUID] = set()
    for shift, position, station, employee in rows:
        hours = paid_hours(shift.starts_at, shift.ends_at, shift.break_minutes)
        hours_by_day[shift.business_date] += hours
        count_by_day[shift.business_date] += 1
        if shift.employee_id is not None:
            people.add(shift.employee_id)
        names = [_text(v) if v else ("", False) for v in (employee, position, station)]
        hidden += sum(h for _, h in names)
        start, end = shift.starts_at.astimezone(zone), shift.ends_at.astimezone(zone)
        shifts.append(
            ScheduleShiftFacts(
                date=shift.business_date,
                weekday=shift.business_date.strftime("%A"),
                employee=names[0][0] or "Unassigned",
                position=names[1][0],
                station=names[2][0] or None,
                starts=label_time(shift.starts_at, zone),
                ends=label_time(shift.ends_at, zone),
                ends_next_day=end.date() > start.date(),
                paid_hours=round(float(hours), 2),
            )
        )
        if n <= SHIFT_LIST_DAYS:
            proposal_targets.append(
                {
                    "shift_id": str(shift.id),
                    "week_start": published[shift.schedule_id].week_start.isoformat(),
                    "business_date": shift.business_date.isoformat(),
                    "employee_id": str(shift.employee_id) if shift.employee_id else None,
                    "employee": names[0][0] or "Unassigned",
                    "position_id": str(shift.position_id),
                    "position": names[1][0],
                    "station_id": str(shift.station_id) if shift.station_id else None,
                    "station": names[2][0] or None,
                    "starts_at": shift.starts_at.isoformat(),
                    "ends_at": shift.ends_at.isoformat(),
                    "break_minutes": shift.break_minutes,
                    "notes": shift.notes,
                }
            )
    listed = shifts if n <= SHIFT_LIST_DAYS else []
    unpublished = [d for d in dates if d not in covered]
    total_hours = round(float(sum(hours_by_day.values(), Decimal(0))), 2)
    facts = ScheduleFacts(
        period=FactsPeriod(from_=args.from_, to=args.to, days=n),
        shift_count=len(rows),
        scheduled_hours=total_hours,
        people_scheduled=len(people),
        days=[
            ScheduleDayFacts(
                date=d,
                weekday=d.strftime("%A"),
                published=d in covered,
                shifts=count_by_day[d],
                scheduled_hours=round(float(hours_by_day[d]), 2),
            )
            for d in dates
        ],
        shifts=listed[:MAX_SCHEDULE_SHIFTS],
        shifts_shown=min(len(listed), MAX_SCHEDULE_SHIFTS),
        unpublished_dates=unpublished,
        draft_has_unpublished_changes=changed,
    )
    if n > SHIFT_LIST_DAYS and rows:
        facts.shifts_note = (
            f"Individual shifts are listed only for ranges of up to {SHIFT_LIST_DAYS} days. "
            "Use the per-day and range totals."
        )
    notes: list[str] = []
    if unpublished:
        facts.note = "No schedule is published for some dates; nobody is shown as working then."
        notes.append(f"No schedule is published for {_date_list(unpublished)}.")
    elif not rows:
        facts.note = "The published schedule has no shifts in this period."
    if changed:
        notes.append(
            "The draft schedule has changes that are not published; Sous shows published "
            "shifts only."
        )
    if len(listed) > MAX_SCHEDULE_SHIFTS:
        notes.append(f"Showing the first {MAX_SCHEDULE_SHIFTS} of {len(listed)} shifts.")
    as_of = max((s.published_at for s in published.values() if s.published_at), default=None)
    tag = f" ({args.from_.isoformat()} to {args.to.isoformat()})"
    evidence = [
        Evidence(
            label=f"Published shifts{tag}", value=len(rows), unit="count", source="get_schedule",
            as_of=as_of,
        ),
        Evidence(
            label=f"Scheduled hours{tag}", value=total_hours, unit="hours", source="get_schedule",
            as_of=as_of,
        ),
    ]  # fmt: skip
    if n <= 7:
        evidence += [
            Evidence(
                label=f"Published shifts {d.isoformat()}", value=count_by_day[d], unit="count",
                source="get_schedule", as_of=as_of,
            )
            for d in dates
            if d in covered
        ]  # fmt: skip
    return ToolResult(
        data={
            **facts.model_dump(mode="json", by_alias=True),
            "proposal_targets": proposal_targets if n <= SHIFT_LIST_DAYS else [],
        },
        evidence=evidence,
        period=(args.from_, args.to),
        hidden_names=hidden,
        notes=notes,
    )


_PAR_ROWS = text("""
SELECT i.name, i.category, i.base_unit, p.par_base_quantity AS par,
       COALESCE(l.on_hand_base_quantity, 0) AS on_hand, l.updated_at AS level_at,
       l.item_id IS NULL AS never_counted
FROM inventory_items i
LEFT JOIN inventory_levels l ON l.org_id = i.org_id AND l.item_id = i.id AND l.location_id = :loc
LEFT JOIN inventory_pars p ON p.org_id = i.org_id AND p.item_id = i.id AND p.location_id = :loc
WHERE i.org_id = :org AND i.active
ORDER BY i.name, i.id
""")


async def _below_par(
    db: AsyncSession, org_id: uuid.UUID, loc: Location, args: BelowParArgs, today: date, _: Any
) -> ToolResult:
    rows = (await db.execute(_PAR_ROWS, {"org": org_id, "loc": loc.id})).all()
    zone = get_zone(loc.timezone)
    as_of = max((r.level_at for r in rows if r.level_at is not None), default=None)
    with_par = [r for r in rows if r.par is not None]
    below = sorted(
        (r for r in with_par if r.on_hand < r.par),
        # Most depleted first: share of par on hand, then the larger shortfall.
        key=lambda r: (r.on_hand / r.par if r.par else 0, -(r.par - r.on_hand)),
    )
    no_par = [r for r in rows if r.par is None]
    hidden = 0
    items: list[ParItemFacts] = []
    shown = below[: args.limit]
    for r in shown:
        name, name_hidden = _text(r.name)
        category, category_hidden = _text(r.category)
        hidden += name_hidden + category_hidden
        items.append(
            ParItemFacts(
                name=name,
                category=category,
                base_unit=r.base_unit,
                on_hand=_num(r.on_hand),
                par=_num(r.par),
                shortfall=_num(r.par - r.on_hand),
                never_counted=r.never_counted,
            )
        )
    without: list[str] = []
    for r in no_par[:10]:
        name, name_hidden = _text(r.name)
        hidden += name_hidden
        without.append(name)
    facts = ItemFacts(
        items_tracked=len(rows),
        items_with_par=len(with_par),
        below_par_count=len(below),
        items=items,
        items_shown=len(items),
        items_without_par=len(no_par),
        without_par_names=without,
        stock_as_of_local=local_timestamp(as_of.astimezone(zone)) if as_of else None,
    )
    notes: list[str] = []
    if not rows:
        facts.note = "No inventory items are set up yet."
    elif not with_par:
        facts.note = "No par levels are set at this location, so nothing can be checked."
    if no_par and rows:
        notes.append(f"{len(no_par)} item(s) have no par at this location and were not checked.")
    never = sum(1 for r in below if r.never_counted)
    if never:
        notes.append(
            f"{never} below-par item(s) have never been counted or received here; on hand reads 0."
        )
    if len(below) > len(shown):
        notes.append(f"Showing {len(shown)} of {len(below)} items below par.")
    evidence = [
        Evidence(
            label="Items below par", value=len(below), unit="count", source="find_items_below_par",
            as_of=as_of,
        ),
        Evidence(
            label="Items without a par", value=len(no_par), unit="count",
            source="find_items_below_par", as_of=as_of,
        ),
    ]  # fmt: skip
    for it, r in zip(items, shown, strict=True):
        unit: Unit = r.base_unit
        evidence += [
            Evidence(
                label=f"On hand: {it.name} ({unit})", value=it.on_hand, unit=unit,
                source="find_items_below_par", as_of=r.level_at,
            ),
            Evidence(
                label=f"Par: {it.name} ({unit})", value=it.par, unit=unit,
                source="find_items_below_par", as_of=r.level_at,
            ),
        ]  # fmt: skip
    return ToolResult(
        data=facts.model_dump(mode="json", by_alias=True),
        evidence=evidence,
        hidden_names=hidden,
        notes=notes,
    )


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    args: type[BaseModel]
    run: Runner


TOOLS: dict[str, ToolDef] = {
    t.name: t
    for t in (
        ToolDef(
            "get_sales_summary",
            "Net sales, orders, average check, labor cost and labor % for a date range, with "
            "channel mix. Optionally compares with the previous period or the same days a year "
            "earlier. Use this for totals and for any comparison between two periods.",
            SalesSummaryArgs,
            _sales_summary,
        ),
        ToolDef(
            "get_daily_sales",
            "Day-by-day net sales, orders, average check and labor % for a date range of at "
            "most 92 days, plus the best and slowest day. Use this for trends and day questions.",
            DailySalesArgs,
            _daily_sales,
        ),
        ToolDef(
            "get_top_items",
            "Top-selling menu items by net sales for a date range, with quantity and share.",
            TopItemsArgs,
            _top_items,
        ),
        ToolDef(
            "get_today",
            "Sales so far today (the current business day): net sales, orders, average check, "
            "busiest hour, and the time of the last order.",
            NoArgs,
            _today,
        ),
        ToolDef(
            "get_data_freshness",
            "How current the data is: status and last-updated time for orders, timecards, "
            "catalog and team members.",
            NoArgs,
            _freshness,
        ),
        ToolDef(
            "get_labor_summary",
            "Actual labor from timecards for a date range of at most 92 days: paid hours, labor "
            "cost, labor % of net sales, and which days have no timecards (unknown, not zero). "
            "Day by day for ranges of 14 days or less. Use this for hours worked and labor cost.",
            LaborArgs,
            _labor_summary,
        ),
        ToolDef(
            "get_schedule",
            "Published shifts for a date range of at most 14 days, past or up to 56 days ahead: "
            "shifts and scheduled hours per day and for the whole range. For ranges of up to 2 "
            "days it also lists who works, position, start and end times (overnight shifts end "
            "the next day). Drafts are not shown. Use this for who is working.",
            ScheduleArgs,
            _schedule,
        ),
        ToolDef(
            "prepare_schedule_change",
            "Prepare one draft shift addition, adjustment, or removal for manager review. "
            "Use get_schedule first to identify an existing shift by target_shift_id. For an "
            "adjustment provide its complete replacement fields, preserving any details the "
            "manager did not ask to change. Additions need Monday week_start, position_id, and "
            "ISO start/end timestamps with timezone offsets. Removals need only target_shift_id. "
            "This never applies or publishes a schedule change.",
            PrepareScheduleChangeArgs,
            _schedule,
        ),
        ToolDef(
            "find_items_below_par",
            "Inventory items whose stock on hand is below their par level at this location, most "
            "depleted first, with quantities in base units (g, ml or ea), plus items with no par.",
            BelowParArgs,
            _below_par,
        ),
    )
}
TOOL_NAMES: tuple[str, ...] = tuple(TOOLS)


def _simplify(node: Any) -> Any:
    """Trim a pydantic JSON schema to what local models handle well."""
    if isinstance(node, dict):
        if "anyOf" in node:  # Optional[X] -> X
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            if len(options) == 1:
                merged = {k: v for k, v in node.items() if k != "anyOf"}
                return _simplify({**options[0], **merged})
        return {k: _simplify(v) for k, v in node.items() if k not in ("title", "default")}
    if isinstance(node, list):
        return [_simplify(v) for v in node]
    return node


def tool_specs() -> list[dict[str, Any]]:
    """Ollama / OpenAI-style function specs for the registry."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": _simplify(t.args.model_json_schema(by_alias=True)),
            },
        }
        for t in TOOLS.values()
    ]


def _failed(name: str, message: str) -> ToolResult:
    return ToolResult(data={"error": message}, ok=False, tool=name)


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors()
    )


async def run_tool(
    name: str,
    args: dict[str, Any],
    db: AsyncSession,
    org_id: uuid.UUID,
    loc: Location,
    now: datetime,
    *,
    allowed: Collection[uuid.UUID] | None = None,
    caller: AuthContext | None = None,
) -> ToolResult:
    """Validate and run one tool. Never raises: failures come back as `ok=False` results.

    `allowed` is the caller's current location scope. The location is rechecked against it, and
    against the org, before any query runs."""
    tool = TOOLS.get(name)
    if tool is None:
        return _failed(name, f"Unknown tool. Available tools: {', '.join(TOOL_NAMES)}.")
    if loc.org_id != org_id or (allowed is not None and loc.id not in allowed):
        log.warning("sous_tool_scope_denied", tool=name)
        return _failed(name, "This location is outside your access.")
    try:
        parsed = tool.args.model_validate(args if isinstance(args, dict) else {})
    except ValidationError as exc:
        return _failed(name, _validation_message(exc))
    today = business_date(now, loc.timezone, loc.business_day_start)
    try:
        # A savepoint, so a failed query doesn't poison the request's transaction.
        async with db.begin_nested():
            if name == "prepare_schedule_change":
                if caller is None:
                    return _failed(name, "Schedule proposals are unavailable in this context.")
                if caller.role not in {"owner", "admin", "manager"}:
                    return _failed(name, "Only managers can prepare schedule proposals.")
                proposal_args = cast(PrepareScheduleChangeArgs, parsed)
                proposal = await proposal_service.create(
                    db,
                    caller,
                    loc.id,
                    action=proposal_args.action,
                    week_start=proposal_args.week_start,
                    target_shift_id=proposal_args.target_shift_id,
                    shift=proposal_args.shift,
                )
                saved_schedule = await db.get(Schedule, proposal.schedule_id)
                week_start = (
                    saved_schedule.week_start if saved_schedule else proposal_args.week_start
                )
                shift_summary = proposal.payload or proposal.before_snapshot or {}
                position = (
                    await db.get(Position, uuid.UUID(shift_summary["position_id"]))
                    if shift_summary.get("position_id")
                    else None
                )
                employee = (
                    await db.get(StaffMember, uuid.UUID(shift_summary["employee_id"]))
                    if shift_summary.get("employee_id")
                    else None
                )
                position_name, position_hidden = (
                    _text(position.name) if position else ("Position", False)
                )
                employee_name, employee_hidden = (
                    _text(employee.display_name) if employee else (None, False)
                )
                before_snapshot = proposal.before_snapshot
                if before_snapshot:
                    before_snapshot = {
                        **before_snapshot,
                        "position_name": position_name,
                        "employee_name": employee_name,
                    }
                result = ToolResult(
                    data={
                        "proposals_created": 1,
                        "id": str(proposal.id),
                        "action": proposal.action,
                        "location_id": str(proposal.location_id),
                        "schedule_id": str(proposal.schedule_id),
                        "week_start": (
                            week_start.isoformat() if isinstance(week_start, date) else None
                        ),
                        "target_shift_id": (
                            str(proposal.target_shift_id) if proposal.target_shift_id else None
                        ),
                        "expected_version": proposal.expected_version,
                        "status": proposal.status,
                        "expires_at": proposal.expires_at.isoformat(),
                        "created_at": proposal.created_at.isoformat(),
                        "payload": proposal.payload,
                        "before_snapshot": before_snapshot,
                        "position_name": position_name,
                        "employee_name": employee_name,
                    },
                    hidden_names=position_hidden + employee_hidden,
                    tool=name,
                )
            else:
                result = await tool.run(db, org_id, loc, parsed, today, now)
    except ToolArgError as exc:
        return _failed(name, str(exc))
    except Exception as exc:
        log.warning("sous_tool_error", tool=name, error=type(exc).__name__)
        return _failed(name, "The tool failed unexpectedly. Try again or answer without it.")
    result.tool = name
    return result


__all__ = [
    "MAX_DAILY_DAYS",
    "MAX_LABOR_DAYS",
    "MAX_RANGE_DAYS",
    "MAX_SCHEDULE_DAYS",
    "TOOLS",
    "TOOL_NAMES",
    "ToolResult",
    "run_tool",
    "tool_specs",
]
