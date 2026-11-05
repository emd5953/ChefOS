"""Open shift coverage: employee requests to pick up open shifts and manager decisions."""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.errors import ProblemError, not_found
from app.identity.models import Location
from app.team import me, notify
from app.team.models import (
    EmployeePosition,
    Position,
    PublishedShift,
    Schedule,
    Shift,
    ShiftCoverageRequest,
    ShiftTradeRequest,
    StaffMember,
    Station,
)
from app.team.requests.availability import _now
from app.team.scheduling import builder as schedule
from app.team.schemas import (
    OpenShift,
    ShiftCoverageRequestOut,
)
from app.team.service import Scope, get_location

# ---------------------------------------------------------------- open shift coverage


def _coverage_out(
    row: ShiftCoverageRequest, employee: StaffMember, shift: PublishedShift, position: Position
) -> ShiftCoverageRequestOut:
    return ShiftCoverageRequestOut(
        id=row.id,
        location_id=row.location_id,
        published_shift_id=row.published_shift_id,
        employee_id=row.employee_id,
        employee_name=employee.display_name,
        business_date=shift.business_date,
        starts_at=shift.starts_at,
        ends_at=shift.ends_at,
        position_name=position.name,
        status=row.status,
        created_at=row.created_at,
        decision_note=row.decision_note,
    )


