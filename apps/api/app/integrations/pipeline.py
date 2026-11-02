"""The shared pipeline: land raw -> normalize -> upsert canonical -> rebuild read models.

Everything here runs inside the caller's transaction and is set-based: one INSERT per table
per batch, never per record. Callers pass pages of about 500 items. All functions are
idempotent: a payload with a known hash is skipped, and canonical upserts only apply a
version that is newer than the stored one.
"""

import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.models import (
    MenuItem,
    MenuItemVariant,
    SalesOrder,
    SalesOrderLine,
    SalesOrderReturn,
)
from app.ids import new_id
from app.integrations.identity import IssueCollector, add_refs, resolve_refs
from app.integrations.links import Link
from app.integrations.models import IntegrationLocationLink, RawRecord
from app.integrations.providers.square.normalizer import (
    NORMALIZER_VERSION,
    line_ref,
    normalize_catalog_object,
    normalize_order,
    normalize_team_member,
    normalize_timecard,
)
from app.integrations.providers.square.types import (
    CanonicalMenuCategory,
    CanonicalMenuItem,
    MappingContext,
    MappingIssueSignal,
    RawItem,
)
from app.integrations.readmodels import Pair, rebuild_daily_labor, rebuild_daily_sales
from app.team import service as team
from app.team.models import StaffMember, Timecard

BATCH = 500
SOURCE = "square"


@dataclass(frozen=True)
class ConnCtx:
    org_id: uuid.UUID
    connection_id: uuid.UUID
    links: dict[str, Link]


@dataclass(frozen=True)
class Landed:
    item: RawItem
    sha: str


@dataclass
class BatchResult:
    fetched: int = 0
    landed: int = 0  # new payloads (new hash)
    applied: int = 0  # canonical rows written
    deferred: int = 0  # waiting for a location link
    quarantined: int = 0
    sales_pairs: set[Pair] = field(default_factory=set)
    labor_pairs: set[Pair] = field(default_factory=set)

    def merge(self, other: BatchResult) -> None:
        self.fetched += other.fetched
        self.landed += other.landed
        self.applied += other.applied
        self.deferred += other.deferred
        self.quarantined += other.quarantined
        self.sales_pairs |= other.sales_pairs
        self.labor_pairs |= other.labor_pairs


