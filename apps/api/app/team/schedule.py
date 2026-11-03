"""Schedule builder: draft shifts, projections against forecast, conflicts, copy week.

Every shift write locks the schedule row and bumps `schedules.version`; publish (T4) checks
that version. Writes for an employee also lock the employee row, so two concurrent writes
can't both pass the overlap check.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.commerce import service as commerce
from app.errors import ProblemError, not_found, validation_error
from app.identity.models import Location
from app.ids import new_id
from app.integrations.freshness import location_freshness
from app.integrations.providers.square.business_date import business_date
from app.integrations.readmodels import refresh_scheduled_hours
from app.team import forecast, notify
from app.team.models import (
    EmployeeLocation,
    EmployeePosition,
    Position,
    PublishedShift,
    Schedule,
    Shift,
    StaffMember,
    Station,
)
from app.team.schemas import (
    ConflictOut,
    CopyOut,
    EmployeeRef,
    FreshnessOut,
    PublishOut,
    ScheduleDay,
    ScheduleEmployee,
    ScheduleMeta,
    ScheduleOut,
    ScheduleWeek,
    ShiftIn,
    ShiftOut,
    ShiftPatch,
)
from app.team.service import Scope, effective_rate, get_location

MIN_SHIFT = timedelta(minutes=15)
MAX_SHIFT = timedelta(hours=16)
HOURS = Decimal(3600)
# Fields whose change makes a shift differ from its published copy.
SNAPSHOT_FIELDS = (
    "employee_id", "location_id", "position_id", "station_id", "business_date", "starts_at",
    "ends_at", "break_minutes", "notes",
)  # fmt: skip


# Copied from shifts into published_shifts on publish.
PUBLISHED_COLUMNS = (
    "id", "org_id", "location_id", "schedule_id", "employee_id", "position_id", "station_id",
    "business_date", "starts_at", "ends_at", "break_minutes", "notes", "created_by", "created_at",
    "updated_at",
)  # fmt: skip


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- pure helpers


def check_week_start(week_start: date) -> None:
    if week_start.weekday() != 0:
        raise validation_error({"week_start": "Weeks start on Monday"})


def week_dates(week_start: date) -> list[date]:
    return [week_start + timedelta(days=i) for i in range(7)]


def paid_hours(starts_at: datetime, ends_at: datetime, break_minutes: int) -> Decimal:
    """Timestamp difference minus the unpaid break, so DST days come out right. Subtracts in UTC:
    Python compares two datetimes sharing one tzinfo by wall clock, which is wrong across DST."""
    seconds = Decimal(int((ends_at.astimezone(UTC) - starts_at.astimezone(UTC)).total_seconds()))
    return (seconds - Decimal(break_minutes * 60)) / HOURS


def shift_cost(hours: Decimal, rate: int | None) -> int | None:
    if rate is None:
        return None
    return int((hours * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def check_times(starts_at: datetime, ends_at: datetime, break_minutes: int) -> None:
    length = ends_at.astimezone(UTC) - starts_at.astimezone(UTC)
    if length <= timedelta(0):
        raise validation_error({"ends_at": "Must be after the start"})
    if length < MIN_SHIFT:
        raise validation_error({"ends_at": "Shifts are at least 15 minutes"})
    if length > MAX_SHIFT:
        raise validation_error({"ends_at": "Shifts are at most 16 hours"})
    if timedelta(minutes=break_minutes) >= length:
        raise validation_error({"break_minutes": "Break must be shorter than the shift"})


def shift_by_weeks(ts: datetime, weeks: int, zone: ZoneInfo) -> datetime:
    """Move a timestamp by whole weeks in local wall time (4pm stays 4pm across DST)."""
    local = ts.astimezone(zone).replace(tzinfo=None) + timedelta(weeks=weeks)
    return local.replace(tzinfo=zone).astimezone(UTC)


def label_time(ts: datetime, zone: ZoneInfo) -> str:
    local = ts.astimezone(zone)
    hour = local.hour % 12 or 12
    suffix = "am" if local.hour < 12 else "pm"
    return f"{hour}{suffix}" if local.minute == 0 else f"{hour}:{local.minute:02d}{suffix}"


def label_shift(starts_at: datetime, ends_at: datetime, zone: ZoneInfo) -> str:
    day = starts_at.astimezone(zone).strftime("%a")
    return f"{day} {label_time(starts_at, zone)}-{label_time(ends_at, zone)}"


def snapshot_key(s: Shift | PublishedShift) -> tuple[Any, ...]:
    return tuple(getattr(s, f) for f in SNAPSHOT_FIELDS)


def count_changes(
    draft: Sequence[Shift | PublishedShift], published: Sequence[Shift | PublishedShift]
) -> int:
    a = {s.id: snapshot_key(s) for s in draft}
    b = {s.id: snapshot_key(s) for s in published}
    return len(a.keys() ^ b.keys()) + sum(1 for k in a.keys() & b.keys() if a[k] != b[k])


# ---------------------------------------------------------------- loading


async def get_schedule(
    db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID, week_start: date
) -> Schedule | None:
    sched: Schedule | None = await db.scalar(
        select(Schedule).where(
            Schedule.org_id == org_id,
            Schedule.location_id == location_id,
            Schedule.week_start == week_start,
        )
    )
    return sched


async def lock_schedule(
    db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID, week_start: date
) -> Schedule:
    """Get or create the week's schedule row, locked for this transaction."""
    await db.execute(
        pg_insert(Schedule)
        .values(id=new_id(), org_id=org_id, location_id=location_id, week_start=week_start)
        .on_conflict_do_nothing(constraint="uq_schedules_location_week")
    )
    sched = await db.scalar(
        select(Schedule)
        .where(Schedule.location_id == location_id, Schedule.week_start == week_start)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert sched is not None
    return sched


async def bump(sched: Schedule) -> None:
    sched.version += 1
    sched.updated_at = _now()


async def _rates(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, StaffMember]:
    if not ids:
        return {}
    return {e.id: e for e in await db.scalars(select(StaffMember).where(StaffMember.id.in_(ids)))}


def shift_out(s: Shift, emp: StaffMember | None) -> ShiftOut:
    hours = paid_hours(s.starts_at, s.ends_at, s.break_minutes)
    return ShiftOut(
        id=s.id,
        location_id=s.location_id,
        employee_id=s.employee_id,
        position_id=s.position_id,
        station_id=s.station_id,
        business_date=s.business_date,
        starts_at=s.starts_at,
        ends_at=s.ends_at,
        break_minutes=s.break_minutes,
        notes=s.notes,
        paid_hours=round(float(hours), 2),
        cost=shift_cost(hours, effective_rate(emp)) if emp is not None else None,
        updated_at=s.updated_at,
    )


# ---------------------------------------------------------------- conflicts


@dataclass
class ConflictContext:
    location: Location
    zone: ZoneInfo
    shifts: list[Shift]
    employees: dict[uuid.UUID, StaffMember]
    assigned: set[uuid.UUID]  # employees who work at this location


async def _overlaps(db: AsyncSession, shifts: list[Shift]) -> dict[uuid.UUID, Shift]:
    """shift id -> one other draft shift (any location) that overlaps it for the same person."""
    mine = [s for s in shifts if s.employee_id is not None]
    if not mine:
        return {}
    lo = min(s.starts_at for s in mine)
    hi = max(s.ends_at for s in mine)
    others = list(
        await db.scalars(
            select(Shift).where(
                Shift.employee_id.in_({s.employee_id for s in mine}),
                Shift.starts_at < hi,
                Shift.ends_at > lo,
            )
        )
    )
    by_emp: dict[uuid.UUID, list[Shift]] = defaultdict(list)
    for o in others:
        assert o.employee_id is not None
        by_emp[o.employee_id].append(o)
    out: dict[uuid.UUID, Shift] = {}
    for s in mine:
        assert s.employee_id is not None
        for o in by_emp[s.employee_id]:
            if o.id != s.id and o.starts_at < s.ends_at and o.ends_at > s.starts_at:
                out[s.id] = o
                break
    return out


async def conflicts_for(db: AsyncSession, ctx: ConflictContext) -> list[ConflictOut]:
    out: list[ConflictOut] = []
    overlaps = await _overlaps(db, ctx.shifts)
    for s in ctx.shifts:
        if s.employee_id is None:
            out.append(ConflictOut(shift_id=s.id, kind="unassigned", detail="No one assigned"))
            continue
        emp = ctx.employees.get(s.employee_id)
        name = emp.display_name if emp else "This employee"
        if s.id in overlaps:
            o = overlaps[s.id]
            out.append(
                ConflictOut(
                    shift_id=s.id,
                    kind="overlap",
                    detail=f"{name} also works {label_shift(o.starts_at, o.ends_at, ctx.zone)}",
                )
            )
        if emp is None or not emp.active or emp.merged_into_id is not None:
            out.append(
                ConflictOut(shift_id=s.id, kind="inactive_employee", detail=f"{name} is inactive")
            )
        if s.employee_id not in ctx.assigned:
            out.append(
                ConflictOut(
                    shift_id=s.id,
                    kind="not_assigned_to_location",
                    detail=f"{name} isn't assigned to {ctx.location.name}",
                )
            )
    for hook in CONFLICT_HOOKS:
        out.extend(await hook(db, ctx))
    return out


# Later milestones add conflict sources (time off, availability) here.
CONFLICT_HOOKS: list[Any] = []
PUBLISH_REMOVE_HOOKS: list[Any] = []


# ---------------------------------------------------------------- read


def _pct(cost: int, sales: int | None) -> float | None:
    return round(cost / sales * 100, 1) if sales else None


async def _assigned(db: AsyncSession, location_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        await db.scalars(
            select(EmployeeLocation.employee_id).where(EmployeeLocation.location_id == location_id)
        )
    )


async def read_schedule(
    db: AsyncSession, scope: Scope, location_id: uuid.UUID, week_start: date
) -> ScheduleOut:
    check_week_start(week_start)
    loc = await get_location(db, scope, location_id)
    zone = ZoneInfo(loc.timezone)
    sched = await get_schedule(db, scope.org_id, loc.id, week_start)
    shifts: list[Shift] = []
    published: list[PublishedShift] = []
    if sched is not None:
        shifts = list(
            await db.scalars(
                select(Shift)
                .where(Shift.schedule_id == sched.id)
                .order_by(Shift.starts_at, Shift.id)
            )
        )
        published = list(
            await db.scalars(select(PublishedShift).where(PublishedShift.schedule_id == sched.id))
        )
    assigned = await _assigned(db, loc.id)
    referenced = {s.employee_id for s in shifts if s.employee_id is not None}
    employees = await _rates(db, referenced | assigned)
    roster_ids = {
        e.id
        for e in employees.values()
        if e.id in referenced or (e.active and e.merged_into_id is None)
    }
    positions: dict[uuid.UUID, list[tuple[uuid.UUID, bool]]] = defaultdict(list)
    if roster_ids:
        for r in await db.execute(
            select(
                EmployeePosition.employee_id,
                EmployeePosition.position_id,
                EmployeePosition.is_primary,
            ).where(EmployeePosition.employee_id.in_(roster_ids))
        ):
            positions[r.employee_id].append((r.position_id, r.is_primary))

    out_shifts = [
        shift_out(s, employees.get(s.employee_id) if s.employee_id else None) for s in shifts
    ]
    now = _now()
    today = business_date(now, loc.timezone, loc.business_day_start)
    fresh = [f for f in await location_freshness(db, loc.id, now) if f.stream == "orders"]
    connected = any(f.status != "not_connected" for f in fresh)
    dates = week_dates(week_start)
    forecasts = await forecast.forecast_days(
        db, scope.org_id, loc.id, dates, today, connected=connected
    )

    days: list[ScheduleDay] = []
    for d in dates:
        todays = [x for x in out_shifts if x.business_date == d]
        cost = sum(x.cost for x in todays if x.cost is not None)
        f = forecasts[d]
        days.append(
            ScheduleDay(
                business_date=d,
                scheduled_hours=round(sum(x.paid_hours for x in todays), 2),
                projected_cost=cost,
                unrated_shifts=sum(1 for x in todays if x.cost is None),
                forecast_sales=f.sales,
                forecast_basis=f.basis,
                forecast_reason=f.reason,
                projected_labor_pct=_pct(cost, f.sales),
            )
        )
    week_cost = sum(d.projected_cost for d in days)
    # The week has a forecast only when every day does (a partial sum would understate it).
    week_sales = (
        sum(d.forecast_sales or 0 for d in days)
        if all(d.forecast_sales is not None for d in days)
        else None
    )
    ref_first, ref_last = today - timedelta(days=28), today - timedelta(days=1)
    reference = await commerce.summary(
        db, scope.org_id, loc, ref_first, ref_last, "previous_period"
    )
    week = ScheduleWeek(
        scheduled_hours=round(sum(d.scheduled_hours for d in days), 2),
        projected_cost=week_cost,
        unrated_shifts=sum(d.unrated_shifts for d in days),
        forecast_sales=week_sales,
        projected_labor_pct=_pct(week_cost, week_sales),
        reference_labor_pct=reference.current.labor_pct if connected else None,
        reference_period={"from": ref_first, "to": ref_last},
    )
    conflicts = await conflicts_for(db, ConflictContext(loc, zone, shifts, employees, assigned))
    changes = count_changes(shifts, published)
    return ScheduleOut(
        location_id=loc.id,
        timezone=loc.timezone,
        schedule=ScheduleMeta(
            id=sched.id if sched else None,
            week_start=week_start,
            version=sched.version if sched else 0,
            published_version=sched.published_version if sched else None,
            published_at=sched.published_at if sched else None,
            has_unpublished_changes=changes > 0,
            unpublished_changes=changes,
        ),
        shifts=out_shifts,
        employees=sorted(
            (
                ScheduleEmployee(
                    id=e.id,
                    display_name=e.display_name,
                    active=e.active and e.merged_into_id is None,
                    primary_position_id=next(
                        (p for p, primary in positions[e.id] if primary), None
                    ),
                    position_ids=[p for p, _ in positions[e.id]],
                    has_rate=effective_rate(e) is not None,
                    assigned=e.id in assigned,
                )
                for e in employees.values()
                if e.id in roster_ids
            ),
            key=lambda e: (e.display_name.lower(), e.id),
        ),
        days=days,
        week=week,
        conflicts=conflicts,
        freshness=[
            FreshnessOut(
                stream="orders", location_id=f.location_id, data_as_of=f.data_as_of, status=f.status
            )
            for f in fresh
        ],
    )


# ---------------------------------------------------------------- write


@dataclass
class Resolved:
    starts_at: datetime
    ends_at: datetime
    break_minutes: int
    business_date: date


async def _check_refs(
    db: AsyncSession,
    scope: Scope,
    location_id: uuid.UUID,
    *,
    employee_id: uuid.UUID | None,
    position_id: uuid.UUID,
    station_id: uuid.UUID | None,
    allow_archived_position: uuid.UUID | None = None,
) -> StaffMember | None:
    pos = await db.scalar(
        select(Position).where(Position.id == position_id, Position.org_id == scope.org_id)
    )
    if pos is None or (pos.archived_at is not None and pos.id != allow_archived_position):
        raise validation_error({"position_id": "Unknown position"})
    if station_id is not None:
        st = await db.get(Station, station_id)
        if st is None or st.location_id != location_id or st.archived_at is not None:
            raise validation_error({"station_id": "Unknown station for this location"})
    if employee_id is None:
        return None
    # Lock the employee: overlap checks for one person are serialized.
    emp = await db.scalar(
        select(StaffMember)
        .where(StaffMember.id == employee_id, StaffMember.org_id == scope.org_id)
        .with_for_update()
    )
    if emp is None:
        raise validation_error({"employee_id": "Unknown employee"})
    if not emp.active or emp.merged_into_id is not None:
        raise validation_error({"employee_id": "This employee is inactive"})
    return emp


def _resolve_times(
    loc: Location, week_start: date, starts_at: datetime, ends_at: datetime, break_minutes: int
) -> Resolved:
    check_times(starts_at, ends_at, break_minutes)
    bd = business_date(starts_at, loc.timezone, loc.business_day_start)
    if not week_start <= bd <= week_start + timedelta(days=6):
        raise validation_error(
            {"starts_at": f"Must fall in the week of {week_start.isoformat()} (business date {bd})"}
        )
    return Resolved(starts_at.astimezone(UTC), ends_at.astimezone(UTC), break_minutes, bd)


async def _check_overlap(
    db: AsyncSession,
    employee: StaffMember,
    starts_at: datetime,
    ends_at: datetime,
    exclude: uuid.UUID | None,
) -> None:
    stmt = (
        select(Shift, Location)
        .join(Location, Location.id == Shift.location_id)
        .where(
            Shift.employee_id == employee.id, Shift.starts_at < ends_at, Shift.ends_at > starts_at
        )
    )
    if exclude is not None:
        stmt = stmt.where(Shift.id != exclude)
    row = (await db.execute(stmt.limit(1))).first()
    if row is not None:
        other, loc = row
        zone = ZoneInfo(loc.timezone)
        raise ProblemError(
            409,
            "shift_overlap",
            f"{employee.display_name} already works "
            f"{label_shift(other.starts_at, other.ends_at, zone)} at {loc.name} "
            f"(shift {other.id}).",
            title="Shift overlaps another shift",
        )


def _audit_view(s: Shift) -> dict[str, Any]:
    return {
        "employee_id": str(s.employee_id) if s.employee_id else None,
        "position_id": str(s.position_id),
        "station_id": str(s.station_id) if s.station_id else None,
        "business_date": s.business_date.isoformat(),
        "starts_at": s.starts_at.isoformat(),
        "ends_at": s.ends_at.isoformat(),
        "break_minutes": s.break_minutes,
        "notes": s.notes,
    }


async def create_shift(
    db: AsyncSession, scope: Scope, location_id: uuid.UUID, week_start: date, data: ShiftIn
) -> ShiftOut:
    check_week_start(week_start)
    loc = await get_location(db, scope, location_id)
    times = _resolve_times(loc, week_start, data.starts_at, data.ends_at, data.break_minutes)
    sched = await lock_schedule(db, scope.org_id, loc.id, week_start)
    emp = await _check_refs(
        db,
        scope,
        loc.id,
        employee_id=data.employee_id,
        position_id=data.position_id,
        station_id=data.station_id,
    )
    if emp is not None:
        await _check_overlap(db, emp, times.starts_at, times.ends_at, None)
    shift = Shift(
        org_id=scope.org_id,
        location_id=loc.id,
        schedule_id=sched.id,
        employee_id=data.employee_id,
        position_id=data.position_id,
        station_id=data.station_id,
        business_date=times.business_date,
        starts_at=times.starts_at,
        ends_at=times.ends_at,
        break_minutes=times.break_minutes,
        notes=data.notes.strip(),
        created_by=scope.user_id,
    )
    db.add(shift)
    await bump(sched)
    await db.flush()
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="shift",
        entity_id=shift.id,
        action="shift.created",
        location_id=loc.id,
        details={"after": _audit_view(shift)},
    )
    return shift_out(shift, emp)


