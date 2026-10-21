"""Clock-in attendance (TEAM-6): published shifts against imported timecards.

A shift with no clock-in `GRACE` after its start is *late*; one with none by its end is
*missed*. Statuses are judged as of the instant timecards are imported through (the location's
`timecards` stream `data_as_of`, capped at now), never wall-clock time alone, so a sync lag or an
outage cannot read as a no-show. A location with no timecard source is never judged.

Detection runs after every ConnectionSync pass (app.workflows.team): that pass is both the
schedule that lets time move a shift from upcoming to late and the import that can clear it.
Alerts go to the location's managers through `notifications`, one per recipient, shift and kind
(`uq_notifications_attendance`), so re-running the detector is harmless.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import activity_org_session
from app.errors import validation_error
from app.identity.models import Location
from app.integrations.freshness import FreshnessItem, location_freshness
from app.integrations.models import IntegrationLocationLink
from app.integrations.providers.square.business_date import business_date
from app.team import notify
from app.team.models import Position, PublishedShift, StaffMember, Timecard
from app.team.schedule import label_shift, paid_hours
from app.team.schemas import (
    AttendanceCounts,
    AttendanceDay,
    AttendanceOut,
    AttendanceShift,
    AttendanceStatus,
    FreshnessOut,
    UnscheduledTimecard,
)
from app.team.service import Scope, get_location

GRACE = timedelta(minutes=10)
# A clock-in up to this long before the start can belong to the shift.
EARLY = timedelta(hours=2)
# The detector looks back this far; older shifts are history, not alerts.
LOOKBACK = timedelta(hours=36)
MAX_RANGE_DAYS = 31
ALERT_KINDS = ("shift_late", "shift_missed")


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- pure rules


@dataclass(frozen=True)
class Punch:
    id: uuid.UUID
    staff_member_id: uuid.UUID | None
    business_date: date
    clock_in: datetime
    clock_out: datetime | None
    paid_hours: Decimal


def assign(
    shifts: Sequence[PublishedShift], punches: Sequence[Punch]
) -> dict[uuid.UUID, list[Punch]]:
    """shift id -> its timecards. A timecard belongs to at most one shift: the same employee's
    shift whose window [start - EARLY, end) holds the clock-in, nearest start first."""
    by_emp: dict[uuid.UUID, list[PublishedShift]] = defaultdict(list)
    for s in shifts:
        if s.employee_id is not None:
            by_emp[s.employee_id].append(s)
    out: dict[uuid.UUID, list[Punch]] = defaultdict(list)
    for p in punches:
        if p.staff_member_id is None:
            continue
        candidates = [
            s
            for s in by_emp.get(p.staff_member_id, [])
            if s.starts_at - EARLY <= p.clock_in < s.ends_at
        ]
        if candidates:
            best = min(candidates, key=lambda s: (abs(p.clock_in - s.starts_at), s.starts_at))
            out[best.id].append(p)
    return out


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def judge(
    starts_at: datetime,
    ends_at: datetime,
    clock_in: datetime | None,
    now: datetime,
    as_of: datetime | None,
) -> tuple[AttendanceStatus, int | None]:
    """(status, minutes late). `as_of` is how far timecards are known; None means no source."""
    due = starts_at + GRACE
    if clock_in is not None:
        if clock_in <= due:
            return "on_time", None
        return "late", _minutes(clock_in - starts_at)
    if now < due:
        return "upcoming", None
    if as_of is None or as_of < due:
        return "awaiting_data", None
    if as_of >= ends_at:
        return "missed", None
    return "late", _minutes(as_of - starts_at)


# ---------------------------------------------------------------- loading


async def timecards_as_of(
    db: AsyncSession, location_id: uuid.UUID, now: datetime
) -> tuple[datetime | None, FreshnessItem]:
    item = next(
        f for f in await location_freshness(db, location_id, now) if f.stream == "timecards"
    )
    if item.status == "not_connected" or item.data_as_of is None:
        return None, item
    return min(now, item.data_as_of), item


async def _punches(
    db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID, first: date, last: date
) -> list[Punch]:
    rows = await db.execute(
        select(
            Timecard.id,
            Timecard.staff_member_id,
            Timecard.business_date,
            Timecard.clock_in,
            Timecard.clock_out,
            Timecard.paid_hours,
        )
        .where(
            Timecard.org_id == org_id,
            Timecard.location_id == location_id,
            Timecard.business_date.between(first, last),
        )
        .order_by(Timecard.clock_in, Timecard.id)
    )
    return [Punch(*r) for r in rows]


async def _published(
    db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID, first: date, last: date
) -> list[PublishedShift]:
    return list(
        await db.scalars(
            select(PublishedShift)
            .where(
                PublishedShift.org_id == org_id,
                PublishedShift.location_id == location_id,
                PublishedShift.business_date.between(first, last),
            )
            .order_by(PublishedShift.starts_at, PublishedShift.id)
        )
    )


async def _names(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await db.execute(
        select(StaffMember.id, StaffMember.display_name).where(StaffMember.id.in_(ids))
    )
    return {r.id: r.display_name for r in rows}


async def _positions(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, Position]:
    if not ids:
        return {}
    return {p.id: p for p in await db.scalars(select(Position).where(Position.id.in_(ids)))}


def _hours(punches: list[Punch]) -> float:
    return round(float(sum((p.paid_hours for p in punches), Decimal(0))), 2)


# ---------------------------------------------------------------- the attendance view


async def attendance(
    db: AsyncSession,
    scope: Scope,
    location_id: uuid.UUID,
    first: date | None,
    last: date | None,
    now: datetime | None = None,
) -> AttendanceOut:
    """Scheduled vs actual per published shift and business day. Open (unassigned) shifts count
    toward scheduled hours but have nobody to judge."""
    now = now or _now()
    loc = await get_location(db, scope, location_id)
    today = business_date(now, loc.timezone, loc.business_day_start)
    first = first or today
    last = last or first
    if first > last:
        raise validation_error({"from": "Must be on or before `to`"})
    if (last - first).days + 1 > MAX_RANGE_DAYS:
        raise validation_error({"to": f"Range is limited to {MAX_RANGE_DAYS} days"})

    as_of, fresh = await timecards_as_of(db, loc.id, now)
    # One day either side, so overnight and early clock-ins find their shift (the day before's
    # overnight shift claims its timecards, though only shifts in range are shown).
    context = await _published(db, scope.org_id, loc.id, first - timedelta(days=1), last)
    shifts = [s for s in context if s.business_date >= first]
    punches = await _punches(
        db, scope.org_id, loc.id, first - timedelta(days=1), last + timedelta(days=1)
    )
    assigned = assign(context, punches)
    used = {p.id for ps in assigned.values() for p in ps}
    names = await _names(
        db,
        {s.employee_id for s in shifts if s.employee_id is not None}
        | {p.staff_member_id for p in punches if p.staff_member_id is not None},
    )
    positions = await _positions(db, {s.position_id for s in shifts})

    by_day: dict[date, list[AttendanceShift]] = defaultdict(list)
    open_shifts = 0
    for s in shifts:
        if s.employee_id is None:
            open_shifts += 1
            continue
        ps = assigned.get(s.id, [])
        clock_in = ps[0].clock_in if ps else None
        clock_out = None if not ps or any(p.clock_out is None for p in ps) else ps[-1].clock_out
        status, late = judge(s.starts_at, s.ends_at, clock_in, now, as_of)
        pos = positions[s.position_id]
        by_day[s.business_date].append(
            AttendanceShift(
                shift_id=s.id,
                employee_id=s.employee_id,
                employee_name=names.get(s.employee_id),
                position_id=pos.id,
                position_name=pos.name,
                position_color=pos.color,
                starts_at=s.starts_at,
                ends_at=s.ends_at,
                scheduled_hours=round(
                    float(paid_hours(s.starts_at, s.ends_at, s.break_minutes)), 2
                ),
                clock_in=clock_in,
                clock_out=clock_out,
                actual_hours=_hours(ps),
                status=status,
                minutes_late=late,
            )
        )

    days: list[AttendanceDay] = []
    d = first
    while d <= last:
        todays = [s for s in shifts if s.business_date == d]
        day_punches = [p for p in punches if p.business_date == d]
        rows = by_day.get(d, [])
        counts = AttendanceCounts()
        for r in rows:
            setattr(counts, r.status, getattr(counts, r.status) + 1)
        days.append(
            AttendanceDay(
                business_date=d,
                scheduled_hours=round(
                    float(
                        sum(
                            (paid_hours(s.starts_at, s.ends_at, s.break_minutes) for s in todays),
                            Decimal(0),
                        )
                    ),
                    2,
                ),
                actual_hours=_hours(day_punches),
                counts=counts,
                shifts=rows,
                unscheduled=[
                    UnscheduledTimecard(
                        timecard_id=p.id,
                        employee_id=p.staff_member_id,
                        employee_name=names.get(p.staff_member_id) if p.staff_member_id else None,
                        clock_in=p.clock_in,
                        clock_out=p.clock_out,
                        actual_hours=_hours([p]),
                    )
                    for p in day_punches
                    if p.id not in used
                ],
            )
        )
        d += timedelta(days=1)

    return AttendanceOut(
        location_id=loc.id,
        timezone=loc.timezone,
        period={"from": first, "to": last},
        grace_minutes=int(GRACE.total_seconds() // 60),
        as_of=as_of,
        open_shifts_excluded=open_shifts,
        days=days,
        freshness=[
            FreshnessOut(
                stream="timecards",
                location_id=fresh.location_id,
                data_as_of=fresh.data_as_of,
                status=fresh.status,
            )
        ],
    )


# ---------------------------------------------------------------- detection and alerts


def _alert_text(
    kind: str, name: str, clocked_in: bool, position: str, label: str, loc: Location
) -> tuple[str, str]:
    where = f"{position}, {label} at {loc.name}."
    grace = int(GRACE.total_seconds() // 60)
    if kind == "shift_missed":
        return f"{name} missed a shift", f"{where} No clock-in by the end of the shift."
    if clocked_in:
        return f"{name} clocked in late", f"{where} Clocked in more than {grace} minutes late."
    return f"{name} hasn't clocked in", f"{where} No clock-in {grace} minutes after the start."


async def detect(db: AsyncSession, org_id: uuid.UUID, loc: Location, now: datetime) -> int:
    """Alert the location's managers about late and missed shifts that started within
    LOOKBACK. Returns how many notifications were written (repeats write nothing)."""
    as_of, _ = await timecards_as_of(db, loc.id, now)
    if as_of is None:
        return 0
    first = business_date(as_of - LOOKBACK, loc.timezone, loc.business_day_start)
    last = business_date(as_of, loc.timezone, loc.business_day_start)
    shifts = [
        s
        for s in await _published(db, org_id, loc.id, first, last)
        if s.employee_id is not None and as_of - LOOKBACK <= s.starts_at <= as_of - GRACE
    ]
    if not shifts:
        return 0
    punches = await _punches(
        db, org_id, loc.id, first - timedelta(days=1), last + timedelta(days=1)
    )
    assigned = assign(shifts, punches)
    flagged: list[tuple[PublishedShift, str, bool]] = []
    for s in shifts:
        ps = assigned.get(s.id, [])
        clock_in = ps[0].clock_in if ps else None
        status, _late = judge(s.starts_at, s.ends_at, clock_in, now, as_of)
        if status == "late":
            flagged.append((s, "shift_late", clock_in is not None))
        elif status == "missed":
            flagged.append((s, "shift_missed", False))
    if not flagged:
        return 0
    recipients = await notify.managers_for(db, org_id, [loc.id])
    names = await _names(db, {s.employee_id for s, _, _ in flagged if s.employee_id})
    positions = await _positions(db, {s.position_id for s, _, _ in flagged})
    zone = ZoneInfo(loc.timezone)
    written = 0
    for s, kind, clocked_in in flagged:
        title, body = _alert_text(
            kind,
            names.get(s.employee_id, "An employee") if s.employee_id else "An employee",
            clocked_in,
            positions[s.position_id].name,
            label_shift(s.starts_at, s.ends_at, zone),
            loc,
        )
        written += await notify.notify_once(
            db,
            org_id,
            recipients,
            kind=kind,
            title=title,
            body=body,
            link=f"/app/team/attendance?loc={loc.id}&date={s.business_date.isoformat()}",
            entity_type="shift",
            entity_id=s.id,
        )
    return written


async def check_connection(
    org_id: uuid.UUID, connection_id: uuid.UUID, now: datetime | None = None
) -> int:
    """Run the detector for every location this connection imports timecards for."""
    now = now or _now()
    async with activity_org_session(org_id) as db:
        locations = list(
            await db.scalars(
                select(Location)
                .join(IntegrationLocationLink, IntegrationLocationLink.location_id == Location.id)
                .where(
                    IntegrationLocationLink.connection_id == connection_id,
                    IntegrationLocationLink.active.is_(True),
                    Location.archived_at.is_(None),
                )
            )
        )
        written = 0
        for loc in locations:
            written += await detect(db, org_id, loc, now)
    return written