def payload_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _chunks[T](seq: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _version_key(item: RawItem) -> tuple[int, float]:
    try:
        v = int(item.version) if item.version is not None else 0
    except ValueError:
        v = 0
    return (v, item.updated_at.timestamp() if item.updated_at else 0.0)


# ---------------------------------------------------------------- landing


async def land_raw(
    db: AsyncSession,
    ctx: ConnCtx,
    stream: str,
    items: Sequence[RawItem],
    run_id: uuid.UUID | None,
    now: datetime,
) -> tuple[list[Landed], int]:
    """Insert raw payloads. Returns (all items with hashes, number that were new)."""
    entries = [Landed(i, payload_hash(i.payload)) for i in items]
    new_keys: set[tuple[str, str]] = set()
    for chunk in _chunks(entries, BATCH):
        rows = [
            {
                "id": new_id(),
                "org_id": ctx.org_id,
                "connection_id": ctx.connection_id,
                "stream": stream,
                "external_id": e.item.external_id,
                "source_updated_at": e.item.updated_at,
                "source_version": e.item.version,
                "payload_sha256": e.sha,
                "payload": e.item.payload,
                "fetched_at": now,
                "sync_run_id": run_id,
                "normalize_status": "pending",
            }
            for e in chunk
        ]
        res = await db.execute(
            pg_insert(RawRecord)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_raw_records_hash")
            .returning(RawRecord.external_id, RawRecord.payload_sha256)
        )
        new_keys.update((r.external_id, r.payload_sha256) for r in res)
    return [e for e in entries if (e.item.external_id, e.sha) in new_keys], len(new_keys)


_MARK = text("""
UPDATE raw_records r SET normalize_status = :status, normalizer_version = :version,
  normalize_error = k.err
FROM unnest(CAST(:ext AS text[]), CAST(:sha AS text[]), CAST(:err AS text[])) AS k(e, h, err)
WHERE r.connection_id = :conn AND r.stream = :stream AND r.external_id = k.e
  AND r.payload_sha256 = k.h
""")


async def mark_raw(
    db: AsyncSession,
    ctx: ConnCtx,
    stream: str,
    entries: Sequence[Landed],
    status: str,
    errors: Sequence[str | None] | None = None,
) -> None:
    if not entries:
        return
    await db.execute(
        _MARK,
        {
            "status": status,
            "version": NORMALIZER_VERSION,
            "ext": [e.item.external_id for e in entries],
            "sha": [e.sha for e in entries],
            "err": list(errors) if errors else [None] * len(entries),
            "conn": ctx.connection_id,
            "stream": stream,
        },
    )


# ---------------------------------------------------------------- orders


def _line_catalog_refs(payloads: Iterable[dict[str, Any]]) -> set[str]:
    return {line_ref(li) for p in payloads for li in (p.get("line_items") or [])}


async def process_orders(
    db: AsyncSession, ctx: ConnCtx, entries: Sequence[Landed], now: datetime
) -> BatchResult:
    """Normalize and apply landed order payloads, then rebuild daily_sales for what changed."""
    res = BatchResult(fetched=len(entries))
    if not entries:
        return res
    issues = IssueCollector()
    variants = await resolve_refs(
        db,
        ctx.connection_id,
        "menu_item_variant",
        _line_catalog_refs(e.item.payload for e in entries),
    )
    mctx: dict[str, MappingContext] = {}
    best: dict[str, tuple[Landed, Any]] = {}
    superseded: list[Landed] = []
    quarantined: list[Landed] = []
    q_errors: list[str | None] = []
    deferred = 0
    for e in sorted(entries, key=lambda x: _version_key(x.item)):
        loc_ext = str(e.item.payload.get("location_id") or "")
        link = ctx.links.get(loc_ext)
        if link is None or link.location_id is None:
            label = link.external_name if link else loc_ext
            issues.add(MappingIssueSignal("unmapped_location", loc_ext, label or loc_ext))
            deferred += 1
            continue
        mc = mctx.get(loc_ext)
        if mc is None:
            mc = mctx[loc_ext] = MappingContext(
                timezone=link.timezone,
                business_day_start=link.business_day_start,
                resolve_catalog=lambda ref: ref if ref in variants else None,
            )
        try:
            norm = normalize_order(e.item.payload, mc)
        except Exception as exc:
            quarantined.append(e)
            q_errors.append(f"{type(exc).__name__}: {exc}"[:500])
            continue
        prior = best.get(norm.order.external_id)
        if prior is not None:
            superseded.append(prior[0])
        best[norm.order.external_id] = (e, (norm, link))
    res.deferred = deferred
    res.quarantined = len(quarantined)
    res.landed = len(entries)
    await mark_raw(db, ctx, "orders", quarantined, "quarantined", q_errors)

    applied_entries: list[Landed] = []
    if best:
        applied_entries = await _apply_orders(db, ctx, best, issues, res, now)
    await mark_raw(db, ctx, "orders", [*superseded, *applied_entries], "ok")
    await issues.flush(db, ctx.org_id, ctx.connection_id, now)
    if res.sales_pairs:
        await rebuild_daily_sales(db, ctx.org_id, res.sales_pairs, now)
    return res


_EXISTING_ORDERS = text("""
SELECT id, external_id, location_id, business_date, source_version, source_updated_at
FROM sales_orders WHERE connection_id = :conn AND external_id = ANY(:ids)
""")
_EXISTING_RETURN_DATES = text(
    "SELECT DISTINCT location_id, business_date FROM sales_order_returns WHERE order_id = ANY(:ids)"
)


async def _apply_orders(
    db: AsyncSession,
    ctx: ConnCtx,
    best: dict[str, tuple[Landed, Any]],
    issues: IssueCollector,
    res: BatchResult,
    now: datetime,
) -> list[Landed]:
    existing = {
        r.external_id: r
        for r in await db.execute(_EXISTING_ORDERS, {"conn": ctx.connection_id, "ids": list(best)})
    }
    to_apply: dict[str, tuple[Landed, Any]] = {}
    handled: list[Landed] = []
    for ext, (landed, payload) in best.items():
        order = payload[0].order
        cur = existing.get(ext)
        if cur is not None and (cur.source_version, cur.source_updated_at) >= (
            order.source_version,
            order.source_updated_at,
        ):
            handled.append(landed)  # landed, but an equal or newer version is already applied
            continue
        to_apply[ext] = (landed, payload)
    if not to_apply:
        return handled

    old_ids = [existing[e].id for e in to_apply if e in existing]
    for e in to_apply:
        if e in existing:
            res.sales_pairs.add((existing[e].location_id, existing[e].business_date))
    if old_ids:
        for r in await db.execute(_EXISTING_RETURN_DATES, {"ids": old_ids}):
            res.sales_pairs.add((r.location_id, r.business_date))

    order_rows: list[dict[str, Any]] = []
    for ext, (_, (norm, link)) in to_apply.items():
        o = norm.order
        issues.add_many(norm.signals)
        order_rows.append(
            {
                "id": new_id(),
                "org_id": ctx.org_id,
                "connection_id": ctx.connection_id,
                "external_id": ext,
                "location_id": link.location_id,
                "business_date": o.business_date,
                "opened_at": o.created_at,
                "closed_at": o.closed_at,
                "state": "completed",
                "channel": o.channel,
                "gross_sales": o.gross_sales,
                "discounts": o.discounts,
                "returns": o.returns,
                "net_sales": o.net_sales,
                "tax": o.tax,
                "tips": o.tips,
                "service_charges": o.service_charges,
                "currency": o.currency,
                "source": SOURCE,
                "source_version": o.source_version,
                "source_updated_at": o.source_updated_at,
            }
        )
    stmt = pg_insert(SalesOrder).values(order_rows)
    ex = stmt.excluded
    upd_cols = [
        "location_id", "business_date", "opened_at", "closed_at", "state", "channel",
        "gross_sales", "discounts", "returns", "net_sales", "tax", "tips", "service_charges",
        "currency", "source_version", "source_updated_at",
    ]  # fmt: skip
    returned = await db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_sales_orders_conn_external",
            set_={c: getattr(ex, c) for c in upd_cols},
            where=tuple_(SalesOrder.source_version, SalesOrder.source_updated_at)
            < tuple_(ex.source_version, ex.source_updated_at),
        ).returning(SalesOrder.id, SalesOrder.external_id)
    )
    ids = {r.external_id: r.id for r in returned}
    applied_ext = [e for e in to_apply if e in ids]
    handled.extend(to_apply[e][0] for e in to_apply if e not in ids)  # lost a race to a newer one

    replaced = [ids[e] for e in applied_ext if e in existing]
    if replaced:
        await db.execute(
            text("DELETE FROM sales_order_lines WHERE order_id = ANY(:ids)"), {"ids": replaced}
        )
        await db.execute(
            text("DELETE FROM sales_order_returns WHERE order_id = ANY(:ids)"), {"ids": replaced}
        )

    line_rows: list[dict[str, Any]] = []
    return_rows: list[dict[str, Any]] = []
    for ext in applied_ext:
        _, (norm, link) = to_apply[ext]
        o = norm.order
        oid = ids[ext]
        res.sales_pairs.add((link.location_id, o.business_date))
        for ln in o.lines:
            line_rows.append(
                {
                    "id": new_id(),
                    "org_id": ctx.org_id,
                    "order_id": oid,
                    "location_id": link.location_id,
                    "business_date": o.business_date,
                    "line_uid": ln.line_uid,
                    "menu_item_variant_id": None,
                    "external_item_ref": ln.external_item_ref,
                    "name": ln.name,
                    "quantity": ln.quantity,
                    "gross": ln.gross_cents,
                    "discounts": ln.discount_cents,
                    "net": ln.net_cents,
                    "_variant_ext": ln.menu_item_variant_external_id,
                }
            )
        for r in norm.returns:
            res.sales_pairs.add((link.location_id, r.business_date))
            return_rows.append(
                {
                    "id": new_id(),
                    "org_id": ctx.org_id,
                    "order_id": oid,
                    "location_id": link.location_id,
                    "business_date": r.business_date,
                    "external_id": r.external_id,
                    "processed_at": r.processed_at,
                    "channel": o.channel,
                    "amount": r.net_cents,
                    "tax": r.tax_cents,
                    "reason": r.reason,
                }
            )
    if line_rows:
        variant_ids = await resolve_refs(
            db,
            ctx.connection_id,
            "menu_item_variant",
            {r["_variant_ext"] for r in line_rows if r["_variant_ext"]},
        )
        for lr in line_rows:
            ext_v = lr.pop("_variant_ext")
            lr["menu_item_variant_id"] = variant_ids.get(ext_v) if ext_v else None
        for chunk in _chunks(line_rows, 1500):
            await db.execute(pg_insert(SalesOrderLine).values(list(chunk)))
    for chunk in _chunks(return_rows, 1500):
        await db.execute(pg_insert(SalesOrderReturn).values(list(chunk)))
    res.applied = len(applied_ext)
    return [*handled, *(to_apply[e][0] for e in applied_ext)]


