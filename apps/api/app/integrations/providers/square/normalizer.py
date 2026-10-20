"""Pure normalizers: Square payloads -> canonical records. No I/O.

Metric definitions (integration spec, v1):
  * net sales = item gross - item discounts - returns. Tax, tips, service charges and
    gift card sales are excluded.
  * RETURNS BELONG TO THE BUSINESS DATE THEY WERE PROCESSED, not the original sale's date.
    ``normalize_order`` therefore emits the sale on the order's own business date
    (``CanonicalSalesOrder.net_sales`` = gross - discounts, returns NOT subtracted) and
    emits each refund as a separate ``CanonicalReturn`` whose ``business_date`` comes from
    the refund's ``created_at``. Daily net sales = sum(order.net_sales) - sum(return.net_cents).
  * business date = local date of closed_at / start_at shifted by business_day_start.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.integrations.providers.square.business_date import business_date
from app.integrations.providers.square.types import (
    CanonicalMenuCategory,
    CanonicalMenuItem,
    CanonicalMenuVariant,
    CanonicalReturn,
    CanonicalSalesLine,
    CanonicalSalesOrder,
    CanonicalStaff,
    CanonicalTimecard,
    Channel,
    MappingContext,
    MappingIssueSignal,
    parse_ts,
)

NORMALIZER_VERSION = "1"

_POS_SOURCES = frozenset(
    {"square point of sale", "square terminal", "square register", "square for restaurants"}
)
_DELIVERY_MARKETPLACES = frozenset(
    {"doordash", "uber eats", "ubereats", "grubhub", "postmates", "caviar", "seamless"}
)


@dataclass(frozen=True, slots=True)
class OrderNormalization:
    order: CanonicalSalesOrder
    returns: list[CanonicalReturn]
    signals: list[MappingIssueSignal]


@dataclass(frozen=True, slots=True)
class TimecardNormalization:
    timecard: CanonicalTimecard
    signals: list[MappingIssueSignal]


def _money(m: Mapping[str, Any] | None) -> int:
    if not m:
        return 0
    return int(m.get("amount") or 0)


def _split(total: int, weights: list[int]) -> list[int]:
    """Split ``total`` proportionally to ``weights``; parts always sum to ``total``."""
    w = sum(weights)
    if not weights:
        return []
    if w <= 0:
        return [total] + [0] * (len(weights) - 1)
    out: list[int] = []
    cum_w = 0
    prev = 0
    for x in weights:
        cum_w += x
        cur = (total * cum_w * 2 + w) // (2 * w)
        out.append(cur - prev)
        prev = cur
    return out


# ---------------------------------------------------------------- channel


def derive_channel(source_name: str | None, fulfillment_types: list[str]) -> Channel | None:
    """Return the channel, or None when the combination is not recognized."""
    src = (source_name or "").strip().lower()
    types = {t.upper() for t in fulfillment_types}
    if src in _DELIVERY_MARKETPLACES or "DELIVERY" in types:
        return "delivery"
    if src == "square online":
        if not types or types == {"PICKUP"}:
            return "online"
        return None
    if src in _POS_SOURCES:
        if not types:
            return "dine_in"
        if types == {"PICKUP"}:
            return "pickup"
        return None
    return None


# ---------------------------------------------------------------- orders


def _normalize_line(
    li: Mapping[str, Any], ctx: MappingContext, signals: dict[tuple[str, str], MappingIssueSignal]
) -> CanonicalSalesLine:
    qty = Decimal(str(li.get("quantity") or "1"))
    base = _money(li.get("base_price_money"))
    gross = _money(li.get("gross_sales_money")) or int(base * qty)
    discount = _money(li.get("total_discount_money"))
    cat_id = li.get("catalog_object_id")
    name = str(li.get("name") or "")
    ref = str(cat_id) if cat_id else (name or str(li.get("uid") or "unknown"))
    variant = ctx.resolve_catalog(str(cat_id)) if cat_id else None
    if variant is None:
        signals.setdefault(
            ("unmapped_item", ref),
            MappingIssueSignal("unmapped_item", ref, name or ref),
        )
    return CanonicalSalesLine(
        line_uid=str(li.get("uid") or ""),
        name=name,
        variation_name=li.get("variation_name"),
        external_item_ref=ref,
        menu_item_variant_external_id=variant,
        quantity=qty,
        gross_cents=gross,
        discount_cents=discount,
        net_cents=gross - discount,
        tax_cents=_money(li.get("total_tax_money")),
    )


def _normalize_returns(
    payload: Mapping[str, Any], ctx: MappingContext, order_id: str, location_id: str
) -> list[CanonicalReturn]:
    rlis = [
        rli
        for ret in (payload.get("returns") or [])
        for rli in (ret.get("return_line_items") or [])
    ]
    refunds = [
        r for r in (payload.get("refunds") or []) if r.get("status") not in ("REJECTED", "FAILED")
    ]
    if not rlis and not refunds:
        return []

    gross = disc = tax = 0
    for rli in rlis:
        qty = Decimal(str(rli.get("quantity") or "1"))
        gross += _money(rli.get("gross_return_money")) or int(
            _money(rli.get("base_price_money")) * qty
        )
        disc += _money(rli.get("total_discount_money"))
        tax += _money(rli.get("total_tax_money"))

    reasons: list[str | None] = [r.get("reason") for r in refunds]
    if refunds:
        stamps = [parse_ts(str(r["created_at"])) for r in refunds]
        ids = [str(r["id"]) for r in refunds]
        amounts = [_money(r.get("amount_money")) for r in refunds]
    else:  # itemized return without a refund record: fall back to the order's updated_at
        stamps = [parse_ts(str(payload["updated_at"]))]
        ids = [f"{order_id}-return"]
        amounts = [gross - disc + tax]
        reasons = [None]

    if rlis:
        g_parts, d_parts, t_parts = (_split(v, amounts) for v in (gross, disc, tax))
    else:  # refund with no itemization: whole amount is treated as net sales, no tax
        g_parts, d_parts, t_parts = amounts, [0] * len(amounts), [0] * len(amounts)

    out: list[CanonicalReturn] = []
    for i, ts in enumerate(stamps):
        net = g_parts[i] - d_parts[i]
        out.append(
            CanonicalReturn(
                external_id=ids[i],
                order_external_id=order_id,
                location_external_id=location_id,
                business_date=business_date(ts, ctx.timezone, ctx.business_day_start),
                processed_at=ts,
                gross_cents=g_parts[i],
                discount_cents=d_parts[i],
                net_cents=net,
                tax_cents=t_parts[i],
                total_cents=net + t_parts[i],
                reason=reasons[i],
            )
        )
    return out


def normalize_order(payload: Mapping[str, Any], ctx: MappingContext) -> OrderNormalization:
    order_id = str(payload["id"])
    location_id = str(payload.get("location_id") or "")
    signals: dict[tuple[str, str], MappingIssueSignal] = {}

    updated_at = parse_ts(str(payload["updated_at"]))
    closed_at = parse_ts(str(payload.get("closed_at") or payload["updated_at"]))
    created_raw = payload.get("created_at")

    lines = [
        _normalize_line(li, ctx, signals)
        for li in (payload.get("line_items") or [])
        if li.get("item_type") != "GIFT_CARD"  # gift card sales are excluded from net sales
    ]
    gross = sum(ln.gross_cents for ln in lines)
    discounts = sum(ln.discount_cents for ln in lines)

    source_name = (payload.get("source") or {}).get("name")
    ftypes = [str(f.get("type")) for f in (payload.get("fulfillments") or []) if f.get("type")]
    channel = derive_channel(source_name, ftypes)
    if channel is None:
        key = f"{source_name or ''}|{'+'.join(sorted(ftypes))}"
        signals[("unknown_channel", key)] = MappingIssueSignal(
            "unknown_channel", key, f"source={source_name!r} fulfillments={sorted(ftypes)}"
        )
        channel = "other"

    tips = _money(payload.get("total_tip_money"))
    if not payload.get("total_tip_money"):
        tips = sum(_money(t.get("tip_money")) for t in (payload.get("tenders") or []))

    returns = _normalize_returns(payload, ctx, order_id, location_id)
    total_money = payload.get("total_money") or {}
    order = CanonicalSalesOrder(
        external_id=order_id,
        location_external_id=location_id,
        business_date=business_date(closed_at, ctx.timezone, ctx.business_day_start),
        closed_at=closed_at,
        created_at=parse_ts(str(created_raw)) if created_raw else None,
        source_updated_at=updated_at,
        source_version=int(payload.get("version") or 1),
        channel=channel,
        source_name=source_name,
        currency=str(total_money.get("currency") or "USD"),
        gross_sales=gross,
        discounts=discounts,
        returns=sum(r.net_cents for r in returns),
        net_sales=gross - discounts,
        tax=_money(payload.get("total_tax_money")),
        tips=tips,
        service_charges=_money(payload.get("total_service_charge_money")),
        total=_money(total_money),
        lines=lines,
        normalizer_version=NORMALIZER_VERSION,
    )
    return OrderNormalization(order=order, returns=returns, signals=list(signals.values()))


# ---------------------------------------------------------------- catalog


def normalize_catalog_object(
    obj: Mapping[str, Any], category_names: Mapping[str, str]
) -> CanonicalMenuItem | CanonicalMenuCategory | None:
    """ITEM -> CanonicalMenuItem (with variants), CATEGORY -> CanonicalMenuCategory,
    other catalog types (TAX, DISCOUNT, ...) -> None."""
    kind = obj.get("type")
    deleted = bool(obj.get("is_deleted"))
    if kind == "CATEGORY":
        name = str((obj.get("category_data") or {}).get("name") or "")
        return CanonicalMenuCategory(external_id=str(obj["id"]), name=name, deleted=deleted)
    if kind != "ITEM":
        return None
    data = obj.get("item_data") or {}
    cat_id = data.get("category_id") or (data.get("reporting_category") or {}).get("id")
    if not cat_id:
        cats = data.get("categories") or []
        cat_id = cats[0].get("id") if cats else None
    variants: list[CanonicalMenuVariant] = []
    for v in data.get("variations") or []:
        vd = v.get("item_variation_data") or {}
        price = vd.get("price_money")
        variants.append(
            CanonicalMenuVariant(
                external_id=str(v["id"]),
                name=str(vd.get("name") or ""),
                price_cents=_money(price) if price else None,
                currency=str((price or {}).get("currency") or "USD"),
                deleted=bool(v.get("is_deleted")) or deleted,
            )
        )
    updated = obj.get("updated_at")
    version = obj.get("version")
    return CanonicalMenuItem(
        external_id=str(obj["id"]),
        name=str(data.get("name") or ""),
        category_external_id=str(cat_id) if cat_id else None,
        category_name=category_names.get(str(cat_id)) if cat_id else None,
        deleted=deleted,
        source_updated_at=parse_ts(updated) if updated else None,
        source_version=int(version) if version is not None else None,
        variants=variants,
    )


# ---------------------------------------------------------------- team members


def normalize_team_member(obj: Mapping[str, Any]) -> CanonicalStaff:
    jobs = (obj.get("wage_setting") or {}).get("job_assignments") or []
    job = jobs[0] if jobs else {}
    rate = job.get("hourly_rate")
    updated = obj.get("updated_at")
    return CanonicalStaff(
        external_id=str(obj["id"]),
        given_name=obj.get("given_name"),
        family_name=obj.get("family_name"),
        status=str(obj.get("status") or "ACTIVE").lower(),
        job_title=job.get("job_title"),
        hourly_rate_cents=_money(rate) if rate else None,
        source_updated_at=parse_ts(updated) if updated else None,
    )


# ---------------------------------------------------------------- timecards


def normalize_timecard(payload: Mapping[str, Any], ctx: MappingContext) -> TimecardNormalization:
    signals: list[MappingIssueSignal] = []
    member_id = str(payload.get("team_member_id") or "")
    if not ctx.is_known_team_member(member_id):
        signals.append(
            MappingIssueSignal(
                "unmapped_team_member", member_id, f"Timecard for unknown team member {member_id}"
            )
        )
    start = parse_ts(str(payload["start_at"]))
    end_raw = payload.get("end_at")
    end = parse_ts(str(end_raw)) if end_raw else None
    wage = payload.get("wage") or {}
    rate = _money(wage.get("hourly_rate"))

    unpaid_seconds = 0
    for b in payload.get("breaks") or []:
        if b.get("is_paid", False) or not b.get("end_at"):
            continue
        unpaid_seconds += int(
            (parse_ts(str(b["end_at"])) - parse_ts(str(b["start_at"]))).total_seconds()
        )

    is_open = end is None
    if end is None:
        paid_seconds = 0
        cost = 0
    else:
        paid_seconds = max(0, int((end - start).total_seconds()) - unpaid_seconds)
        cost = (paid_seconds * rate + 1800) // 3600  # half-up to the cent
    updated = payload.get("updated_at")
    version = payload.get("version")
    tc = CanonicalTimecard(
        external_id=str(payload["id"]),
        location_external_id=str(payload.get("location_id") or ""),
        team_member_external_id=member_id,
        business_date=business_date(start, ctx.timezone, ctx.business_day_start),
        start_at=start,
        end_at=end,
        paid_hours=round(paid_seconds / 3600, 4),
        unpaid_break_minutes=unpaid_seconds // 60,
        hourly_rate_cents=rate,
        cost_cents=cost,
        open=is_open,
        tip_eligible=bool(wage.get("tip_eligible", False)),
        job_title=wage.get("title"),
        source_updated_at=parse_ts(updated) if updated else None,
        source_version=int(version) if version is not None else None,
    )
    return TimecardNormalization(timecard=tc, signals=signals)
