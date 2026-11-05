"""Forecast sales for scheduling (docs/specs/team.md, "Forecast and projected labor").

Forecast for business date D = the median of net sales (all channels) on the last 4 complete
same-weekday business dates before min(D, today). Fewer than 2 such dates with history ->
null, reason `not_enough_history`. A date on or after the location's first day with sales but
without a `daily_sales` row is a closed day and counts as 0. No POS connection -> null,
reason `not_connected`.

Median, not mean: one buyout or snowstorm day in the window moves the forecast by at most the
gap between the two middle values.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

WINDOW = 4
MIN_POINTS = 2
Reason = Literal["not_connected", "not_enough_history"]


@dataclass(frozen=True)
class Forecast:
    business_date: date
    sales: int | None  # cents
    basis: list[date]  # the dates the median was taken over, newest first
    reason: Reason | None


def median_cents(values: Iterable[int]) -> int:
    """Median in whole cents; an even count averages the middle pair, rounding half up."""
    v = sorted(values)
    if not v:
        raise ValueError("median of nothing")
    mid = len(v) // 2
    if len(v) % 2:
        return v[mid]
    return (v[mid - 1] + v[mid] + 1) // 2


def basis_dates(target: date, today: date, n: int = WINDOW) -> list[date]:
    """The `n` most recent same-weekday dates strictly before min(target, today)."""
    cutoff = min(target, today)
    d = target - timedelta(days=7)
    while d >= cutoff:
        d -= timedelta(days=7)
    return [d - timedelta(days=7 * i) for i in range(n)]


def forecast_for(
    target: date, today: date, sales: Mapping[date, int], history_start: date | None
) -> Forecast:
    """Pure forecast from daily net sales (`sales`: date -> cents, missing = no row)."""
    candidates = basis_dates(target, today)
    if history_start is None:
        return Forecast(target, None, [], "not_enough_history")
    basis = [d for d in candidates if d >= history_start]
    if len(basis) < MIN_POINTS:
        return Forecast(target, None, basis, "not_enough_history")
    return Forecast(target, median_cents(sales.get(d, 0) for d in basis), basis, None)


_SALES = text("""
SELECT business_date, sum(net_sales) AS net FROM daily_sales
WHERE org_id = :org AND location_id = :loc AND business_date BETWEEN :f AND :t
GROUP BY business_date
""")
_FIRST = text(
    "SELECT min(business_date) FROM daily_sales WHERE org_id = :org AND location_id = :loc"
)


async def forecast_days(
    db: AsyncSession,
    org_id: uuid.UUID,
    location_id: uuid.UUID,
    dates: list[date],
    today: date,
    *,
    connected: bool,
) -> dict[date, Forecast]:
    if not connected:
        return {d: Forecast(d, None, [], "not_connected") for d in dates}
    every = [b for d in dates for b in basis_dates(d, today)]
    p = {"org": org_id, "loc": location_id}
    history_start = await db.scalar(_FIRST, p)
    sales: dict[date, int] = {}
    if every:
        rows = await db.execute(_SALES, {**p, "f": min(every), "t": max(every)})
        sales = {r.business_date: int(r.net) for r in rows}
    return {d: forecast_for(d, today, sales, history_start) for d in dates}