# ---------------------------------------------------------------- timecards

_EXISTING_TIMECARDS = text("""
SELECT external_id, location_id, business_date, source_version, source_updated_at
FROM timecards WHERE connection_id = :conn AND external_id = ANY(:ids)
""")


async def process_timecards(
    db: AsyncSession, ctx: ConnCtx, entries: Sequence[Landed], now: datetime
) -> BatchResult:
    res = BatchResult(fetched=len(entries), landed=len(entries))
    if not entries:
        return res
    issues = IssueCollector()
    staff = await resolve_refs(
        db,
        ctx.connection_id,
        "staff_member",
        {str(e.item.payload.get("team_member_id") or "") for e in entries},
    )
    mctx: dict[str, MappingContext] = {}
    best: dict[str, tuple[Landed, Any, Link]] = {}
    superseded: list[Landed] = []
    quarantined: list[Landed] = []
    q_errors: list[str | None] = []
    for e in sorted(entries, key=lambda x: _version_key(x.item)):
        loc_ext = str(e.item.payload.get("location_id") or "")
        link = ctx.links.get(loc_ext)
        if link is None or link.location_id is None:
            issues.add(
                MappingIssueSignal(
                    "unmapped_location", loc_ext, link.external_name if link else loc_ext
                )
            )
            res.deferred += 1
            continue
        mc = mctx.get(loc_ext)
        if mc is None:
            mc = mctx[loc_ext] = MappingContext(
                timezone=link.timezone,
                business_day_start=link.business_day_start,
                is_known_team_member=lambda tm: tm in staff,
            )
        try:
            norm = normalize_timecard(e.item.payload, mc)
        except Exception as exc:
            quarantined.append(e)
            q_errors.append(f"{type(exc).__name__}: {exc}"[:500])
            continue
        prior = best.get(norm.timecard.external_id)
        if prior is not None:
            superseded.append(prior[0])
        best[norm.timecard.external_id] = (e, norm, link)
    res.quarantined = len(quarantined)
    await mark_raw(db, ctx, "timecards", quarantined, "quarantined", q_errors)

    handled: list[Landed] = list(superseded)
    if best:
        existing = {
            r.external_id: r
            for r in await db.execute(
                _EXISTING_TIMECARDS, {"conn": ctx.connection_id, "ids": list(best)}
            )
        }
        rows: list[dict[str, Any]] = []
        for ext, (landed, norm, link) in best.items():
            tc = norm.timecard
            version = tc.source_version or 1
            updated = tc.source_updated_at or tc.start_at
            cur = existing.get(ext)
            if cur is not None and (cur.source_version, cur.source_updated_at) >= (
                version,
                updated,
            ):
                handled.append(landed)
                continue
            if cur is not None:
                res.labor_pairs.add((cur.location_id, cur.business_date))
            assert link.location_id is not None
            res.labor_pairs.add((link.location_id, tc.business_date))
            issues.add_many(norm.signals)
            handled.append(landed)
            rows.append(
                {
                    "id": new_id(),
                    "org_id": ctx.org_id,
                    "connection_id": ctx.connection_id,
                    "external_id": ext,
                    "location_id": link.location_id,
                    "staff_member_id": staff.get(tc.team_member_external_id),
                    "external_staff_ref": tc.team_member_external_id,
                    "business_date": tc.business_date,
                    "clock_in": tc.start_at,
                    "clock_out": tc.end_at,
                    "break_minutes": tc.unpaid_break_minutes,
                    "hourly_rate": tc.hourly_rate_cents,
                    "paid_hours": tc.paid_hours,
                    "cost": tc.cost_cents,
                    "currency": "USD",
                    "source": SOURCE,
                    "source_version": version,
                    "source_updated_at": updated,
                }
            )
        for chunk in _chunks(rows, BATCH):
            stmt = pg_insert(Timecard).values(list(chunk))
            ex = stmt.excluded
            cols = [
                "location_id", "staff_member_id", "external_staff_ref", "business_date",
                "clock_in", "clock_out", "break_minutes", "hourly_rate", "paid_hours", "cost",
                "source_version", "source_updated_at",
            ]  # fmt: skip
            await db.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_timecards_conn_external",
                    set_={c: getattr(ex, c) for c in cols},
                    where=tuple_(Timecard.source_version, Timecard.source_updated_at)
                    < tuple_(ex.source_version, ex.source_updated_at),
                )
            )
        res.applied = len(rows)
    await mark_raw(db, ctx, "timecards", handled, "ok")
    await issues.flush(db, ctx.org_id, ctx.connection_id, now)
    if res.labor_pairs:
        await rebuild_daily_labor(db, ctx.org_id, res.labor_pairs, now)
    return res


