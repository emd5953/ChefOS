"""Demo seeding for Team (`python -m app.cli demo`): logins through the real invitation path,
a published current week, and today's clock-in exceptions, all through the real services (audit
included).

Idempotent: anything that already exists is left alone.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import scoped_session
from app.errors import ProblemError
from app.identity.models import Location, User
from app.integrations.models import ExternalRef, IntegrationLocationLink
from app.integrations.providers.square import fake_data as fd
from app.integrations.providers.square.business_date import business_date
from app.team import attendance, invitations, schedule
from app.team.models import Position, Shift, StaffMember
from app.team.schemas import InvitationCreate, ShiftIn
from app.team.service import Scope

GM_EMAIL = "gm@chefos.local"
EMPLOYEE_EMAIL = "maya@chefos.local"
EMPLOYEE_NAME = "Maya Okafor"
ATTENDANCE_NOTE = "Attendance demo"


async def _invite_and_accept(
    owner: Scope, data: InvitationCreate, display_name: str, password: str
) -> bool:
    async with scoped_session(owner.org_id) as db:
        if await db.scalar(select(User.id).where(User.email == data.email)) is not None:
            return False
        _, token = await invitations.create_invitation(db, owner, data)
    await invitations.accept(token, display_name, password, user_agent="cli-demo", ip=None)
    return True


async def seed_logins(org_id: uuid.UUID, owner_user_id: uuid.UUID, password: str) -> list[str]:
    """A manager (gm@) for every location and an employee login (maya@) linked to the imported
    Square team member Maya Okafor. Returns the emails it created."""
    async with scoped_session(org_id) as db:
        locations = list(
            await db.scalars(
                select(Location.id).where(Location.org_id == org_id, Location.archived_at.is_(None))
            )
        )
        maya = await db.scalar(
            select(StaffMember).where(
                StaffMember.display_name == EMPLOYEE_NAME, StaffMember.merged_into_id.is_(None)
            )
        )
        maya_id = maya.id if maya is not None and maya.user_id is None else None
    owner = Scope(org_id, owner_user_id, "owner", frozenset(locations))
    created: list[str] = []
    gm = InvitationCreate(role="manager", email=GM_EMAIL, location_ids=locations)
    if await _invite_and_accept(owner, gm, "Jordan Reyes", password):
        created.append(GM_EMAIL)
    if maya_id is not None:
        emp = InvitationCreate(
            role="employee", employee_id=maya_id, email=EMPLOYEE_EMAIL, location_ids=locations
        )
        try:
            if await _invite_and_accept(owner, emp, EMPLOYEE_NAME, password):
                created.append(EMPLOYEE_EMAIL)
        except ProblemError as exc:  # e.g. the employee isn't assigned to a location yet
            print(f"Skipped the employee login: {exc.detail}")
    return created


async def seed_week(org_id: uuid.UUID, owner_user_id: uuid.UUID) -> int:
    """Publish the current week at the Square-linked location from the simulator's own shift
    pattern (the same shifts its timecards come from). Skips a week that already has shifts.
    Returns the number of shifts created."""
    async with scoped_session(org_id) as db:
        loc = await db.scalar(
            select(Location)
            .join(IntegrationLocationLink, IntegrationLocationLink.location_id == Location.id)
            .where(Location.org_id == org_id, IntegrationLocationLink.active.is_(True))
            .limit(1)
        )
        if loc is None:
            return 0
        today = business_date(datetime.now(UTC), loc.timezone, loc.business_day_start)
        week = today - timedelta(days=today.weekday())
        sched = await schedule.get_schedule(db, org_id, loc.id, week)
        if sched is not None and (
            sched.published_version is not None
            or await db.scalar(select(Shift.id).where(Shift.schedule_id == sched.id).limit(1))
        ):
            return 0
        refs = await resolve_refs_any(db, "staff_member", [s.id for s in fd.STAFF])
        positions = {
            p.name.lower(): p.id
            for p in await db.scalars(select(Position).where(Position.org_id == org_id))
        }
        locations = list(await db.scalars(select(Location.id).where(Location.org_id == org_id)))
        location_id = loc.id
    owner = Scope(org_id, owner_user_id, "owner", frozenset(locations))
    created = 0
    for i in range(7):
        for spec in fd.shifts_for_day(week + timedelta(days=i)):
            position_id = positions.get(spec.role.lower())
            if position_id is None:
                continue
            body = ShiftIn(
                employee_id=refs.get(spec.staff_id),  # the contract server isn't in the directory
                position_id=position_id,
                starts_at=spec.start,
                ends_at=spec.end,
                break_minutes=30 if spec.break_start else 0,
            )
            try:
                async with scoped_session(org_id) as db:
                    await schedule.create_shift(db, owner, location_id, week, body)
                created += 1
            except ProblemError:
                continue  # e.g. a double that overlaps; the demo doesn't need it
    async with scoped_session(org_id) as db:
        version = (await schedule.lock_schedule(db, org_id, location_id, week)).version
    async with scoped_session(org_id) as db:
        await schedule.publish(
            db, owner, location_id, week, expected_version=version, acknowledge_conflicts=True
        )
    return created


async def seed_attendance(org_id: uuid.UUID, owner_user_id: uuid.UUID) -> tuple[int, int]:
    """Publish two shifts today that nobody clocks in for, one that started 25 minutes ago
    (late) and one that ended 20 minutes ago (missed), for team members the simulator has off
    today (its timecards follow its own shift pattern, so they never clock in). Then run the
    detector once so the GM's alerts are there immediately; the worker keeps it current.
    Skips creating shifts when today already has them. Returns (shifts created, alerts written)."""
    now = datetime.now(UTC)
    async with scoped_session(org_id) as db:
        loc = await db.scalar(
            select(Location)
            .join(IntegrationLocationLink, IntegrationLocationLink.location_id == Location.id)
            .where(Location.org_id == org_id, IntegrationLocationLink.active.is_(True))
            .limit(1)
        )
        if loc is None:
            return 0, 0
        today = business_date(now, loc.timezone, loc.business_day_start)
        seeded = await db.scalar(
            select(Shift.id)
            .where(
                Shift.location_id == loc.id,
                Shift.notes == ATTENDANCE_NOTE,
                Shift.business_date >= today - timedelta(days=1),
            )
            .limit(1)
        )
        refs = await resolve_refs_any(db, "staff_member", [s.id for s in fd.STAFF])
        positions = {
            p.name.lower(): p.id
            for p in await db.scalars(select(Position).where(Position.org_id == org_id))
        }
        locations = list(await db.scalars(select(Location.id).where(Location.org_id == org_id)))
    owner = Scope(org_id, owner_user_id, "owner", frozenset(locations))
    created = 0
    if seeded is None:
        working = {s.staff_id for s in fd.shifts_for_day(today)}
        off = [
            s
            for s in fd.STAFF
            if s.id not in working and s.id in refs and s.role.lower() in positions
        ]
        anchor = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
        late_start = anchor - timedelta(minutes=25)
        missed_end = anchor - timedelta(minutes=20)
        plans = [
            (late_start, late_start + timedelta(hours=5)),
            (missed_end - timedelta(hours=4), missed_end),
        ]
        weeks: set[date] = set()
        for starts_at, ends_at in plans:
            bd = business_date(starts_at, loc.timezone, loc.business_day_start)
            week = bd - timedelta(days=bd.weekday())
            while off:
                staff = off.pop(0)
                body = ShiftIn(
                    employee_id=refs[staff.id],
                    position_id=positions[staff.role.lower()],
                    starts_at=starts_at,
                    ends_at=ends_at,
                    notes=ATTENDANCE_NOTE,
                )
                try:
                    async with scoped_session(org_id) as db:
                        await schedule.create_shift(db, owner, loc.id, week, body)
                except ProblemError:
                    continue  # overlaps one of their shifts; try the next person
                created += 1
                weeks.add(week)
                break
        for week in sorted(weeks):
            async with scoped_session(org_id) as db:
                version = (await schedule.lock_schedule(db, org_id, loc.id, week)).version
            async with scoped_session(org_id) as db:
                await schedule.publish(
                    db, owner, loc.id, week, expected_version=version, acknowledge_conflicts=True
                )
    async with scoped_session(org_id) as db:
        alerts = await attendance.detect(db, org_id, loc, now)
    return created, alerts


async def resolve_refs_any(
    db: AsyncSession, entity_type: str, external_ids: list[str]
) -> dict[str, uuid.UUID]:
    """external id -> entity id across the org's connections."""
    rows = await db.execute(
        select(ExternalRef.external_id, ExternalRef.entity_id).where(
            ExternalRef.entity_type == entity_type, ExternalRef.external_id.in_(external_ids)
        )
    )
    return {r.external_id: r.entity_id for r in rows}