async def _get_shift(db: AsyncSession, scope: Scope, shift_id: uuid.UUID) -> Shift:
    shift = await db.scalar(select(Shift).where(Shift.id == shift_id, Shift.org_id == scope.org_id))
    if shift is None or shift.location_id not in scope.location_ids:
        raise not_found("Shift not found")
    return shift


async def update_shift(
    db: AsyncSession, scope: Scope, shift_id: uuid.UUID, data: ShiftPatch
) -> ShiftOut:
    shift = await _get_shift(db, scope, shift_id)
    sched = await db.scalar(
        select(Schedule).where(Schedule.id == shift.schedule_id).with_for_update()
    )
    assert sched is not None
    await db.refresh(shift)
    if shift.updated_at != data.expected_updated_at:
        raise ProblemError(
            409, "conflict", "This shift changed since you loaded it. Refresh and try again."
        )
    loc = await get_location(db, scope, shift.location_id)
    fields = data.model_fields_set - {"expected_updated_at"}
    before = _audit_view(shift)
    new = {f: getattr(data, f) if f in fields else getattr(shift, f) for f in (
        "employee_id", "position_id", "station_id", "starts_at", "ends_at", "break_minutes", "notes"
    )}  # fmt: skip
    if new["position_id"] is None:
        raise validation_error({"position_id": "Must not be null"})
    for f in ("starts_at", "ends_at", "break_minutes", "notes"):
        if new[f] is None:
            raise validation_error({f: "Must not be null"})
    times = _resolve_times(
        loc, sched.week_start, new["starts_at"], new["ends_at"], new["break_minutes"]
    )
    emp = await _check_refs(
        db,
        scope,
        loc.id,
        employee_id=new["employee_id"],
        position_id=new["position_id"],
        station_id=new["station_id"],
        allow_archived_position=shift.position_id,
    )
    if emp is not None:
        await _check_overlap(db, emp, times.starts_at, times.ends_at, shift.id)
    shift.employee_id = new["employee_id"]
    shift.position_id = new["position_id"]
    shift.station_id = new["station_id"]
    shift.starts_at, shift.ends_at = times.starts_at, times.ends_at
    shift.break_minutes = times.break_minutes
    shift.business_date = times.business_date
    shift.notes = str(new["notes"]).strip()
    after = _audit_view(shift)
    if after != before:
        shift.updated_at = _now()
        await bump(sched)
        await audit.record(
            db,
            org_id=scope.org_id,
            actor_user_id=scope.user_id,
            entity_type="shift",
            entity_id=shift.id,
            action="shift.updated",
            location_id=loc.id,
            details={
                "before": {k: v for k, v in before.items() if after[k] != v},
                "after": {k: v for k, v in after.items() if before[k] != v},
            },
        )
    await db.flush()
    return shift_out(shift, emp)