# ---------------------------------------------------------------- reference streams


async def process_catalog(
    db: AsyncSession, ctx: ConnCtx, entries: Sequence[Landed], now: datetime
) -> BatchResult:
    """Catalog is small and fetched whole: every object is applied (upserts are idempotent)."""
    res = BatchResult(fetched=len(entries), landed=len(entries))
    cats = {
        str(e.item.payload["id"]): str((e.item.payload.get("category_data") or {}).get("name", ""))
        for e in entries
        if e.item.payload.get("type") == "CATEGORY"
    }
    items: list[CanonicalMenuItem] = []
    quarantined: list[Landed] = []
    q_errors: list[str | None] = []
    for e in entries:
        try:
            parsed = normalize_catalog_object(e.item.payload, cats)
        except Exception as exc:
            quarantined.append(e)
            q_errors.append(f"{type(exc).__name__}: {exc}"[:500])
            continue
        if isinstance(parsed, CanonicalMenuItem):
            items.append(parsed)
        elif isinstance(parsed, CanonicalMenuCategory):
            pass
    res.quarantined = len(quarantined)
    await mark_raw(db, ctx, "catalog", quarantined, "quarantined", q_errors)
    await mark_raw(db, ctx, "catalog", [e for e in entries if e not in quarantined], "ok")
    if not items:
        return res

    item_refs = await resolve_refs(
        db, ctx.connection_id, "menu_item", [i.external_id for i in items]
    )
    variant_refs = await resolve_refs(
        db,
        ctx.connection_id,
        "menu_item_variant",
        [v.external_id for i in items for v in i.variants],
    )
    new_item_refs: dict[str, uuid.UUID] = {}
    new_variant_refs: dict[str, uuid.UUID] = {}
    item_rows: list[dict[str, Any]] = []
    variant_rows: list[dict[str, Any]] = []
    for it in items:
        item_id = item_refs.get(it.external_id) or new_item_refs.setdefault(
            it.external_id, new_id()
        )
        item_rows.append(
            {
                "id": item_id,
                "org_id": ctx.org_id,
                "name": it.name,
                "category": it.category_name or "",
                "active": not it.deleted,
            }
        )
        for v in it.variants:
            vid = variant_refs.get(v.external_id) or new_variant_refs.setdefault(
                v.external_id, new_id()
            )
            variant_rows.append(
                {
                    "id": vid,
                    "org_id": ctx.org_id,
                    "menu_item_id": item_id,
                    "name": v.name,
                    "price": v.price_cents,
                    "currency": v.currency,
                    "active": not v.deleted,
                }
            )
    stmt = pg_insert(MenuItem).values(item_rows)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={c: getattr(stmt.excluded, c) for c in ("name", "category", "active")},
        )
    )
    stmt_v = pg_insert(MenuItemVariant).values(variant_rows)
    await db.execute(
        stmt_v.on_conflict_do_update(
            index_elements=["id"],
            set_={
                c: getattr(stmt_v.excluded, c)
                for c in ("menu_item_id", "name", "price", "currency", "active")
            },
        )
    )
    await add_refs(db, ctx.org_id, ctx.connection_id, "menu_item", new_item_refs)
    await add_refs(db, ctx.org_id, ctx.connection_id, "menu_item_variant", new_variant_refs)
    if new_variant_refs:  # lines that arrived before their catalog entry
        await db.execute(
            text(
                "UPDATE sales_order_lines l SET menu_item_variant_id = r.entity_id "
                "FROM external_refs r WHERE r.connection_id = :conn "
                "AND r.entity_type = 'menu_item_variant' AND r.external_id = l.external_item_ref "
                "AND l.menu_item_variant_id IS NULL AND l.external_item_ref = ANY(:ids)"
            ),
            {"conn": ctx.connection_id, "ids": list(new_variant_refs)},
        )
    res.applied = len(items)
    return res


