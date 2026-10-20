"""Employee self-service (/v1/me/*). The employee is always resolved from the session's user,
never from a request parameter, and responses never carry rates or costs."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import not_found
from app.identity.models import Location, Organization
from app.integrations.providers.square.business_date import business_date
from app.team.models import (
    EmployeeLocation,
    Position,
    PublishedShift,
    Schedule,
    StaffMember,
    Station,
)
from app.team.schedule import check_week_start
from app.team.schemas import EmployeeRef, MyLocation, MyShift, MyWeek, TeamShift


async def my_employee(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> StaffMember:
    emp = await db.scalar(
        select(StaffMember).where(
            StaffMember.org_id == org_id,
            StaffMember.user_id == user_id,
            StaffMember.merged_into_id.is_(None),
        )
    )
    if emp is None:
        raise not_found("No employee profile is linked to your login")
    return emp


async def my_location_ids(db: AsyncSession, employee_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        await db.scalars(
            select(EmployeeLocation.location_id).where(EmployeeLocation.employee_id == employee_id)
        )
    )


async def local_today(db: AsyncSession, org_id: uuid.UUID, location_ids: set[uuid.UUID]) -> date:
    """Today's business date at the employee's first location (else in the org's zone)."""
    loc = (
        await db.scalar(
            select(Location).where(Location.id.in_(location_ids)).order_by(Location.created_at)
        )
        if location_ids
        else None
    )
    if loc is not None:
        return business_date(datetime.now(UTC), loc.timezone, loc.business_day_start)
    org = await db.get_one(Organization, org_id)
    return business_date(datetime.now(UTC), org.timezone)


async def current_week(db: AsyncSession, org_id: uuid.UUID, location_ids: set[uuid.UUID]) -> date:
    """Monday of today's business week at the employee's location."""
    today = await local_today(db, org_id, location_ids)
    return today - timedelta(days=today.weekday())


async def my_week(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, week_start: date | None
) -> MyWeek:
    emp = await my_employee(db, org_id, user_id)
    assigned = await my_location_ids(db, emp.id)
    if week_start is None:
        week_start = await current_week(db, org_id, assigned)
    check_week_start(week_start)
    # Published weeks at my locations, plus any location where I have a published shift.
    mine_elsewhere = set(
        await db.scalars(
            select(Schedule.location_id)
            .join(PublishedShift, PublishedShift.schedule_id == Schedule.id)
            .where(Schedule.week_start == week_start, PublishedShift.employee_id == emp.id)
        )
    )
    schedules = list(
        await db.scalars(
            select(Schedule).where(
                Schedule.org_id == org_id,
                Schedule.week_start == week_start,
                Schedule.location_id.in_(assigned | mine_elsewhere),
                Schedule.published_version.is_not(None),
            )
        )
    )
    loc_ids = {s.location_id for s in schedules} | assigned
    locations = (
        list(await db.scalars(select(Location).where(Location.id.in_(loc_ids)))) if loc_ids else []
    )
    rows = (
        (
            await db.execute(
                select(PublishedShift, Position, Station.name, StaffMember.display_name)
                .join(Position, Position.id == PublishedShift.position_id)
                .outerjoin(Station, Station.id == PublishedShift.station_id)
                .outerjoin(StaffMember, StaffMember.id == PublishedShift.employee_id)
                .where(PublishedShift.schedule_id.in_([s.id for s in schedules]))
                .order_by(PublishedShift.starts_at, PublishedShift.id)
            )
        ).all()
        if schedules
        else []
    )
    my_shifts: list[MyShift] = []
    team: list[TeamShift] = []
    for shift, pos, station_name, name in rows:
        if shift.employee_id == emp.id:
            my_shifts.append(
                MyShift(
                    id=shift.id,
                    location_id=shift.location_id,
                    business_date=shift.business_date,
                    starts_at=shift.starts_at,
                    ends_at=shift.ends_at,
                    break_minutes=shift.break_minutes,
                    position_name=pos.name,
                    position_color=pos.color,
                    station_name=station_name,
                    notes=shift.notes,
                )
            )
        elif shift.employee_id is not None and name is not None:
            team.append(
                TeamShift(
                    location_id=shift.location_id,
                    business_date=shift.business_date,
                    starts_at=shift.starts_at,
                    ends_at=shift.ends_at,
                    employee_name=name,
                    position_name=pos.name,
                    position_color=pos.color,
                )
            )
    published = [s.published_at for s in schedules if s.published_at is not None]
    return MyWeek(
        week_start=week_start,
        employee=EmployeeRef(id=emp.id, display_name=emp.display_name),
        published_at=max(published) if published else None,
        locations=[
            MyLocation(id=loc.id, name=loc.name, timezone=loc.timezone)
            for loc in sorted(locations, key=lambda x: x.name)
        ],
        my_shifts=my_shifts,
        team_shifts=team,
    )