async def delete_shift(db: AsyncSession, scope: Scope, shift_id: uuid.UUID) -> None:
    shift = await _get_shift(db, scope, shift_id)
    sched = await db.scalar(
        select(Schedule).where(Schedule.id == shift.schedule_id).with_for_update()
    )
    assert sched is not None
    before = _audit_view(shift)
    await db.execute(delete(Shift).where(Shift.id == shift.id))
    await bump(sched)
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="shift",
        entity_id=shift_id,
        action="shift.deleted",
        location_id=shift.location_id,
        details={"before": before},
    )


async def copy_week(
    db: AsyncSession, scope: Scope, location_id: uuid.UUID, week_start: date, from_week: date
) -> CopyOut:
    """Copy another week's draft into an empty week, in local wall time. Inactive employees,
    and employees who already work elsewhere at the new time, become unassigned."""
    check_week_start(week_start)
    check_week_start(from_week)
    if from_week == week_start:
        raise validation_error({"from_week_start": "Choose a different week"})
    loc = await get_location(db, scope, location_id)
    zone = ZoneInfo(loc.timezone)
    target = await lock_schedule(db, scope.org_id, loc.id, week_start)
    existing = await db.scalar(
        select(func.count()).select_from(Shift).where(Shift.schedule_id == target.id)
    )
    if existing:
        raise ProblemError(409, "conflict", "The target week already has shifts")
    source = await get_schedule(db, scope.org_id, loc.id, from_week)
    src = (
        list(await db.scalars(select(Shift).where(Shift.schedule_id == source.id)))
        if source
        else []
    )
    if not src:
        raise validation_error({"from_week_start": "That week has no shifts to copy"})
    weeks = (week_start - from_week).days // 7
    emps = await _rates(db, {s.employee_id for s in src if s.employee_id is not None})
    stations = {
        s.id
        for s in await db.scalars(
            select(Station).where(Station.location_id == loc.id, Station.archived_at.is_(None))
        )
    }
    copied = unassigned = 0
    for s in sorted(src, key=lambda x: (x.starts_at, x.id)):
        starts = shift_by_weeks(s.starts_at, weeks, zone)
        ends = shift_by_weeks(s.ends_at, weeks, zone)
        employee_id = s.employee_id
        emp = emps.get(employee_id) if employee_id else None
        if emp is not None and (not emp.active or emp.merged_into_id is not None):
            employee_id = None
        if employee_id is not None:
            clash = await db.scalar(
                select(Shift.id).where(
                    Shift.employee_id == employee_id, Shift.starts_at < ends, Shift.ends_at > starts
                )
            )
            if clash is not None:
                employee_id = None
        if s.employee_id is not None and employee_id is None:
            unassigned += 1
        db.add(
            Shift(
                org_id=scope.org_id,
                location_id=loc.id,
                schedule_id=target.id,
                employee_id=employee_id,
                position_id=s.position_id,
                station_id=s.station_id if s.station_id in stations else None,
                business_date=business_date(starts, loc.timezone, loc.business_day_start),
                starts_at=starts,
                ends_at=ends,
                break_minutes=s.break_minutes,
                notes=s.notes,
                created_by=scope.user_id,
            )
        )
        await db.flush()  # later rows' overlap checks see this one
        copied += 1
    await bump(target)
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="schedule",
        entity_id=target.id,
        action="schedule.copied",
        location_id=loc.id,
        details={
            "from_week_start": from_week.isoformat(),
            "week_start": week_start.isoformat(),
            "copied": copied,
            "unassigned": unassigned,
        },
    )
    return CopyOut(copied=copied, unassigned=unassigned)