async def process_team_members(
    db: AsyncSession, ctx: ConnCtx, entries: Sequence[Landed], now: datetime
) -> BatchResult:
    """Upsert POS-owned employee fields, then hand the rest to Team (`on_staff_imported`).

    Only name, job title, POS rate, and active are written here; team-owned fields (positions,
    locations, rate override, phone, login) are never overwritten by sync.
    """
    res = BatchResult(fetched=len(entries), landed=len(entries))
    if not entries:
        return res
    refs = await resolve_refs(
        db, ctx.connection_id, "staff_member", [e.item.external_id for e in entries]
    )
    was_active = {
        r.id: r.active
        for r in await db.execute(
            text("SELECT id, active FROM staff_members WHERE id = ANY(:ids)"),
            {"ids": list(refs.values())},
        )
    }
    new_refs: dict[str, uuid.UUID] = {}
    rows: dict[uuid.UUID, dict[str, Any]] = {}
    linked = [link.location_id for link in ctx.links.values() if link.location_id is not None]
    imported: dict[uuid.UUID, team.ImportedStaff] = {}
    for e in entries:
        s = normalize_team_member(e.item.payload)
        sid = refs.get(s.external_id) or new_refs.setdefault(s.external_id, new_id())
        name = " ".join(p for p in (s.given_name, s.family_name) if p) or s.external_id
        active = s.status == "active"
        rows[sid] = {
            "id": sid,
            "org_id": ctx.org_id,
            "display_name": name,
            "job_title": s.job_title,
            "hourly_rate": s.hourly_rate_cents,
            "active": active,
            "source": SOURCE,
        }
        if s.assigned_location_ids is None:  # all locations
            locations = tuple(linked)
        else:
            locations = tuple(
                link.location_id
                for ext in s.assigned_location_ids
                if (link := ctx.links.get(ext)) is not None and link.location_id is not None
            )
        imported[sid] = team.ImportedStaff(
            employee_id=sid,
            email=s.email,
            job_title=s.job_title,
            location_ids=locations,
            deactivated=was_active.get(sid, False) and not active,
        )
    stmt = pg_insert(StaffMember).values(list(rows.values()))
    cols = ("display_name", "job_title", "hourly_rate", "active")
    ex = stmt.excluded
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={**{c: getattr(ex, c) for c in cols}, "updated_at": now},
            where=tuple_(*(getattr(StaffMember, c) for c in cols)).is_distinct_from(
                tuple_(*(getattr(ex, c) for c in cols))
            ),
        )
    )
    await add_refs(db, ctx.org_id, ctx.connection_id, "staff_member", new_refs)
    if new_refs:  # timecards that arrived before their team member
        await db.execute(
            text(
                "UPDATE timecards t SET staff_member_id = r.entity_id FROM external_refs r "
                "WHERE r.connection_id = :conn AND r.entity_type = 'staff_member' "
                "AND r.external_id = t.external_staff_ref AND t.staff_member_id IS NULL "
                "AND t.connection_id = :conn AND t.external_staff_ref = ANY(:ids)"
            ),
            {"conn": ctx.connection_id, "ids": list(new_refs)},
        )
    await team.on_staff_imported(db, ctx.org_id, list(imported.values()))
    await mark_raw(db, ctx, "team_members", entries, "ok")
    res.applied = len(rows)
    return res