async def open_shifts(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> list[OpenShift]:
    emp = await me.my_employee(db, org_id, user_id)
    locations = await me.my_location_ids(db, emp.id)
    if not locations:
        return []
    latest_status = (
        select(ShiftCoverageRequest.status)
        .where(
            ShiftCoverageRequest.published_shift_id == PublishedShift.id,
            ShiftCoverageRequest.employee_id == emp.id,
        )
        .order_by(ShiftCoverageRequest.created_at.desc(), ShiftCoverageRequest.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    latest_id = (
        select(ShiftCoverageRequest.id)
        .where(
            ShiftCoverageRequest.published_shift_id == PublishedShift.id,
            ShiftCoverageRequest.employee_id == emp.id,
        )
        .order_by(ShiftCoverageRequest.created_at.desc(), ShiftCoverageRequest.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(
                PublishedShift,
                Position,
                Station.name,
                latest_status,
                latest_id,
                Location,
            )
            .join(Position, Position.id == PublishedShift.position_id)
            .join(Location, Location.id == PublishedShift.location_id)
            .outerjoin(Station, Station.id == PublishedShift.station_id)
            .where(
                PublishedShift.org_id == org_id,
                PublishedShift.location_id.in_(locations),
                PublishedShift.employee_id.is_(None),
                PublishedShift.starts_at > schedule._now(),
            )
            .order_by(PublishedShift.starts_at)
            .limit(100)
        )
    ).all()
    return [
        OpenShift(
            id=s.id,
            location_id=s.location_id,
            location_name=location.name,
            timezone=location.timezone,
            business_date=s.business_date,
            starts_at=s.starts_at,
            ends_at=s.ends_at,
            break_minutes=s.break_minutes,
            position_name=p.name,
            position_color=p.color,
            station_name=station,
            request_id=request_id,
            request_status=status,
        )
        for s, p, station, status, request_id, location in rows
    ]


async def request_open_shift(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, shift_id: uuid.UUID
) -> ShiftCoverageRequestOut:
    emp = await me.my_employee(db, org_id, user_id)
    shift = await db.scalar(
        select(PublishedShift).where(PublishedShift.id == shift_id, PublishedShift.org_id == org_id)
    )
    if shift is None:
        raise not_found("Open shift not found")
    sched = await db.scalar(
        select(Schedule).where(Schedule.id == shift.schedule_id).with_for_update()
    )
    if sched is None:
        raise not_found("Open shift not found")
    shift = await db.scalar(
        select(PublishedShift).where(PublishedShift.id == shift_id).with_for_update()
    )
    if shift is None or shift.employee_id is not None or shift.starts_at <= schedule._now():
        raise not_found("Open shift not found")
    locations = await me.my_location_ids(db, emp.id)
    if shift.location_id not in locations:
        raise not_found("Open shift not found")
    existing = await db.scalar(
        select(ShiftCoverageRequest).where(
            ShiftCoverageRequest.published_shift_id == shift.id,
            ShiftCoverageRequest.employee_id == emp.id,
            ShiftCoverageRequest.status == "pending",
        )
    )
    if existing:
        raise ProblemError(409, "request_exists", "You already requested this shift.")
    position_ids = set(
        await db.scalars(
            select(EmployeePosition.position_id).where(EmployeePosition.employee_id == emp.id)
        )
    )
    if shift.position_id not in position_ids or not emp.active:
        raise ProblemError(422, "not_eligible", "You are not eligible for this shift.")
    draft = await db.scalar(
        select(Shift).where(Shift.id == shift.id, Shift.schedule_id == sched.id).with_for_update()
    )
    if draft is None or draft.employee_id is not None:
        raise ProblemError(409, "schedule_changed", "This shift is no longer open.")
    active_trade = await db.scalar(
        select(ShiftTradeRequest.id)
        .where(
            ShiftTradeRequest.schedule_id == sched.id,
            ShiftTradeRequest.status.in_(("pending_recipient", "pending_manager")),
            or_(
                ShiftTradeRequest.offered_shift_id == shift.id,
                ShiftTradeRequest.requested_shift_id == shift.id,
            ),
        )
        .limit(1)
    )
    if active_trade is not None:
        raise ProblemError(409, "shift_reserved", "This shift is part of a pending trade.")
    req = ShiftCoverageRequest(
        org_id=org_id,
        location_id=shift.location_id,
        schedule_id=sched.id,
        published_shift_id=shift.id,
        employee_id=emp.id,
    )
    db.add(req)
    await db.flush()
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="shift_coverage_request",
        entity_id=req.id,
        action="shift_coverage.requested",
        location_id=shift.location_id,
        details={"shift_id": str(shift.id), "employee_id": str(emp.id)},
    )
    managers = await notify.managers_for(db, org_id, [shift.location_id])
    await notify.notify(
        db,
        org_id,
        managers,
        kind="shift_coverage_requested",
        title="Open shift request",
        body=f"{emp.display_name} requested {shift.business_date}.",
        link="/app/team/requests",
        entity_type="shift_coverage_request",
        entity_id=req.id,
    )
    position = await db.get(Position, shift.position_id)
    if position is None:
        raise ProblemError(409, "schedule_changed", "The shift position is no longer available.")
    return _coverage_out(req, emp, shift, position)


async def cancel_coverage_request(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, request_id: uuid.UUID
) -> ShiftCoverageRequestOut:
    employee = await me.my_employee(db, org_id, user_id)
    req = await db.scalar(
        select(ShiftCoverageRequest)
        .where(
            ShiftCoverageRequest.id == request_id,
            ShiftCoverageRequest.org_id == org_id,
            ShiftCoverageRequest.employee_id == employee.id,
        )
        .with_for_update()
    )
    if req is None:
        raise not_found("Shift request not found")
    if req.status != "pending":
        raise ProblemError(409, "request_resolved", "Only pending requests can be cancelled.")
    sched = await db.scalar(
        select(Schedule).where(Schedule.id == req.schedule_id).with_for_update()
    )
    shift = await db.scalar(
        select(PublishedShift).where(PublishedShift.id == req.published_shift_id)
    )
    if sched is None or shift is None:
        raise ProblemError(409, "schedule_changed", "This shift is no longer available.")
    req.status = "cancelled"
    req.decided_by, req.decided_at = user_id, schedule._now()
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="shift_coverage_request",
        entity_id=req.id,
        action="shift_coverage.cancelled",
        location_id=req.location_id,
        details={"shift_id": str(shift.id)},
    )
    position = await db.get(Position, shift.position_id)
    if position is None:
        raise ProblemError(409, "schedule_changed", "The shift position is no longer available.")
    return _coverage_out(req, employee, shift, position)


async def list_coverage_requests(
    db: AsyncSession, scope: Scope, location_id: uuid.UUID
) -> list[ShiftCoverageRequestOut]:
    await get_location(db, scope, location_id)
    rows = (
        await db.execute(
            select(ShiftCoverageRequest, StaffMember, PublishedShift, Position)
            .join(StaffMember, StaffMember.id == ShiftCoverageRequest.employee_id)
            .join(PublishedShift, PublishedShift.id == ShiftCoverageRequest.published_shift_id)
            .join(Position, Position.id == PublishedShift.position_id)
            .where(
                ShiftCoverageRequest.org_id == scope.org_id,
                ShiftCoverageRequest.location_id == location_id,
                ShiftCoverageRequest.status == "pending",
            )
            .order_by(ShiftCoverageRequest.created_at)
        )
    ).all()
    return [_coverage_out(req, emp, shift, pos) for req, emp, shift, pos in rows]


async def decide_coverage_request(
    db: AsyncSession, scope: Scope, request_id: uuid.UUID, decision: str, note: str | None
) -> ShiftCoverageRequestOut:
    req = await db.scalar(
        select(ShiftCoverageRequest)
        .where(ShiftCoverageRequest.id == request_id, ShiftCoverageRequest.org_id == scope.org_id)
        .with_for_update()
    )
    if req is None or req.location_id not in scope.location_ids:
        raise not_found("Shift request not found")
    if req.status != "pending":
        raise ProblemError(409, "request_resolved", "This request has already been resolved.")
    sched = await db.scalar(
        select(Schedule).where(Schedule.id == req.schedule_id).with_for_update()
    )
    shift = await db.scalar(
        select(PublishedShift).where(PublishedShift.id == req.published_shift_id).with_for_update()
    )
    draft = await db.scalar(
        select(Shift).where(Shift.id == req.published_shift_id).with_for_update()
    )
    employee = await db.scalar(
        select(StaffMember).where(StaffMember.id == req.employee_id).with_for_update()
    )
    if sched is None or shift is None or draft is None or employee is None:
        raise ProblemError(409, "schedule_changed", "The shift or employee is no longer available.")
    if decision == "approve":
        if (
            shift.employee_id is not None
            or draft.employee_id is not None
            or shift.starts_at <= schedule._now()
        ):
            raise ProblemError(409, "schedule_changed", "This shift is no longer available.")
        if any(
            getattr(draft, field) != getattr(shift, field) for field in schedule.SNAPSHOT_FIELDS
        ):
            raise ProblemError(
                409, "schedule_changed", "The shift changed after the request was submitted."
            )
        active_trade = await db.scalar(
            select(ShiftTradeRequest.id)
            .where(
                ShiftTradeRequest.schedule_id == sched.id,
                ShiftTradeRequest.status.in_(("pending_recipient", "pending_manager")),
                or_(
                    ShiftTradeRequest.offered_shift_id == shift.id,
                    ShiftTradeRequest.requested_shift_id == shift.id,
                ),
            )
            .limit(1)
        )
        if active_trade is not None:
            raise ProblemError(409, "shift_reserved", "This shift is part of a pending trade.")
        assigned_locations = await me.my_location_ids(db, employee.id)
        if req.location_id not in assigned_locations:
            raise ProblemError(
                409, "not_eligible", "The employee is no longer assigned to this location."
            )
        position_ids = set(
            await db.scalars(
                select(EmployeePosition.position_id).where(
                    EmployeePosition.employee_id == employee.id
                )
            )
        )
        if draft.position_id not in position_ids:
            raise ProblemError(
                409, "not_eligible", "The employee is no longer eligible for this position."
            )
        scope_for_shift = scope
        await schedule._check_overlap(db, employee, draft.starts_at, draft.ends_at, draft.id)
        await schedule._check_refs(
            db,
            scope_for_shift,
            req.location_id,
            employee_id=employee.id,
            position_id=draft.position_id,
            station_id=draft.station_id,
        )
        draft.employee_id = employee.id
        draft.updated_at = _now()
        await schedule.bump(sched)
        await audit.record(
            db,
            org_id=scope.org_id,
            actor_user_id=scope.user_id,
            entity_type="shift",
            entity_id=draft.id,
            action="shift.coverage_assigned",
            location_id=req.location_id,
            details={"employee_id": str(employee.id), "request_id": str(req.id)},
        )
    req.status = "approved" if decision == "approve" else "denied"
    req.decided_by, req.decided_at, req.decision_note = scope.user_id, schedule._now(), note
    if decision == "approve":
        other_pending = list(
            await db.scalars(
                select(ShiftCoverageRequest)
                .where(
                    ShiftCoverageRequest.published_shift_id == shift.id,
                    ShiftCoverageRequest.status == "pending",
                    ShiftCoverageRequest.id != req.id,
                )
                .with_for_update()
            )
        )
        for other in other_pending:
            other.status = "denied"
            other.decided_by, other.decided_at = scope.user_id, schedule._now()
            other.decision_note = "Another employee was assigned this shift."
            await audit.record(
                db,
                org_id=scope.org_id,
                actor_user_id=scope.user_id,
                entity_type="shift_coverage_request",
                entity_id=other.id,
                action="shift_coverage.denied",
                location_id=req.location_id,
                details={"shift_id": str(shift.id), "reason": "already_assigned"},
            )
            other_employee = await db.get(StaffMember, other.employee_id)
            if other_employee and other_employee.user_id:
                await notify.notify(
                    db,
                    scope.org_id,
                    [other_employee.user_id],
                    kind="shift_coverage_decided",
                    title="Open shift request declined",
                    body="Another employee was assigned this shift.",
                    link="/my",
                    entity_type="shift_coverage_request",
                    entity_id=other.id,
                )
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="shift_coverage_request",
        entity_id=req.id,
        action=f"shift_coverage.{req.status}",
        location_id=req.location_id,
        details={"shift_id": str(req.published_shift_id), "note": note},
    )
    if employee.user_id:
        await notify.notify(
            db,
            scope.org_id,
            [employee.user_id],
            kind="shift_coverage_decided",
            title=f"Open shift request {req.status}",
            body="Your manager updated the draft schedule. Check back after it is published.",
            link="/my",
            entity_type="shift_coverage_request",
            entity_id=req.id,
        )
    position = await db.get(Position, shift.position_id)
    if position is None:
        raise ProblemError(409, "schedule_changed", "The shift position is no longer available.")
    return _coverage_out(req, employee, shift, position)


