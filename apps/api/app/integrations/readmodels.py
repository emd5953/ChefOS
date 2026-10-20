"""Read-model rebuilds: daily_sales and daily_labor, per affected (location, business_date).

Rebuilds read canonical tables only, so they are idempotent: the same canonical state always
produces the same rows. A transaction-scoped advisory lock per key keeps concurrent writers
(incremental sync vs. backfill) from interleaving a delete/insert.
"""

import uuid
from collections.abc import Iterable
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Pair = tuple[uuid.UUID, date]

_LOCK = text(
    "SELECT pg_advisory_xact_lock(k) FROM ("
    "SELECT hashtextextended(:kind || ':' || l::text || ':' || d::text, 0) AS k "
    "FROM unnest(CAST(:locs AS uuid[]), CAST(:dates AS date[])) AS t(l, d) ORDER BY 1) s"
)

_DELETE_SALES = text(
    "DELETE FROM daily_sales ds USING unnest(CAST(:locs AS uuid[]), CAST(:dates AS date[])) "
    "AS k(l, d) WHERE ds.org_id = :org AND ds.location_id = k.l AND ds.business_date = k.d"
)
_DELETE_LABOR = text(
    "DELETE FROM daily_labor dl USING unnest(CAST(:locs AS uuid[]), CAST(:dates AS date[])) "
    "AS k(l, d) WHERE dl.org_id = :org AND dl.location_id = k.l AND dl.business_date = k.d"
)

_REBUILD_SALES = text("""
WITH keys AS (
  SELECT l AS location_id, d AS business_date
  FROM unnest(CAST(:locs AS uuid[]), CAST(:dates AS date[])) AS t(l, d)
), s AS (
  SELECT o.location_id, o.business_date, o.channel, count(*) AS orders,
         sum(o.gross_sales) AS gross, sum(o.discounts) AS disc, sum(o.tax) AS tax,
         sum(o.tips) AS tips, max(o.currency) AS currency
  FROM sales_orders o JOIN keys k
    ON k.location_id = o.location_id AND k.business_date = o.business_date
  WHERE o.org_id = :org AND o.state = 'completed'
  GROUP BY 1, 2, 3
), r AS (
  SELECT x.location_id, x.business_date, x.channel, sum(x.amount) AS amount
  FROM sales_order_returns x JOIN keys k
    ON k.location_id = x.location_id AND k.business_date = x.business_date
  WHERE x.org_id = :org
  GROUP BY 1, 2, 3
)
INSERT INTO daily_sales (org_id, location_id, business_date, channel, orders, net_sales,
  gross_sales, discounts, returns, tax, tips, currency, computed_at, data_as_of)
SELECT :org, COALESCE(s.location_id, r.location_id), COALESCE(s.business_date, r.business_date),
  COALESCE(s.channel, r.channel), COALESCE(s.orders, 0),
  COALESCE(s.gross, 0) - COALESCE(s.disc, 0) - COALESCE(r.amount, 0),
  COALESCE(s.gross, 0), COALESCE(s.disc, 0), COALESCE(r.amount, 0),
  COALESCE(s.tax, 0), COALESCE(s.tips, 0), COALESCE(s.currency, 'USD'), now(), :as_of
FROM s FULL JOIN r ON s.location_id = r.location_id
  AND s.business_date = r.business_date AND s.channel = r.channel
""")

_REBUILD_LABOR = text("""
WITH keys AS (
  SELECT l AS location_id, d AS business_date
  FROM unnest(CAST(:locs AS uuid[]), CAST(:dates AS date[])) AS t(l, d)
)
INSERT INTO daily_labor (org_id, location_id, business_date, scheduled_hours, actual_hours,
  actual_cost, currency, computed_at, data_as_of)
SELECT :org, t.location_id, t.business_date, 0, sum(t.paid_hours), sum(t.cost),
  max(t.currency), now(), :as_of
FROM timecards t JOIN keys k
  ON k.location_id = t.location_id AND k.business_date = t.business_date
WHERE t.org_id = :org
GROUP BY t.location_id, t.business_date
""")


def _params(org_id: uuid.UUID, pairs: Iterable[Pair], as_of: datetime) -> dict[str, object] | None:
    ordered = sorted(set(pairs))
    if not ordered:
        return None
    return {
        "org": org_id,
        "locs": [p[0] for p in ordered],
        "dates": [p[1] for p in ordered],
        "as_of": as_of,
    }


async def rebuild_daily_sales(
    db: AsyncSession, org_id: uuid.UUID, pairs: Iterable[Pair], as_of: datetime
) -> int:
    params = _params(org_id, pairs, as_of)
    if params is None:
        return 0
    await db.execute(_LOCK, {"kind": "sales", "locs": params["locs"], "dates": params["dates"]})
    await db.execute(_DELETE_SALES, {k: params[k] for k in ("org", "locs", "dates")})
    await db.execute(_REBUILD_SALES, params)
    return len(params["dates"])  # type: ignore[arg-type]


async def rebuild_daily_labor(
    db: AsyncSession, org_id: uuid.UUID, pairs: Iterable[Pair], as_of: datetime
) -> int:
    params = _params(org_id, pairs, as_of)
    if params is None:
        return 0
    await db.execute(_LOCK, {"kind": "labor", "locs": params["locs"], "dates": params["dates"]})
    await db.execute(_DELETE_LABOR, {k: params[k] for k in ("org", "locs", "dates")})
    await db.execute(_REBUILD_LABOR, params)
    return len(params["dates"])  # type: ignore[arg-type]