async def process_locations(
    db: AsyncSession, ctx: ConnCtx, entries: Sequence[Landed], now: datetime
) -> BatchResult:
    """Record Square locations as (unlinked) links. Linking is the owner's decision."""
    res = BatchResult(fetched=len(entries), landed=len(entries))
    if not entries:
        return res
    rows = [
        {
            "id": new_id(),
            "org_id": ctx.org_id,
            "connection_id": ctx.connection_id,
            "external_location_id": e.item.external_id,
            "external_name": str(e.item.payload.get("name") or e.item.external_id),
            "timezone": str(e.item.payload.get("timezone") or "UTC"),
        }
        for e in entries
    ]
    stmt = pg_insert(IntegrationLocationLink).values(rows)
    await db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_location_links_ext",
            set_={"external_name": stmt.excluded.external_name},
        )
    )
    await mark_raw(db, ctx, "locations", entries, "ok")
    res.applied = len(rows)
    return res


# ---------------------------------------------------------------- dispatch

PROCESSORS = {
    "orders": process_orders,
    "timecards": process_timecards,
    "catalog": process_catalog,
    "team_members": process_team_members,
    "locations": process_locations,
}


async def _lock_ingestion(db: AsyncSession, ctx: ConnCtx) -> None:
    # Incremental sync's session lock does not cover webhooks, backfills or deferred replay.
    # Serialize before reading canonical versions, including absent rows. Otherwise an upsert
    # can win after that read while its child replacement and old-date rebuild use stale state.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"ingest:{ctx.org_id}:{ctx.connection_id}"},
    )