async def cancel_coverage_for_removed_shift(
    db: AsyncSession, scope: Scope, shift: PublishedShift
) -> None:
    pending = list(
        await db.scalars(
            select(ShiftCoverageRequest)
            .where(
                ShiftCoverageRequest.published_shift_id == shift.id,
                ShiftCoverageRequest.status == "pending",
            )
            .with_for_update()
        )
    )
    for req in pending:
        req.status = "cancelled"
        req.decided_by, req.decided_at = scope.user_id, schedule._now()
        req.decision_note = "The schedule no longer includes this shift."
        await audit.record(
            db,
            org_id=scope.org_id,
            actor_user_id=scope.user_id,
            entity_type="shift_coverage_request",
            entity_id=req.id,
            action="shift_coverage.cancelled",
            location_id=req.location_id,
            details={"shift_id": str(shift.id), "reason": "shift_removed"},
        )
        employee = await db.get(StaffMember, req.employee_id)
        if employee and employee.user_id:
            await notify.notify(
                db,
                scope.org_id,
                [employee.user_id],
                kind="shift_coverage_decided",
                title="Open shift request cancelled",
                body="The schedule no longer includes this shift.",
                link="/my",
                entity_type="shift_coverage_request",
                entity_id=req.id,
            )


schedule.PUBLISH_REMOVE_HOOKS.append(cancel_coverage_for_removed_shift)
