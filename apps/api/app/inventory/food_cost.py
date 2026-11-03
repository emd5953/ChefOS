# ruff: noqa: E501
"""Deterministic, count-anchored operational food-cost variance calculation."""

import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ProblemError
from app.identity.models import Location
from app.integrations.freshness import location_freshness
from app.integrations.providers.square.business_date import business_date, get_zone
from app.inventory.food_cost_schemas import (
    FoodCostCoverage,
    FoodCostItem,
    FoodCostVarianceIn,
    FoodCostVarianceOut,
)

ZERO = Decimal("0")
SIX = Decimal("0.000001")
CENT = Decimal("1")
MAX_PERIOD_DAYS = 93


def _minor(value: Decimal) -> int:
    return int(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= ZERO:
        return None
    return ((numerator / denominator) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _problem(status: int, code: str, detail: str) -> ProblemError:
    return ProblemError(status, code, detail, title="Food cost variance unavailable")


def _theoretical_usage(
    rows: list[Any], baseline: datetime | None, baseline_missing: bool
) -> tuple[dict[uuid.UUID, Decimal], dict[str, int], int, Decimal, Decimal]:
    """Aggregate recipe ingredients while counting each POS line exactly once."""
    theoretical: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    excluded: dict[str, int] = defaultdict(int)
    grouped: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[row.id].append(row)
    total_quantity = sum((Decimal(group[0].quantity) for group in grouped.values()), ZERO)
    mapped_quantity = ZERO
    for group in grouped.values():
        row = group[0]
        quantity = Decimal(row.quantity)
        reason: str | None = None
        if baseline_missing or row.closed_at < baseline:
            reason = "before_history_baseline"
        elif row.menu_item_variant_id is None or row.menu_item_id is None:
            reason = "unmapped_sales_line"
        elif row.recipe_id is None:
            reason = "menu_item_without_recipe"
        elif row.recipe_version_id is None:
            reason = "missing_recipe_history"
        elif not row.recipe_active:
            reason = "inactive_recipe"
        elif not group[0].ingredient_id:
            reason = "recipe_without_ingredients"
        elif any(not ingredient.ingredient_active for ingredient in group):
            reason = "inactive_ingredient"
        elif row.yield_quantity is None or Decimal(row.yield_quantity) <= ZERO:
            reason = "invalid_recipe_yield"
        if reason:
            excluded[reason] += 1
            continue
        mapped_quantity += quantity
        for ingredient in group:
            theoretical[ingredient.ingredient_id] += (
                quantity
                * Decimal(ingredient.ingredient_quantity)
                / Decimal(ingredient.yield_quantity)
            )
    return theoretical, excluded, len(grouped), total_quantity, mapped_quantity


async def build_variance(
    db: AsyncSession,
    org_id: uuid.UUID,
    location: Location,
    body: FoodCostVarianceIn,
    *,
    now: datetime | None = None,
) -> FoodCostVarianceOut:
    """Build a report from submitted count snapshots and source events; absent inputs stay null."""
    now = now or datetime.now().astimezone()
    opening_ids = list(dict.fromkeys(body.opening_count_session_ids))
    closing_ids = list(dict.fromkeys(body.closing_count_session_ids))
    if len(opening_ids) != len(body.opening_count_session_ids) or len(closing_ids) != len(
        body.closing_count_session_ids
    ):
        raise _problem(422, "invalid_count_pair", "Count session IDs must be unique.")

    async def sessions(ids: list[uuid.UUID]) -> list[Any]:
        result = await db.execute(
            text("""SELECT id,location_id,storage_area_id,status,submitted_at
            FROM count_sessions WHERE org_id=:org AND id=ANY(:ids)"""),
            {"org": org_id, "ids": ids},
        )
        rows = list(result)
        if len(rows) != len(ids) or any(
            r.status != "submitted" or r.location_id != location.id or not r.submitted_at
            for r in rows
        ):
            raise _problem(
                422,
                "invalid_count_pair",
                "Each count must be submitted and belong to this location and organization.",
            )
        if len({r.storage_area_id for r in rows}) != len(rows):
            raise _problem(
                422, "invalid_count_pair", "Select exactly one submitted count per storage area."
            )
        return rows

    opening, closing = await sessions(opening_ids), await sessions(closing_ids)
    areas = await db.execute(
        text("SELECT id FROM storage_areas WHERE org_id=:org AND location_id=:loc AND active"),
        {"org": org_id, "loc": location.id},
    )
    expected_areas = {r.id for r in areas}
    open_by_area = {r.storage_area_id: r for r in opening}
    close_by_area = {r.storage_area_id: r for r in closing}
    if (
        not expected_areas
        or set(open_by_area) != expected_areas
        or set(close_by_area) != expected_areas
    ):
        raise _problem(
            422,
            "incomplete_count_pair",
            "Opening and closing counts must cover every active storage area.",
        )

    zone = get_zone(location.timezone)
    opening_dates = {
        business_date(r.submitted_at, location.timezone, location.business_day_start)
        for r in opening
    }
    closing_dates = {
        business_date(r.submitted_at, location.timezone, location.business_day_start)
        for r in closing
    }
    if len(opening_dates) != 1 or len(closing_dates) != 1:
        raise _problem(
            422,
            "inconsistent_count_dates",
            "All opening counts must share a business date, as must all closing counts.",
        )
    open_date, close_date = next(iter(opening_dates)), next(iter(closing_dates))
    first, last = open_date + timedelta(days=1), close_date
    if last < first:
        raise _problem(
            422,
            "reversed_count_pair",
            "Closing counts must be on a later business date than opening counts.",
        )
    if (last - first).days + 1 > MAX_PERIOD_DAYS:
        raise _problem(
            422, "period_too_long", f"Food cost periods are limited to {MAX_PERIOD_DAYS} days."
        )

    async def lines(ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[uuid.UUID, Any]]:
        result = await db.execute(
            text("""SELECT s.storage_area_id,cl.item_id,cl.counted_base_quantity,
            cl.item_name,cl.base_unit FROM count_sessions s JOIN count_lines cl
              ON cl.org_id=s.org_id AND cl.session_id=s.id
            WHERE s.org_id=:org AND s.id=ANY(:ids)"""),
            {"org": org_id, "ids": ids},
        )
        out: dict[uuid.UUID, dict[uuid.UUID, Any]] = defaultdict(dict)
        for row in result:
            out[row.storage_area_id][row.item_id] = row
        return out

    open_lines, close_lines = await lines(opening_ids), await lines(closing_ids)
    assigned = await db.execute(
        text("""SELECT i.id,i.name,i.base_unit,i.currency,a.storage_area_id
        FROM item_storage_assignments a JOIN inventory_items i ON i.org_id=a.org_id AND i.id=a.item_id
        JOIN storage_areas sa ON sa.org_id=a.org_id AND sa.id=a.storage_area_id AND sa.active
        WHERE a.org_id=:org AND a.location_id=:loc AND i.active ORDER BY i.name"""),
        {"org": org_id, "loc": location.id},
    )
    item_rows = {r.id: r for r in assigned}
    currencies = {r.currency for r in item_rows.values()}
    if len(currencies) > 1:
        raise _problem(
            422, "mixed_currency", "This location has inventory items in multiple currencies."
        )
    currency = next(iter(currencies), "USD")

    openings: dict[uuid.UUID, Decimal | None] = {}
    closings: dict[uuid.UUID, Decimal | None] = {}
    missing_count: set[uuid.UUID] = set()
    for item_id, item in item_rows.items():
        opening_line = open_lines.get(item.storage_area_id, {}).get(item_id)
        closing_line = close_lines.get(item.storage_area_id, {}).get(item_id)
        if (
            not opening_line
            or not closing_line
            or opening_line.counted_base_quantity is None
            or closing_line.counted_base_quantity is None
        ):
            missing_count.add(item_id)
            openings[item_id] = closings[item_id] = None
        else:
            openings[item_id] = Decimal(opening_line.counted_base_quantity)
            closings[item_id] = Decimal(closing_line.counted_base_quantity)

    receipt_result = await db.execute(
        text("""SELECT item_id,sum(received_base_quantity) quantity,sum(total_cost_minor) cost
        FROM receipt_lines rl JOIN receipts r ON r.org_id=rl.org_id AND r.id=rl.receipt_id
        WHERE rl.org_id=:org AND rl.location_id=:loc AND r.location_id=:loc
          AND r.business_date BETWEEN :first AND :last
        GROUP BY item_id"""),
        {"org": org_id, "loc": location.id, "first": first, "last": last},
    )
    receipts = {r.item_id: (Decimal(r.quantity), Decimal(r.cost)) for r in receipt_result}
    waste_result = await db.execute(
        text("""SELECT item_id,sum(quantity_base) quantity FROM waste_entries
        WHERE org_id=:org AND location_id=:loc AND business_date BETWEEN :first AND :last
        GROUP BY item_id"""),
        {"org": org_id, "loc": location.id, "first": first, "last": last},
    )
    waste = {r.item_id: Decimal(r.quantity) for r in waste_result}

    baseline = await db.scalar(
        text("SELECT effective_at FROM food_cost_baselines WHERE org_id=:org"), {"org": org_id}
    )
    baseline_missing = baseline is None or first < baseline.astimezone(zone).date()
    sales_result = await db.execute(
        text("""SELECT l.id,l.quantity,l.menu_item_variant_id,o.closed_at,o.returns,
          v.menu_item_id,a.recipe_id,rv.id recipe_version_id,rv.yield_quantity,rv.active recipe_active,
          rvi.item_id ingredient_id,rvi.quantity_base ingredient_quantity,rvi.item_name ingredient_name,
          rvi.base_unit ingredient_unit,ii.active ingredient_active
        FROM sales_order_lines l JOIN sales_orders o ON o.org_id=l.org_id AND o.id=l.order_id
        LEFT JOIN menu_item_variants v ON v.org_id=l.org_id AND v.id=l.menu_item_variant_id
        LEFT JOIN LATERAL (SELECT mra.recipe_id FROM menu_recipe_assignments mra
          WHERE mra.org_id=l.org_id AND mra.menu_item_id=v.menu_item_id AND mra.effective_at<=o.closed_at
          ORDER BY mra.effective_at DESC,mra.id DESC LIMIT 1) a ON true
        LEFT JOIN LATERAL (SELECT x.id,x.yield_quantity,x.active FROM recipe_versions x
          WHERE x.org_id=l.org_id AND x.recipe_id=a.recipe_id AND x.effective_at<=o.closed_at
          ORDER BY x.effective_at DESC,x.version DESC LIMIT 1) rv ON true
        LEFT JOIN recipe_version_ingredients rvi ON rvi.org_id=l.org_id AND rvi.recipe_version_id=rv.id
        LEFT JOIN inventory_items ii ON ii.org_id=rvi.org_id AND ii.id=rvi.item_id
        WHERE l.org_id=:org AND l.location_id=:loc AND l.business_date BETWEEN :first AND :last
          AND o.state='completed' ORDER BY l.id"""),
        {"org": org_id, "loc": location.id, "first": first, "last": last},
    )
    sales = list(sales_result)
    return_exists = await db.scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM sales_order_returns WHERE org_id=:org AND location_id=:loc AND business_date BETWEEN :first AND :last)"
        ),
        {"org": org_id, "loc": location.id, "first": first, "last": last},
    )
    theoretical, excluded, sales_line_count, total_sales_qty, mapped_sales_qty = _theoretical_usage(
        sales, baseline, baseline_missing
    )

    freshness = next(
        (f for f in await location_freshness(db, location.id, now) if f.stream == "orders"), None
    )
    orders_freshness = freshness.status if freshness else "not_connected"
    orders_as_of = freshness.data_as_of if freshness else None
    fresh_through_period = (
        orders_freshness == "fresh"
        and orders_as_of is not None
        and orders_as_of.astimezone(zone).date() >= last
    )

    cost_basis: dict[uuid.UUID, Decimal | None] = {}
    actual_q: dict[uuid.UUID, Decimal | None] = {}
    for item_id in set(item_rows) | set(theoretical) | set(receipts):
        receipt_qty, receipt_cost = receipts.get(item_id, (ZERO, ZERO))
        cost_basis[item_id] = receipt_cost / receipt_qty if receipt_qty > ZERO else None
        if (
            item_id not in item_rows
            or openings.get(item_id) is None
            or closings.get(item_id) is None
        ):
            actual_q[item_id] = None
        else:
            opening_quantity = openings[item_id]
            closing_quantity = closings[item_id]
            if opening_quantity is not None and closing_quantity is not None:
                actual_q[item_id] = opening_quantity + receipt_qty - closing_quantity

    report_items: list[FoodCostItem] = []
    count_covered = len(item_rows) - len(missing_count)
    item_cost_complete = True
    actual_minor_sum = 0
    theoretical_minor_sum = 0
    for item_id in sorted(
        set(item_rows) | set(theoretical),
        key=lambda i: item_rows[i].name if i in item_rows else str(i),
    ):
        item_row: Any | None = item_rows.get(item_id)
        rec_qty = receipts.get(item_id, (ZERO, ZERO))[0]
        actual = actual_q.get(item_id)
        theory = theoretical.get(item_id, ZERO)
        unit_cost = cost_basis.get(item_id)
        if item_id in missing_count:
            status = "missing_count"
        elif actual is not None and actual < ZERO:
            status = "negative_depletion"
        elif unit_cost is None:
            status = "missing_cost"
        elif baseline_missing and item_id in theoretical:
            status = "missing_recipe_history"
        else:
            status = "covered"
        actual_cost = (
            _minor(actual * unit_cost)
            if actual is not None and unit_cost is not None and actual >= ZERO
            else None
        )
        theoretical_known = not excluded
        theoretical_cost = (
            _minor(theory * unit_cost) if unit_cost is not None and theoretical_known else None
        )
        variance = (
            actual_cost - theoretical_cost
            if actual_cost is not None and theoretical_cost is not None
            else None
        )
        if actual_cost is None or theoretical_cost is None:
            item_cost_complete = False
        else:
            actual_minor_sum += actual_cost
            theoretical_minor_sum += theoretical_cost
        report_items.append(
            FoodCostItem(
                item_id=item_id,
                item_name=item_row.name
                if item_row
                else next(
                    (r.ingredient_name for r in sales if r.ingredient_id == item_id), "Unknown item"
                ),
                base_unit=item_row.base_unit
                if item_row
                else next((r.ingredient_unit for r in sales if r.ingredient_id == item_id), ""),
                opening_quantity=openings.get(item_id),
                receipts_quantity=rec_qty,
                closing_quantity=closings.get(item_id),
                actual_depletion_quantity=actual,
                theoretical_usage_quantity=theory if theoretical_known else None,
                weighted_average_unit_cost_minor=unit_cost,
                actual_cost_minor=actual_cost,
                theoretical_cost_minor=theoretical_cost,
                variance_minor=variance,
                variance_pct=_pct(Decimal(variance), Decimal(theoretical_cost))
                if variance is not None and theoretical_cost is not None
                else None,
                recorded_waste_quantity=waste.get(item_id, ZERO),
                status=status,
            )
        )

    quantity_coverage = _pct(mapped_sales_qty, total_sales_qty)
    has_returns = bool(return_exists) or any(row.returns for row in sales)
    mapped_line_count = sales_line_count - sum(excluded.values())
    all_sales_mapped = sales_line_count == mapped_line_count
    all_counts_covered = count_covered == len(item_rows)
    complete = (
        all_counts_covered
        and item_cost_complete
        and all_sales_mapped
        and not has_returns
        and fresh_through_period
        and not baseline_missing
    )
    limitations: list[str] = []
    if missing_count:
        limitations.append(
            f"Counts are missing for {len(missing_count)} active assigned ingredient(s)."
        )
    if any(
        i.actual_depletion_quantity is not None and i.actual_depletion_quantity < ZERO
        for i in report_items
    ):
        limitations.append(
            "Negative depletion was found; affected items are exceptions and are not clamped to zero."
        )
    if excluded:
        limitations.append(
            "Some sales lines do not map to complete effective recipe history; theoretical usage excludes them."
        )
    if has_returns:
        limitations.append(
            "Returns occurred in this period and cannot be allocated to sold item quantities."
        )
    if not fresh_through_period:
        limitations.append(
            f"Orders data is {orders_freshness} or not confirmed through the period end."
        )
    if baseline_missing:
        limitations.append("Recipe history is unavailable for dates before the reporting baseline.")
    if any(i.status == "missing_cost" for i in report_items):
        limitations.append(
            "Some ingredients have no received cost in this location and period; their dollar variance is unavailable."
        )
    if not complete:
        limitations.append(
            "Partial subtotals are not location totals; overall variance percent is unavailable."
        )
    if not sales:
        limitations.append("No completed sales lines were found in the measured period.")

    state = "complete" if complete else "partial"
    total_actual = actual_minor_sum if complete else None
    total_theoretical = theoretical_minor_sum if complete else None
    variance_total = (
        total_actual - total_theoretical
        if complete and total_actual is not None and total_theoretical is not None
        else None
    )
    return FoodCostVarianceOut(
        status=state,
        location_id=location.id,
        opening_count_date=open_date,
        closing_count_date=close_date,
        period_from=first,
        period_to=last,
        opening_submitted_at=[r.submitted_at for r in opening],
        closing_submitted_at=[r.submitted_at for r in closing],
        currency=currency,
        totals_are_complete=complete,
        actual_cost_minor=total_actual,
        theoretical_cost_minor=total_theoretical,
        variance_minor=variance_total,
        variance_pct=_pct(Decimal(variance_total), Decimal(total_theoretical))
        if variance_total is not None and total_theoretical
        else None,
        coverage=FoodCostCoverage(
            count_items_expected=len(item_rows),
            count_items_covered=count_covered,
            sales_lines=sales_line_count,
            mapped_sales_lines=mapped_line_count,
            mapped_sales_quantity=mapped_sales_qty,
            total_sales_quantity=total_sales_qty,
            sales_quantity_coverage_pct=quantity_coverage,
            excluded_sales_lines=dict(excluded),
            has_returns=has_returns,
            orders_freshness=orders_freshness,
            orders_as_of=orders_as_of,
        ),
        items=report_items,
        limitations=limitations,
    )
