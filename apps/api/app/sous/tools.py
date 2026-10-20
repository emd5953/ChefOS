"""Sous tools: a small allowlist of typed, read-only wrappers over `app.commerce.service`.

The model supplies dates and options only. The location comes from the API, which has already
authorized it. Each tool returns JSON-safe `data` for the model (money in dollars, percents in
percent units) and deterministic `evidence` rows (money in cents). The model never builds evidence.
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce import service
from app.commerce.schemas import FreshnessOut, Totals
from app.identity.models import Location
from app.integrations.freshness import FreshnessItem, location_freshness
from app.integrations.providers.square.business_date import business_date, get_zone
from app.sous.schemas import Evidence, Unit

log = structlog.get_logger()

MAX_RANGE_DAYS = 400
MAX_DAILY_DAYS = 92
DAILY_EVIDENCE_DAYS = 14


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
                        f.data_as_of.astimezone(tz).strftime("%b %-d, %-I:%M %p")
                        if f.data_as_of
                        else None
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
) -> ToolResult:
    """Validate and run one tool. Never raises: failures come back as `ok=False` results."""
    tool = TOOLS.get(name)
    if tool is None:
        return _failed(name, f"Unknown tool. Available tools: {', '.join(TOOL_NAMES)}.")
    try:
        parsed = tool.args.model_validate(args if isinstance(args, dict) else {})
    except ValidationError as exc:
        return _failed(name, _validation_message(exc))
    today = business_date(now, loc.timezone, loc.business_day_start)
    try:
        # A savepoint, so a failed query doesn't poison the request's transaction.
        async with db.begin_nested():
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
    "MAX_RANGE_DAYS",
    "TOOLS",
    "TOOL_NAMES",
    "ToolResult",
    "run_tool",
    "tool_specs",
]