# ---------------------------------------------------------------- publish


@dataclass(frozen=True)
class EmployeeDiff:
    added: list[Shift | PublishedShift]
    removed: list[Shift | PublishedShift]
    moved: list[tuple[Shift | PublishedShift, Shift | PublishedShift]]  # (old, new), same id


def diff_by_employee(
    old: Sequence[Shift | PublishedShift],
    new: Sequence[Shift | PublishedShift],
    now: datetime,
) -> dict[uuid.UUID, EmployeeDiff]:
    """Per employee, what changed between two snapshots. Shifts that ended before `now` are
    ignored (no alert about the past). A reassigned shift is removed for one person and added
    for the other."""

    def live(xs: Sequence[Shift | PublishedShift]) -> dict[uuid.UUID, Shift | PublishedShift]:
        return {s.id: s for s in xs if s.employee_id is not None and s.ends_at > now}

    a, b = live(old), live(new)
    out: dict[uuid.UUID, EmployeeDiff] = {}

    def entry(emp: uuid.UUID | None) -> EmployeeDiff:
        assert emp is not None
        return out.setdefault(emp, EmployeeDiff([], [], []))

    for sid in a.keys() | b.keys():
        o, n = a.get(sid), b.get(sid)
        if o is not None and n is not None and o.employee_id == n.employee_id:
            if snapshot_key(o) != snapshot_key(n):
                entry(o.employee_id).moved.append((o, n))
            continue
        if o is not None:
            entry(o.employee_id).removed.append(o)
        if n is not None:
            entry(n.employee_id).added.append(n)
    return out


