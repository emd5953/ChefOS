"""Two-party shift swaps with employee acceptance and manager approval."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.audit import service as audit
from app.errors import ProblemError, not_found, validation_error
from app.identity.models import Location
from app.team import me, notify, schedule
from app.team.models import (
    EmployeeLocation,
    EmployeePosition,
    Position,
    PublishedShift,
    Schedule,
    Shift,
    ShiftCoverageRequest,
    ShiftTradeRequest,
    StaffMember,
)
from app.team.schemas import ShiftTradeOut, ShiftTradeShift
from app.team.service import Scope, get_location

ACTIVE = ("pending_recipient", "pending_manager")


def _now() -> datetime:
    return datetime.now(UTC)


async def _rows(
    db: AsyncSession, conditions: list[ColumnElement[bool]], limit: int = 100
) -> list[ShiftTradeOut]:
    requester = aliased(StaffMember)
    recipient = aliased(StaffMember)
    offered = aliased(PublishedShift)
    requested = aliased(PublishedShift)
    offered_position = aliased(Position)
    requested_position = aliased(Position)
    rows = (
        await db.execute(
            select(
                ShiftTradeRequest,
                requester,
                recipient,
                offered,
                offered_position,
                requested,
                requested_position,
                Location,
            )
            .join(requester, requester.id == ShiftTradeRequest.requester_employee_id)
            .join(recipient, recipient.id == ShiftTradeRequest.recipient_employee_id)
            .join(offered, offered.id == ShiftTradeRequest.offered_shift_id)
            .join(offered_position, offered_position.id == offered.position_id)
            .join(requested, requested.id == ShiftTradeRequest.requested_shift_id)
            .join(requested_position, requested_position.id == requested.position_id)
            .join(Location, Location.id == ShiftTradeRequest.location_id)
            .where(*conditions)
            .order_by(ShiftTradeRequest.created_at.desc(), ShiftTradeRequest.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        ShiftTradeOut(
            id=req.id,
            location_id=req.location_id,
            location_name=location.name,
            timezone=location.timezone,
            schedule_id=req.schedule_id,
            status=req.status,
            my_role="manager",
            requester_employee_id=requester_row.id,
            requester_name=requester_row.display_name,
            recipient_employee_id=recipient_row.id,
            recipient_name=recipient_row.display_name,
            offered_shift=ShiftTradeShift(
                id=offered_row.id,
                business_date=offered_row.business_date,
                starts_at=offered_row.starts_at,
                ends_at=offered_row.ends_at,
                position_name=op.name,
            ),
            requested_shift=ShiftTradeShift(
                id=requested_row.id,
                business_date=requested_row.business_date,
                starts_at=requested_row.starts_at,
                ends_at=requested_row.ends_at,
                position_name=rp.name,
            ),
            created_at=req.created_at,
            decided_at=req.decided_at,
            decision_note=req.decision_note,
        )
        for req, requester_row, recipient_row, offered_row, op, requested_row, rp, location in rows
    ]


async def _employee_scope(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> StaffMember:
    return await me.my_employee(db, org_id, user_id)


async def _validate_trade_shifts(
    db: AsyncSession,
    org_id: uuid.UUID,
    requester: StaffMember,
    offered_id: uuid.UUID,
    requested_id: uuid.UUID,
    *,
    lock: bool,
) -> tuple[Schedule, PublishedShift, PublishedShift]:
    if offered_id == requested_id:
        raise validation_error({"requested_shift_id": "Choose a different shift"})
    offered = await db.scalar(
        select(PublishedShift).where(
            PublishedShift.id == offered_id, PublishedShift.org_id == org_id
        )
    )
    requested = await db.scalar(
        select(PublishedShift).where(
            PublishedShift.id == requested_id, PublishedShift.org_id == org_id
        )
    )
    if (
        offered is None
        or requested is None
        or offered.schedule_id != requested.schedule_id
        or offered.location_id != requested.location_id
        or offered.employee_id != requester.id
        or requested.employee_id is None
        or requested.employee_id == requester.id
        or offered.starts_at <= schedule._now()
        or requested.starts_at <= schedule._now()
    ):
        raise not_found("Trade shifts not found")
    sched = await db.scalar(select(Schedule).where(Schedule.id == offered.schedule_id))
    if sched is None:
        raise not_found("Trade shifts not found")
    if lock:
        sched = await db.scalar(
            select(Schedule).where(Schedule.id == offered.schedule_id).with_for_update()
        )
        assert sched is not None
        shift_ids = sorted((offered_id, requested_id), key=str)
        locked: dict[uuid.UUID, PublishedShift] = {}
        for shift_id in shift_ids:
            row = await db.scalar(
                select(PublishedShift).where(PublishedShift.id == shift_id).with_for_update()
            )
            if row is None:
                raise ProblemError(409, "schedule_changed", "A shift was removed.")
            locked[shift_id] = row
        offered, requested = locked[offered_id], locked[requested_id]
    locations = set(
        await db.scalars(
            select(EmployeeLocation.location_id).where(EmployeeLocation.employee_id == requester.id)
        )
    )
    if offered.location_id not in locations:
        raise not_found("Trade shifts not found")
    return sched, offered, requested


async def _check_reservations(
    db: AsyncSession,
    schedule_id: uuid.UUID,
    shift_ids: set[uuid.UUID],
    *,
    exclude_request_id: uuid.UUID | None = None,
) -> None:
    conditions = [
        ShiftTradeRequest.schedule_id == schedule_id,
        ShiftTradeRequest.status.in_(ACTIVE),
        or_(
            ShiftTradeRequest.offered_shift_id.in_(shift_ids),
            ShiftTradeRequest.requested_shift_id.in_(shift_ids),
        ),
    ]
    if exclude_request_id is not None:
        conditions.append(ShiftTradeRequest.id != exclude_request_id)
    active_trade = await db.scalar(select(ShiftTradeRequest.id).where(*conditions).limit(1))
    if active_trade is not None:
        raise ProblemError(
            409, "shift_reserved", "One of these shifts is in another pending trade."
        )
    open_claim = await db.scalar(
        select(ShiftCoverageRequest.id)
        .where(
            ShiftCoverageRequest.published_shift_id.in_(shift_ids),
            ShiftCoverageRequest.status == "pending",
        )
        .limit(1)
    )
    if open_claim is not None:
        raise ProblemError(409, "shift_reserved", "A shift has an unresolved open-shift request.")


async def _check_snapshot(
    db: AsyncSession,
    schedule_id: uuid.UUID,
    offered: PublishedShift,
    requested: PublishedShift,
) -> tuple[Shift, Shift]:
    draft_rows: dict[uuid.UUID, Shift] = {}
    for shift_id in sorted((offered.id, requested.id), key=str):
        row = await db.scalar(
            select(Shift)
            .where(Shift.id == shift_id, Shift.schedule_id == schedule_id)
            .with_for_update()
        )
        if row is None:
            raise ProblemError(409, "schedule_changed", "A shift was removed from the draft.")
        draft_rows[shift_id] = row
    first, second = draft_rows[offered.id], draft_rows[requested.id]
    for draft, published in ((first, offered), (second, requested)):
        if any(
            getattr(draft, field) != getattr(published, field) for field in schedule.SNAPSHOT_FIELDS
        ):
            raise ProblemError(
                409, "schedule_changed", "A shift changed after the trade was requested."
            )
    return first, second


async def _recipient_user(db: AsyncSession, employee_id: uuid.UUID) -> uuid.UUID | None:
    return await db.scalar(select(StaffMember.user_id).where(StaffMember.id == employee_id))


async def create(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    offered_shift_id: uuid.UUID,
    requested_shift_id: uuid.UUID,
) -> ShiftTradeOut:
    requester = await _employee_scope(db, org_id, user_id)
    sched, offered, requested = await _validate_trade_shifts(
        db, org_id, requester, offered_shift_id, requested_shift_id, lock=True
    )
    recipient = await db.scalar(
        select(StaffMember).where(
            StaffMember.id == requested.employee_id, StaffMember.org_id == org_id
        )
    )
    if recipient is None or not recipient.active or recipient.merged_into_id is not None:
        raise ProblemError(409, "not_eligible", "The other employee is no longer active.")
    if recipient.user_id is None:
        raise ProblemError(
            422, "employee_not_linked", "The other employee cannot review a trade yet."
        )
    employee_locations = {
        (row.employee_id, row.location_id)
        for row in await db.execute(
            select(EmployeeLocation.employee_id, EmployeeLocation.location_id).where(
                EmployeeLocation.employee_id.in_([requester.id, recipient.id])
            )
        )
    }
    if (requester.id, offered.location_id) not in employee_locations or (
        recipient.id,
        offered.location_id,
    ) not in employee_locations:
        raise ProblemError(422, "not_eligible", "Both employees must be assigned to this location.")
    eligible_positions = {
        (row.employee_id, row.position_id)
        for row in await db.execute(
            select(EmployeePosition.employee_id, EmployeePosition.position_id).where(
                EmployeePosition.employee_id.in_([requester.id, recipient.id])
            )
        )
    }
    if (requester.id, requested.position_id) not in eligible_positions or (
        recipient.id,
        offered.position_id,
    ) not in eligible_positions:
        raise ProblemError(
            422, "not_eligible", "An employee is not eligible for the swapped position."
        )
    await _check_reservations(db, sched.id, {offered.id, requested.id})
    for shift in (offered, requested):
        if shift.starts_at <= schedule._now():
            raise ProblemError(409, "schedule_changed", "Past shifts cannot be traded.")
    snapshots = await _check_snapshot(db, sched.id, offered, requested)
    if snapshots[0].employee_id != requester.id or snapshots[1].employee_id != recipient.id:
        raise ProblemError(409, "schedule_changed", "Shift assignments changed after publishing.")
    await _check_trade_overlap(db, requester, snapshots[1], snapshots[0])
    await _check_trade_overlap(db, recipient, snapshots[0], snapshots[1])
    req = ShiftTradeRequest(
        org_id=org_id,
        location_id=offered.location_id,
        schedule_id=sched.id,
        offered_shift_id=offered.id,
        requested_shift_id=requested.id,
        requester_employee_id=requester.id,
        recipient_employee_id=recipient.id,
    )
    db.add(req)
    await db.flush()
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="shift_trade_request",
        entity_id=req.id,
        action="shift_trade.requested",
        location_id=req.location_id,
        details={"offered_shift_id": str(offered.id), "requested_shift_id": str(requested.id)},
    )
    await notify.notify(
        db,
        org_id,
        [recipient.user_id],
        kind="shift_trade_requested",
        title="Shift trade request",
        body=f"{requester.display_name} asked to swap shifts with you.",
        link="/my/requests",
        entity_type="shift_trade_request",
        entity_id=req.id,
    )
    return (await list_for_employee(db, org_id, user_id, request_id=req.id))[0]


async def list_for_employee(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, request_id: uuid.UUID | None = None
) -> list[ShiftTradeOut]:
    employee = await _employee_scope(db, org_id, user_id)
    conditions: list[ColumnElement[bool]] = [
        ShiftTradeRequest.org_id == org_id,
        or_(
            ShiftTradeRequest.requester_employee_id == employee.id,
            ShiftTradeRequest.recipient_employee_id == employee.id,
        ),
    ]
    if request_id is not None:
        conditions.append(ShiftTradeRequest.id == request_id)
    rows = await _rows(db, conditions)
    for row in rows:
        row.my_role = "requester" if row.requester_employee_id == employee.id else "recipient"
    return rows


async def list_for_manager(
    db: AsyncSession, scope: Scope, location_id: uuid.UUID
) -> list[ShiftTradeOut]:
    await get_location(db, scope, location_id)
    return await _rows(
        db,
        [
            ShiftTradeRequest.org_id == scope.org_id,
            ShiftTradeRequest.location_id == location_id,
            ShiftTradeRequest.status == "pending_manager",
        ],
    )


async def respond(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    request_id: uuid.UUID,
    decision: str,
    note: str | None,
) -> ShiftTradeOut:
    employee = await _employee_scope(db, org_id, user_id)
    req = await db.scalar(
        select(ShiftTradeRequest)
        .where(
            ShiftTradeRequest.id == request_id,
            ShiftTradeRequest.org_id == org_id,
            ShiftTradeRequest.recipient_employee_id == employee.id,
        )
        .with_for_update()
    )
    if req is None:
        raise not_found("Shift trade not found")
    if req.status != "pending_recipient":
        raise ProblemError(
            409, "request_resolved", "This trade is no longer awaiting your response."
        )
    sched = await db.scalar(
        select(Schedule).where(Schedule.id == req.schedule_id).with_for_update()
    )
    if sched is None:
        raise ProblemError(409, "schedule_changed", "The schedule no longer exists.")
    offered = await db.scalar(
        select(PublishedShift).where(PublishedShift.id == req.offered_shift_id).with_for_update()
    )
    requested = await db.scalar(
        select(PublishedShift).where(PublishedShift.id == req.requested_shift_id).with_for_update()
    )
    if offered is None or requested is None:
        raise ProblemError(409, "schedule_changed", "A shift was removed.")
    await _check_reservations(db, sched.id, {offered.id, requested.id}, exclude_request_id=req.id)
    await _check_snapshot(db, sched.id, offered, requested)
    if (
        offered.employee_id != req.requester_employee_id
        or requested.employee_id != req.recipient_employee_id
    ):
        raise ProblemError(409, "schedule_changed", "Shift assignments changed after publishing.")
    req.status = "pending_manager" if decision == "accept" else "denied"
    req.decided_at = schedule._now()
    req.decided_by = user_id
    req.decision_note = note
    if decision == "decline":
        req.decided_by = user_id
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="shift_trade_request",
        entity_id=req.id,
        action="shift_trade.accepted" if decision == "accept" else "shift_trade.declined",
        location_id=req.location_id,
        details={"note": note},
    )
    if decision == "accept":
        managers = await notify.managers_for(db, org_id, [req.location_id])
        await notify.notify(
            db,
            org_id,
            managers,
            kind="shift_trade_accepted",
            title="Shift trade accepted",
            body=f"{employee.display_name} accepted a shift trade.",
            link="/app/team/requests",
            entity_type="shift_trade_request",
            entity_id=req.id,
        )
    else:
        requester_user = await _recipient_user(db, req.requester_employee_id)
        if requester_user:
            await notify.notify(
                db,
                org_id,
                [requester_user],
                kind="shift_trade_decided",
                title="Shift trade declined",
                body=f"{employee.display_name} declined your request.",
                link="/my/requests",
                entity_type="shift_trade_request",
                entity_id=req.id,
            )
    return (await list_for_employee(db, org_id, user_id, request_id=req.id))[0]


async def cancel(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, request_id: uuid.UUID
) -> ShiftTradeOut:
    employee = await _employee_scope(db, org_id, user_id)
    req = await db.scalar(
        select(ShiftTradeRequest)
        .where(
            ShiftTradeRequest.id == request_id,
            ShiftTradeRequest.org_id == org_id,
            ShiftTradeRequest.requester_employee_id == employee.id,
        )
        .with_for_update()
    )
    if req is None:
        raise not_found("Shift trade not found")
    if req.status not in ACTIVE:
        raise ProblemError(409, "request_resolved", "This trade can no longer be cancelled.")
    sched = await db.scalar(
        select(Schedule).where(Schedule.id == req.schedule_id).with_for_update()
    )
    if sched is None:
        raise ProblemError(409, "schedule_changed", "The schedule no longer exists.")
    req.status, req.decided_at, req.decided_by = "cancelled", schedule._now(), user_id
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="shift_trade_request",
        entity_id=req.id,
        action="shift_trade.cancelled",
        location_id=req.location_id,
        details={},
    )
    recipient_user = await _recipient_user(db, req.recipient_employee_id)
    if recipient_user:
        await notify.notify(
            db,
            org_id,
            [recipient_user],
            kind="shift_trade_decided",
            title="Shift trade withdrawn",
            body=f"{employee.display_name} withdrew the trade request.",
            link="/my/requests",
            entity_type="shift_trade_request",
            entity_id=req.id,
        )
    managers = await notify.managers_for(db, org_id, [req.location_id])
    await notify.notify(
        db,
        org_id,
        managers,
        kind="shift_trade_withdrawn",
        title="Shift trade withdrawn",
        body=f"{employee.display_name} withdrew a trade request.",
        link="/app/team/requests",
        entity_type="shift_trade_request",
        entity_id=req.id,
    )
    return (await list_for_employee(db, org_id, user_id, request_id=req.id))[0]


async def decide(
    db: AsyncSession,
    scope: Scope,
    request_id: uuid.UUID,
    decision: str,
    note: str | None,
) -> ShiftTradeOut:
    req = await db.scalar(
        select(ShiftTradeRequest)
        .where(ShiftTradeRequest.id == request_id, ShiftTradeRequest.org_id == scope.org_id)
        .with_for_update()
    )
    if req is None or req.location_id not in scope.location_ids:
        raise not_found("Shift trade not found")
    if req.status != "pending_manager":
        raise ProblemError(409, "request_resolved", "This trade is not awaiting manager approval.")
    sched = await db.scalar(
        select(Schedule).where(Schedule.id == req.schedule_id).with_for_update()
    )
    if sched is None:
        raise ProblemError(409, "schedule_changed", "The schedule no longer exists.")
    offered = await db.scalar(
        select(PublishedShift).where(PublishedShift.id == req.offered_shift_id).with_for_update()
    )
    requested = await db.scalar(
        select(PublishedShift).where(PublishedShift.id == req.requested_shift_id).with_for_update()
    )
    if offered is None or requested is None:
        raise ProblemError(409, "schedule_changed", "A shift was removed.")
    if decision == "approve":
        await _check_reservations(
            db, sched.id, {offered.id, requested.id}, exclude_request_id=req.id
        )
        first, second = await _check_snapshot(db, sched.id, offered, requested)
        if (
            offered.employee_id != req.requester_employee_id
            or requested.employee_id != req.recipient_employee_id
            or offered.starts_at <= schedule._now()
            or requested.starts_at <= schedule._now()
        ):
            raise ProblemError(
                409, "schedule_changed", "Shift assignments changed after publishing."
            )
        requester = await db.scalar(
            select(StaffMember).where(StaffMember.id == req.requester_employee_id).with_for_update()
        )
        recipient = await db.scalar(
            select(StaffMember).where(StaffMember.id == req.recipient_employee_id).with_for_update()
        )
        if requester is None or recipient is None:
            raise ProblemError(409, "not_eligible", "An employee is no longer available.")
        member_locations = {
            (row.employee_id, row.location_id)
            for row in await db.execute(
                select(EmployeeLocation.employee_id, EmployeeLocation.location_id).where(
                    EmployeeLocation.employee_id.in_([requester.id, recipient.id])
                )
            )
        }
        if (requester.id, req.location_id) not in member_locations or (
            recipient.id,
            req.location_id,
        ) not in member_locations:
            raise ProblemError(
                409, "not_eligible", "An employee is no longer assigned to this location."
            )
        requester_positions = set(
            await db.scalars(
                select(EmployeePosition.position_id).where(
                    EmployeePosition.employee_id == requester.id
                )
            )
        )
        recipient_positions = set(
            await db.scalars(
                select(EmployeePosition.position_id).where(
                    EmployeePosition.employee_id == recipient.id
                )
            )
        )
        if (
            first.position_id not in recipient_positions
            or second.position_id not in requester_positions
        ):
            raise ProblemError(
                409, "not_eligible", "An employee is no longer eligible for the swapped position."
            )
        loc_scope = Scope(scope.org_id, scope.user_id, scope.role, frozenset({req.location_id}))
        await schedule._check_refs(
            db,
            loc_scope,
            req.location_id,
            employee_id=recipient.id,
            position_id=first.position_id,
            station_id=first.station_id,
        )
        await schedule._check_refs(
            db,
            loc_scope,
            req.location_id,
            employee_id=requester.id,
            position_id=second.position_id,
            station_id=second.station_id,
        )
        await _check_trade_overlap(db, recipient, first, second)
        await _check_trade_overlap(db, requester, second, first)
        before_first, before_second = first.employee_id, second.employee_id
        first.employee_id, second.employee_id = recipient.id, requester.id
        first.updated_at = second.updated_at = schedule._now()
        await schedule.bump(sched)
        for row, before, after in (
            (first, before_first, first.employee_id),
            (second, before_second, second.employee_id),
        ):
            await audit.record(
                db,
                org_id=scope.org_id,
                actor_user_id=scope.user_id,
                entity_type="shift",
                entity_id=row.id,
                action="shift.trade_assigned",
                location_id=req.location_id,
                details={
                    "before_employee_id": str(before),
                    "after_employee_id": str(after),
                    "shift_trade_request_id": str(req.id),
                },
            )
    req.status = "approved" if decision == "approve" else "denied"
    req.decided_by, req.decided_at, req.decision_note = scope.user_id, schedule._now(), note
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="shift_trade_request",
        entity_id=req.id,
        action=f"shift_trade.{req.status}",
        location_id=req.location_id,
        details={"note": note},
    )
    users = await db.scalars(
        select(StaffMember.user_id).where(
            StaffMember.id.in_([req.requester_employee_id, req.recipient_employee_id]),
            StaffMember.user_id.is_not(None),
        )
    )
    await notify.notify(
        db,
        scope.org_id,
        [uid for uid in users if uid],
        kind="shift_trade_decided",
        title=f"Shift trade {req.status}",
        body="Your schedule changed in the draft. Your manager must publish it.",
        link="/my/requests",
        entity_type="shift_trade_request",
        entity_id=req.id,
    )
    return (await _rows(db, [ShiftTradeRequest.id == req.id]))[0]


async def _check_trade_overlap(
    db: AsyncSession,
    employee: StaffMember,
    new_shift: Shift,
    other_shift: Shift,
) -> None:
    conflict = await db.scalar(
        select(Shift.id)
        .where(
            Shift.employee_id == employee.id,
            Shift.id.not_in([new_shift.id, other_shift.id]),
            Shift.starts_at < new_shift.ends_at,
            Shift.ends_at > new_shift.starts_at,
        )
        .limit(1)
    )
    if conflict is not None:
        raise ProblemError(
            409, "shift_overlap", f"{employee.display_name} already has an overlapping shift."
        )


async def cancel_trades_for_removed_shift(
    db: AsyncSession, scope: Scope, shift: PublishedShift
) -> None:
    pending = list(
        await db.scalars(
            select(ShiftTradeRequest)
            .where(
                ShiftTradeRequest.status.in_(ACTIVE),
                or_(
                    ShiftTradeRequest.offered_shift_id == shift.id,
                    ShiftTradeRequest.requested_shift_id == shift.id,
                ),
            )
            .with_for_update()
        )
    )
    for req in pending:
        req.status, req.decided_by, req.decided_at = "cancelled", scope.user_id, schedule._now()
        req.decision_note = "The schedule no longer includes one of these shifts."
        await audit.record(
            db,
            org_id=scope.org_id,
            actor_user_id=scope.user_id,
            entity_type="shift_trade_request",
            entity_id=req.id,
            action="shift_trade.cancelled",
            location_id=req.location_id,
            details={"shift_id": str(shift.id), "reason": "shift_removed"},
        )
        user_ids = await db.scalars(
            select(StaffMember.user_id).where(
                StaffMember.id.in_([req.requester_employee_id, req.recipient_employee_id]),
                StaffMember.user_id.is_not(None),
            )
        )
        await notify.notify(
            db,
            scope.org_id,
            [user_id for user_id in user_ids if user_id],
            kind="shift_trade_decided",
            title="Shift trade cancelled",
            body="The schedule no longer includes one of these shifts.",
            link="/my/requests",
            entity_type="shift_trade_request",
            entity_id=req.id,
        )


schedule.PUBLISH_REMOVE_HOOKS.append(cancel_trades_for_removed_shift)