async def ingest(
    db: AsyncSession,
    ctx: ConnCtx,
    stream: str,
    items: Sequence[RawItem],
    run_id: uuid.UUID | None,
    now: datetime,
) -> BatchResult:
    """Land then process one batch of fetched items for `stream`."""
    await _lock_ingestion(db, ctx)
    landed, new_count = await land_raw(db, ctx, stream, items, run_id, now)
    # Small full-refresh streams re-apply everything; fact streams only new payloads.
    work = (
        [Landed(i, payload_hash(i.payload)) for i in items]
        if stream in ("catalog", "team_members", "locations")
        else landed
    )
    res = await PROCESSORS[stream](db, ctx, work, now)
    res.fetched = len(items)
    res.landed = new_count
    if work is not landed:
        res.applied = new_count  # re-applied rows are no-ops; report only payloads that changed
    return res


async def process_pending(
    db: AsyncSession, ctx: ConnCtx, stream: str, now: datetime, limit: int = BATCH
) -> BatchResult:
    """Normalize raw records that were deferred (for example, until a location got linked)."""
    from sqlalchemy import select

    await _lock_ingestion(db, ctx)
    rows = (
        await db.execute(
            select(RawRecord)
            .where(
                RawRecord.connection_id == ctx.connection_id,
                RawRecord.stream == stream,
                RawRecord.normalize_status == "pending",
            )
            .order_by(RawRecord.id)
            .limit(limit)
        )
    ).scalars()
    entries = [
        Landed(
            RawItem(r.external_id, r.source_updated_at, r.source_version, r.payload),
            r.payload_sha256,
        )
        for r in rows
    ]
    return await PROCESSORS[stream](db, ctx, entries, now)


def business_dates(pairs: Iterable[Pair]) -> set[date]:
    return {p[1] for p in pairs}
