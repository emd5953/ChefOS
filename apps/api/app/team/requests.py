"""Availability and time off: employee requests, manager decisions, and builder conflicts.

Times on requests are location-local wall clock. Time off covers calendar days (start_time on
the first, end_time on the last); availability blocks are unavailable windows per weekday.
Approving time off never deletes shifts: overlapping draft shifts are flagged in the builder.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.errors import ProblemError, not_found, validation_error
from app.identity.models import Location
from app.team import me, notify, schedule
from app.team.models import (
    AvailabilityBlock,
    AvailabilitySet,
    EmployeeLocation,
    Position,
    PublishedShift,
    StaffMember,
    TimeOffRequest,
)
from app.team.schemas import (
    AvailabilityIn,
    AvailabilityOut,
    Block,
    ConflictOut,
    MyAvailability,
    RequestsOut,
    ShiftRef,
    TimeOffIn,
    TimeOffOut,
)
from app.team.service import Scope, employee_location_ids, get_employee, get_location

MIDNIGHT = time(0, 0)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- intervals


def _at(d: date, t: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(d, t).replace(tzinfo=zone).astimezone(UTC)


def time_off_window(req: TimeOffRequest, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = _at(req.starts_on, req.start_time or MIDNIGHT, zone)
    if req.end_time is not None:
        end = _at(req.ends_on, req.end_time, zone)
    else:
        end = _at(req.ends_on + timedelta(days=1), MIDNIGHT, zone)
    return start, end


def unavailable_hits(
    starts_at: datetime, ends_at: datetime, blocks: Iterable[AvailabilityBlock], zone: ZoneInfo
) -> list[AvailabilityBlock]:
    """Blocks a shift touches, checked on every local calendar day the shift spans."""
    by_day: dict[int, list[AvailabilityBlock]] = defaultdict(list)
    for b in blocks:
        by_day[b.weekday].append(b)
    hits: list[AvailabilityBlock] = []
    d = starts_at.astimezone(zone).date()
    last = ends_at.astimezone(zone).date()
    while d <= last:
        for b in by_day.get(d.weekday(), []):
            if b.start_time is None or b.end_time is None:
                lo, hi = _at(d, MIDNIGHT, zone), _at(d + timedelta(days=1), MIDNIGHT, zone)
            else:
                lo, hi = _at(d, b.start_time, zone), _at(d, b.end_time, zone)
            if starts_at < hi and ends_at > lo and b not in hits:
                hits.append(b)
        d += timedelta(days=1)
    return hits


def _hhmm(t: time | None) -> str:
    return t.strftime("%H:%M") if t else ""


def _days(req: TimeOffRequest) -> str:
    if req.starts_on == req.ends_on:
        return req.starts_on.strftime("%a %b %-d")
    return f"{req.starts_on.strftime('%b %-d')}-{req.ends_on.strftime('%b %-d')}"


# ---------------------------------------------------------------- builder conflicts


async def _effective_sets(
    db: AsyncSession, employee_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[AvailabilitySet, list[AvailabilityBlock]]]]:
    """Approved sets per employee, newest effective_from first."""
    if not employee_ids:
        return {}
    sets = list(
        await db.scalars(
            select(AvailabilitySet)
            .where(
                AvailabilitySet.employee_id.in_(employee_ids),
                AvailabilitySet.status == "approved",
            )
            .order_by(AvailabilitySet.effective_from.desc(), AvailabilitySet.decided_at.desc())
        )
    )
    blocks: dict[uuid.UUID, list[AvailabilityBlock]] = defaultdict(list)
    if sets:
        for b in await db.scalars(
            select(AvailabilityBlock).where(AvailabilityBlock.set_id.in_([s.id for s in sets]))
        ):
            blocks[b.set_id].append(b)
    out: dict[uuid.UUID, list[tuple[AvailabilitySet, list[AvailabilityBlock]]]] = defaultdict(list)
    for s in sets:
        out[s.employee_id].append((s, blocks[s.id]))
    return out


def set_for(
    sets: list[tuple[AvailabilitySet, list[AvailabilityBlock]]], on: date
) -> tuple[AvailabilitySet, list[AvailabilityBlock]] | None:
    return next(((s, b) for s, b in sets if s.effective_from <= on), None)


async def request_conflicts(db: AsyncSession, ctx: schedule.ConflictContext) -> list[ConflictOut]:
    assigned = [s for s in ctx.shifts if s.employee_id is not None]
    if not assigned:
        return []
    emp_ids = {s.employee_id for s in assigned if s.employee_id is not None}
    lo = min(s.business_date for s in assigned) - timedelta(days=1)
    hi = max(s.business_date for s in assigned) + timedelta(days=1)
    reqs = list(
        await db.scalars(
            select(TimeOffRequest).where(
                TimeOffRequest.employee_id.in_(emp_ids),
                TimeOffRequest.status.in_(("pending", "approved")),
                TimeOffRequest.starts_on <= hi,
                TimeOffRequest.ends_on >= lo,
            )
        )
    )
    by_emp: dict[uuid.UUID, list[TimeOffRequest]] = defaultdict(list)
    for r in reqs:
        by_emp[r.employee_id].append(r)
    sets = await _effective_sets(db, emp_ids)
    out: list[ConflictOut] = []
    for s in assigned:
        assert s.employee_id is not None
        emp = ctx.employees.get(s.employee_id)
        name = emp.display_name if emp else "This employee"
        for r in by_emp.get(s.employee_id, []):
            start, end = time_off_window(r, ctx.zone)
            if s.starts_at < end and s.ends_at > start:
                kind = "time_off_approved" if r.status == "approved" else "time_off_pending"
                verb = "has approved time off" if r.status == "approved" else "requested time off"
                out.append(
                    ConflictOut(shift_id=s.id, kind=kind, detail=f"{name} {verb} {_days(r)}")
                )
        current = set_for(sets.get(s.employee_id, []), s.business_date)
        if current is not None:
            for b in unavailable_hits(s.starts_at, s.ends_at, current[1], ctx.zone):
                window = f"{_hhmm(b.start_time)}-{_hhmm(b.end_time)}" if b.start_time else "all day"
                day = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[b.weekday]
                out.append(
                    ConflictOut(
                        shift_id=s.id,
                        kind="unavailable",
                        detail=f"{name} is unavailable {day} {window}",
                    )
                )
    return out


# Registered here so schedule.py doesn't import this module (requests -> schedule only).
schedule.CONFLICT_HOOKS.append(request_conflicts)


# ---------------------------------------------------------------- serialization


async def _names(db: AsyncSession, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    ids = set(ids)
    if not ids:
        return {}
    rows = await db.execute(
        select(StaffMember.id, StaffMember.display_name).where(StaffMember.id.in_(ids))
    )
    return {r.id: r.display_name for r in rows}


async def _sets_out(
    db: AsyncSession, sets: list[AvailabilitySet], *, names: bool = False
) -> list[AvailabilityOut]:
    blocks: dict[uuid.UUID, list[Block]] = defaultdict(list)
    if sets:
        for b in await db.scalars(
            select(AvailabilityBlock)
            .where(AvailabilityBlock.set_id.in_([s.id for s in sets]))
            .order_by(AvailabilityBlock.weekday, AvailabilityBlock.start_time)
        ):
            blocks[b.set_id].append(Block.model_validate(b))
    who = await _names(db, [s.employee_id for s in sets]) if names else {}
    return [
        AvailabilityOut(
            id=s.id,
            employee_id=s.employee_id,
            employee_name=who.get(s.employee_id),
            status=s.status,
            effective_from=s.effective_from,
            note=s.note,
            blocks=blocks[s.id],
            submitted_at=s.submitted_at,
            decided_at=s.decided_at,
            decision_note=s.decision_note,
        )
        for s in sets
    ]


def _time_off_out(r: TimeOffRequest, name: str | None = None) -> TimeOffOut:
    return TimeOffOut(
        id=r.id,
        employee_id=r.employee_id,
        employee_name=name,
        starts_on=r.starts_on,
        ends_on=r.ends_on,
        start_time=r.start_time,
        end_time=r.end_time,
        reason=r.reason,
        status=r.status,
        created_at=r.created_at,
        decided_at=r.decided_at,
        decision_note=r.decision_note,
    )


async def _local_today(db: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID) -> date:
    return await me.local_today(db, org_id, await me.my_location_ids(db, employee_id))


# ---------------------------------------------------------------- employee side


async def my_availability(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> MyAvailability:
    emp = await me.my_employee(db, org_id, user_id)
    today = await _local_today(db, org_id, emp.id)
    sets = list(
        await db.scalars(
            select(AvailabilitySet)
            .where(
                AvailabilitySet.employee_id == emp.id,
                AvailabilitySet.status.in_(("pending", "approved")),
            )
            .order_by(AvailabilitySet.effective_from.desc(), AvailabilitySet.submitted_at.desc())
        )
    )
    out = await _sets_out(db, sets)
    approved = [o for o in out if o.status == "approved"]
    current = next((o for o in approved if o.effective_from <= today), None)
    return MyAvailability(
        current=current,
        pending=next((o for o in out if o.status == "pending"), None),
        upcoming=sorted(
            (o for o in approved if o.effective_from > today), key=lambda o: o.effective_from
        ),
    )


async def submit_availability(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, data: AvailabilityIn
) -> AvailabilityOut:
    emp = await me.my_employee(db, org_id, user_id)
    if data.effective_from < await _local_today(db, org_id, emp.id):
        raise validation_error({"effective_from": "Must be today or later"})
    # Lock the employee: one pending set at a time.
    await db.execute(select(StaffMember.id).where(StaffMember.id == emp.id).with_for_update())
    superseded = list(
        await db.scalars(
            update(AvailabilitySet)
            .where(AvailabilitySet.employee_id == emp.id, AvailabilitySet.status == "pending")
            .values(status="superseded")
            .returning(AvailabilitySet.id)
        )
    )
    row = AvailabilitySet(
        org_id=org_id,
        employee_id=emp.id,
        effective_from=data.effective_from,
        note=data.note.strip(),
        submitted_by=user_id,
    )
    db.add(row)
    await db.flush()
    for b in data.blocks:
        db.add(
            AvailabilityBlock(
                org_id=org_id,
                set_id=row.id,
                weekday=b.weekday,
                start_time=b.start_time,
                end_time=b.end_time,
            )
        )
    await db.flush()
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="availability",
        entity_id=row.id,
        action="availability.submitted",
        details={
            "employee_id": str(emp.id),
            "effective_from": data.effective_from.isoformat(),
            "blocks": len(data.blocks),
            "superseded": [str(i) for i in superseded],
        },
    )
    await notify.notify(
        db,
        org_id,
        await notify.managers_for(db, org_id, await employee_location_ids_one(db, emp.id)),
        kind="availability_submitted",
        title=f"{emp.display_name} updated their availability",
        body=f"Starting {data.effective_from.strftime('%a %b %-d')}. Review it in Requests.",
        link="/app/team/requests",
        entity_type="availability",
        entity_id=row.id,
    )
    return (await _sets_out(db, [row]))[0]


async def employee_location_ids_one(db: AsyncSession, employee_id: uuid.UUID) -> set[uuid.UUID]:
    return (await employee_location_ids(db, [employee_id])).get(employee_id, set())


async def my_time_off(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> list[TimeOffOut]:
    emp = await me.my_employee(db, org_id, user_id)
    rows = await db.scalars(
        select(TimeOffRequest)
        .where(TimeOffRequest.employee_id == emp.id)
        .order_by(TimeOffRequest.starts_on.desc(), TimeOffRequest.created_at.desc())
        .limit(100)
    )
    return [_time_off_out(r) for r in rows]


async def request_time_off(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, data: TimeOffIn
) -> TimeOffOut:
    emp = await me.my_employee(db, org_id, user_id)
    if data.starts_on < await _local_today(db, org_id, emp.id):
        raise validation_error({"starts_on": "Time off can't start in the past"})
    req = TimeOffRequest(
        org_id=org_id,
        employee_id=emp.id,
        starts_on=data.starts_on,
        ends_on=data.ends_on,
        start_time=data.start_time,
        end_time=data.end_time,
        reason=data.reason.strip(),
        created_by=user_id,
    )
    db.add(req)
    await db.flush()
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="time_off",
        entity_id=req.id,
        action="time_off.requested",
        details={
            "employee_id": str(emp.id),
            "starts_on": data.starts_on.isoformat(),
            "ends_on": data.ends_on.isoformat(),
        },
    )
    await notify.notify(
        db,
        org_id,
        await notify.managers_for(db, org_id, await employee_location_ids_one(db, emp.id)),
        kind="time_off_requested",
        title=f"{emp.display_name} requested time off {_days(req)}",
        body=req.reason or "Review it in Requests.",
        link="/app/team/requests",
        entity_type="time_off",
        entity_id=req.id,
    )
    return _time_off_out(req)


async def cancel_time_off(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, request_id: uuid.UUID
) -> TimeOffOut:
    emp = await me.my_employee(db, org_id, user_id)
    req = await db.scalar(
        select(TimeOffRequest)
        .where(TimeOffRequest.id == request_id, TimeOffRequest.employee_id == emp.id)
        .with_for_update()
    )
    if req is None:  # someone else's request reads as missing
        raise not_found("Request not found")
    if req.status not in ("pending", "approved"):
        raise ProblemError(409, "conflict", f"This request is already {req.status}")
    if req.ends_on < await _local_today(db, org_id, emp.id):
        raise ProblemError(409, "conflict", "This time off is already over")
    before = req.status
    req.status = "cancelled"
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="time_off",
        entity_id=req.id,
        action="time_off.cancelled",
        details={"before": {"status": before}, "after": {"status": "cancelled"}},
    )
    await db.flush()
    return _time_off_out(req)


# ---------------------------------------------------------------- manager side


async def _visible_employees(
    db: AsyncSession, scope: Scope, location_id: uuid.UUID | None
) -> set[uuid.UUID] | None:
    """None = every employee (owner/admin, no location filter)."""
    if location_id is not None:
        await get_location(db, scope, location_id)
        locs: Iterable[uuid.UUID] = [location_id]
    elif scope.is_admin:
        return None
    else:
        locs = scope.location_ids
    return set(
        await db.scalars(
            select(EmployeeLocation.employee_id).where(EmployeeLocation.location_id.in_(list(locs)))
        )
    )


async def _overlapping_published(
    db: AsyncSession, reqs: list[TimeOffRequest]
) -> dict[uuid.UUID, list[ShiftRef]]:
    if not reqs:
        return {}
    lo = min(r.starts_on for r in reqs) - timedelta(days=1)
    hi = max(r.ends_on for r in reqs) + timedelta(days=1)
    rows = (
        await db.execute(
            select(PublishedShift, Position.name, Location.timezone)
            .join(Position, Position.id == PublishedShift.position_id)
            .join(Location, Location.id == PublishedShift.location_id)
            .where(
                PublishedShift.employee_id.in_({r.employee_id for r in reqs}),
                PublishedShift.business_date.between(lo, hi),
            )
            .order_by(PublishedShift.starts_at)
        )
    ).all()
    out: dict[uuid.UUID, list[ShiftRef]] = defaultdict(list)
    for r in reqs:
        for shift, pos_name, tz in rows:
            if shift.employee_id != r.employee_id:
                continue
            start, end = time_off_window(r, ZoneInfo(tz))
            if shift.starts_at < end and shift.ends_at > start:
                out[r.id].append(
                    ShiftRef(
                        id=shift.id,
                        location_id=shift.location_id,
                        business_date=shift.business_date,
                        starts_at=shift.starts_at,
                        ends_at=shift.ends_at,
                        position_name=pos_name,
                    )
                )
    return out


async def list_requests(
    db: AsyncSession, scope: Scope, location_id: uuid.UUID | None, status: str
) -> RequestsOut:
    visible = await _visible_employees(db, scope, location_id)
    t_stmt = select(TimeOffRequest).where(TimeOffRequest.org_id == scope.org_id)
    a_stmt = select(AvailabilitySet).where(AvailabilitySet.org_id == scope.org_id)
    if visible is not None:
        t_stmt = t_stmt.where(TimeOffRequest.employee_id.in_(visible))
        a_stmt = a_stmt.where(AvailabilitySet.employee_id.in_(visible))
    if status == "pending":
        t_stmt = t_stmt.where(TimeOffRequest.status == "pending")
        a_stmt = a_stmt.where(AvailabilitySet.status == "pending")
        t_stmt = t_stmt.order_by(TimeOffRequest.starts_on, TimeOffRequest.created_at)
        a_stmt = a_stmt.order_by(AvailabilitySet.submitted_at)
    else:  # history: decided, newest first
        t_stmt = t_stmt.where(TimeOffRequest.status != "pending")
        a_stmt = a_stmt.where(
            or_(AvailabilitySet.status == "approved", AvailabilitySet.status == "rejected")
        )
        t_stmt = t_stmt.order_by(TimeOffRequest.created_at.desc())
        a_stmt = a_stmt.order_by(AvailabilitySet.submitted_at.desc())
    reqs = list(await db.scalars(t_stmt.limit(200)))
    sets = list(await db.scalars(a_stmt.limit(200)))
    names = await _names(db, [r.employee_id for r in reqs])
    overlaps = await _overlapping_published(db, reqs)
    time_off = []
    for r in reqs:
        out = _time_off_out(r, names.get(r.employee_id))
        out.overlapping_shifts = overlaps.get(r.id, [])
        time_off.append(out)
    return RequestsOut(time_off=time_off, availability=await _sets_out(db, sets, names=True))


async def decide_time_off(
    db: AsyncSession, scope: Scope, request_id: uuid.UUID, decision: str, note: str | None
) -> TimeOffOut:
    if decision not in ("approve", "deny"):
        raise validation_error({"decision": "Use approve or deny"})
    req = await db.scalar(
        select(TimeOffRequest)
        .where(TimeOffRequest.id == request_id, TimeOffRequest.org_id == scope.org_id)
        .with_for_update()
    )
    if req is None:
        raise not_found("Request not found")
    emp = await get_employee(db, scope, req.employee_id)  # 404 outside the manager's scope
    if req.status != "pending":
        raise ProblemError(409, "conflict", f"This request is already {req.status}")
    req.status = "approved" if decision == "approve" else "denied"
    req.decided_by = scope.user_id
    req.decided_at = _now()
    req.decision_note = (note or "").strip() or None
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="time_off",
        entity_id=req.id,
        action=f"time_off.{req.status}",
        details={"employee_id": str(emp.id), "note": req.decision_note},
    )
    if emp.user_id is not None:
        await notify.notify(
            db,
            scope.org_id,
            [emp.user_id],
            kind="time_off_decided",
            title=f"Time off {_days(req)} {req.status}",
            body=req.decision_note or "",
            link="/my/requests",
            entity_type="time_off",
            entity_id=req.id,
        )
    await db.flush()
    out = _time_off_out(req, emp.display_name)
    out.overlapping_shifts = (await _overlapping_published(db, [req])).get(req.id, [])
    return out


async def decide_availability(
    db: AsyncSession, scope: Scope, set_id: uuid.UUID, decision: str, note: str | None
) -> AvailabilityOut:
    if decision not in ("approve", "reject"):
        raise validation_error({"decision": "Use approve or reject"})
    row = await db.scalar(
        select(AvailabilitySet)
        .where(AvailabilitySet.id == set_id, AvailabilitySet.org_id == scope.org_id)
        .with_for_update()
    )
    if row is None:
        raise not_found("Availability not found")
    emp = await get_employee(db, scope, row.employee_id)
    if row.status != "pending":
        raise ProblemError(409, "conflict", f"This availability is already {row.status}")
    row.status = "approved" if decision == "approve" else "rejected"
    row.decided_by = scope.user_id
    row.decided_at = _now()
    row.decision_note = (note or "").strip() or None
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="availability",
        entity_id=row.id,
        action=f"availability.{row.status}",
        details={"employee_id": str(emp.id), "note": row.decision_note},
    )
    if emp.user_id is not None:
        await notify.notify(
            db,
            scope.org_id,
            [emp.user_id],
            kind="availability_decided",
            title=f"Your availability was {row.status}",
            body=row.decision_note or f"Effective {row.effective_from.strftime('%a %b %-d')}.",
            link="/my/requests",
            entity_type="availability",
            entity_id=row.id,
        )
    await db.flush()
    return (await _sets_out(db, [row], names=True))[0]