def describe(d: EmployeeDiff, zone: ZoneInfo) -> str:
    parts: list[str] = []
    for s in sorted(d.added, key=lambda x: x.starts_at):
        parts.append(f"{label_shift(s.starts_at, s.ends_at, zone)} added")
    for o, n in sorted(d.moved, key=lambda x: x[1].starts_at):
        if (o.starts_at, o.ends_at) != (n.starts_at, n.ends_at):
            day = n.starts_at.astimezone(zone).strftime("%a")
            parts.append(
                f"{day} moved to {label_time(n.starts_at, zone)}-{label_time(n.ends_at, zone)}"
            )
        else:
            parts.append(f"{label_shift(n.starts_at, n.ends_at, zone)} updated")
    for s in sorted(d.removed, key=lambda x: x.starts_at):
        parts.append(f"{label_shift(s.starts_at, s.ends_at, zone)} removed")
    return ", ".join(parts)


def _week_label(week_start: date) -> str:
    return f"{week_start.strftime('%b')} {week_start.day}"


async def publish(
    db: AsyncSession,
    scope: Scope,
    location_id: uuid.UUID,
    week_start: date,
    *,
    expected_version: int,
    acknowledge_conflicts: bool,
) -> PublishOut:
    """Snapshot the draft for employees in one transaction and alert only who changed."""
    check_week_start(week_start)
    loc = await get_location(db, scope, location_id)
    zone = ZoneInfo(loc.timezone)
    sched = await lock_schedule(db, scope.org_id, loc.id, week_start)
    if sched.version != expected_version:
        raise ProblemError(
            409,
            "schedule_changed",
            "Someone changed this schedule since you loaded it. Review the latest version.",
            title="Schedule changed",
        )
    shifts = list(await db.scalars(select(Shift).where(Shift.schedule_id == sched.id)))
    old = list(
        await db.scalars(select(PublishedShift).where(PublishedShift.schedule_id == sched.id))
    )
    first = sched.published_version is None
    if not first and count_changes(shifts, old) == 0:
        return PublishOut(
            published_version=sched.published_version,
            changed=False,
            changed_employees=[],
            notified_users=0,
            employees_without_login=[],
        )

    employees = await _rates(db, {s.employee_id for s in shifts if s.employee_id is not None})
    conflicts = await conflicts_for(
        db, ConflictContext(loc, zone, shifts, employees, await _assigned(db, loc.id))
    )
    if any(c.kind == "overlap" for c in conflicts):
        raise ProblemError(
            409, "shift_overlap", "Resolve overlapping shifts before publishing.",
            title="Shift overlaps another shift",
        )  # fmt: skip
    if conflicts and not acknowledge_conflicts:
        raise ProblemError(
            409,
            "conflicts_unacknowledged",
            f"{len(conflicts)} shift conflict(s). Publish with acknowledge_conflicts to proceed.",
            title="Schedule has conflicts",
        )

    now = _now()
    version = (sched.published_version or 0) + 1
    old_snapshot = [
        PublishedShift(
            **{column: getattr(row, column) for column in PUBLISHED_COLUMNS},
            published_version=row.published_version,
        )
        for row in old
    ]
    old_by_id = {s.id: s for s in old}
    new_ids = {s.id for s in shifts}
    # Keep stable published rows in place. Coverage/trade requests reference them, and deleting
    # every row on every publish would cascade away requests even when the shift still exists.
    for prior in old:
        if prior.id not in new_ids:
            for hook in PUBLISH_REMOVE_HOOKS:
                await hook(db, scope, prior)
            await db.delete(prior)
    for draft in shifts:
        existing_published = old_by_id.get(draft.id)
        if existing_published is None:
            db.add(
                PublishedShift(
                    **{c: getattr(draft, c) for c in PUBLISHED_COLUMNS},
                    published_version=version,
                )
            )
            continue
        for column in PUBLISHED_COLUMNS:
            setattr(existing_published, column, getattr(draft, column))
        existing_published.published_version = version
    await db.flush()
    # daily_labor.scheduled_hours follows the published snapshot.
    await refresh_scheduled_hours(db, scope.org_id, [(loc.id, d) for d in week_dates(week_start)])
    diffs = diff_by_employee(old_snapshot, shifts, now)
    people = await _rates(db, set(diffs))
    notified = 0
    kind = "schedule_published" if first else "schedule_changed"
    link = f"/my?week={week_start.isoformat()}"
    for emp_id, d in sorted(diffs.items(), key=lambda x: str(x[0])):
        emp = people.get(emp_id)
        if emp is None or emp.user_id is None:
            continue
        if first:
            title = f"Your schedule for the week of {_week_label(week_start)} is out"
            body = ", ".join(
                label_shift(s.starts_at, s.ends_at, zone)
                for s in sorted(d.added, key=lambda x: x.starts_at)
            )
        else:
            title = f"Your week of {_week_label(week_start)} changed"
            body = describe(d, zone)
        notified += await notify.notify(
            db,
            scope.org_id,
            [emp.user_id],
            kind=kind,
            title=title,
            body=f"{body} at {loc.name}." if body else f"At {loc.name}.",
            link=link,
            entity_type="schedule",
            entity_id=sched.id,
        )
    sched.published_version = version
    sched.published_at = now
    sched.published_by = scope.user_id
    without_login = sorted(
        (e for e in employees.values() if e.user_id is None),
        key=lambda e: e.display_name.lower(),
    )
    changed_ids = sorted(diffs, key=str)
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="schedule",
        entity_id=sched.id,
        action="schedule.published",
        location_id=loc.id,
        details={
            "week_start": week_start.isoformat(),
            "version": version,
            "shift_count": len(shifts),
            "changed_employee_ids": [str(i) for i in changed_ids],
            "acknowledged_conflicts": len(conflicts),
        },
    )
    return PublishOut(
        published_version=version,
        changed=True,
        changed_employees=changed_ids,
        notified_users=notified,
        employees_without_login=[
            EmployeeRef(id=e.id, display_name=e.display_name) for e in without_login
        ],
    )
