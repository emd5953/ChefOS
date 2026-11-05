"""Manager reviewed schedule proposals from Sous. A proposal never changes a schedule."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.errors import ProblemError, not_found
from app.identity.models import Location
from app.security.sessions import AuthContext
from app.sous.models import ScheduleProposal
from app.team import service as team_service
from app.team.models import Position, PublishedShift, Schedule, Shift, StaffMember, Station
from app.team.scheduling import builder as schedule_service
from app.team.schemas import ShiftIn, ShiftPatch
from app.team.service import Scope


def _now() -> datetime:
    return datetime.now(UTC)


async def create(
    db: AsyncSession,
    ctx: AuthContext,
    location_id: uuid.UUID,
    *,
    action: str,
    week_start: date | None,
    target_shift_id: uuid.UUID | None,
    shift: ShiftIn | None,
) -> ScheduleProposal:
    scope = await team_service.scope_for(ctx)
    location = await team_service.get_location(db, scope, location_id)
    target: Shift | None = None
    before_snapshot: dict[str, object] | None = None
    if action == "add":
        if week_start is None or week_start.weekday() != 0 or shift is None or target_shift_id:
            raise ProblemError(
                422, "invalid_proposal", "A new shift needs a Monday week and a shift payload."
            )
        sched = await schedule_service.lock_schedule(db, ctx.org_id, location.id, week_start)
        await _validate_shift(db, scope, location, week_start, shift, exclude_shift_id=None)
    elif action in {"update", "remove"}:
        if target_shift_id is None or (action == "update") != (shift is not None):
            raise ProblemError(
                422,
                "invalid_proposal",
                "An edit or removal needs the target shift and a valid payload.",
            )
        target = await db.scalar(
            select(Shift).where(
                Shift.id == target_shift_id,
                Shift.org_id == ctx.org_id,
                Shift.location_id == location.id,
            )
        )
        if target is None:
            raise not_found("Draft shift not found")
        week_start = target.business_date - timedelta(days=target.business_date.weekday())
        sched = await schedule_service.lock_schedule(db, ctx.org_id, location.id, week_start)
        target = await db.scalar(
            select(Shift)
            .where(Shift.id == target_shift_id, Shift.schedule_id == sched.id)
            .with_for_update()
        )
        if target is None:
            raise not_found("Draft shift not found")
        published = await db.get(PublishedShift, target.id)
        if published is None or published.schedule_id != sched.id:
            raise ProblemError(
                409, "conflict", "Only a currently published shift can be changed by Sous."
            )
        if any(
            getattr(target, field) != getattr(published, field)
            for field in schedule_service.SNAPSHOT_FIELDS
        ):
            raise ProblemError(
                409,
                "conflict",
                "This shift has unpublished draft changes. Resolve them before proposing another "
                "change.",
            )
        before_snapshot = await _snapshot(db, target)
        if action == "update":
            assert shift is not None
            await _validate_shift(
                db, scope, location, week_start, shift, exclude_shift_id=target.id
            )
    else:
        raise ProblemError(
            422, "invalid_proposal", "Supported actions are add, update, and remove."
        )

    proposal = ScheduleProposal(
        org_id=ctx.org_id,
        location_id=location.id,
        schedule_id=sched.id,
        created_by=ctx.user_id,
        expected_version=sched.version,
        action=action,
        target_shift_id=target.id if target else None,
        payload=shift.model_dump(mode="json") if shift is not None else None,
        before_snapshot=before_snapshot,
        expires_at=_now() + timedelta(hours=24),
    )
    db.add(proposal)
    await db.flush()
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="schedule_proposal",
        entity_id=proposal.id,
        action="schedule.proposal_created",
        location_id=location.id,
        details={
            "schedule_id": str(sched.id),
            "week_start": week_start.isoformat(),
            "action": action,
            "target_shift_id": str(target.id) if target else None,
        },
    )
    return proposal


async def _validate_shift(
    db: AsyncSession,
    scope: Scope,
    location: Location,
    week_start: date,
    shift: ShiftIn,
    *,
    exclude_shift_id: uuid.UUID | None,
) -> None:
    times = schedule_service._resolve_times(
        location,
        week_start,
        shift.starts_at,
        shift.ends_at,
        shift.break_minutes,
    )
    employee = await schedule_service._check_refs(
        db,
        scope,
        location.id,
        employee_id=shift.employee_id,
        position_id=shift.position_id,
        station_id=shift.station_id,
    )
    if employee is not None:
        await schedule_service._check_overlap(
            db, employee, times.starts_at, times.ends_at, exclude_shift_id
        )


async def _snapshot(db: AsyncSession, shift: Shift) -> dict[str, object]:
    position = await db.get(Position, shift.position_id)
    employee = await db.get(StaffMember, shift.employee_id) if shift.employee_id else None
    station = await db.get(Station, shift.station_id) if shift.station_id else None
    return {
        "shift_id": str(shift.id),
        "employee_id": str(shift.employee_id) if shift.employee_id else None,
        "employee_name": employee.display_name if employee else None,
        "position_id": str(shift.position_id),
        "position_name": position.name if position else "Position",
        "station_id": str(shift.station_id) if shift.station_id else None,
        "station_name": station.name if station else None,
        "business_date": shift.business_date.isoformat(),
        "starts_at": shift.starts_at.isoformat(),
        "ends_at": shift.ends_at.isoformat(),
        "break_minutes": shift.break_minutes,
        "notes": shift.notes,
    }


async def decide(
    db: AsyncSession,
    ctx: AuthContext,
    proposal_id: uuid.UUID,
    approve: bool,
) -> ScheduleProposal:
    scope = await team_service.scope_for(ctx)
    proposal = await db.scalar(
        select(ScheduleProposal)
        .where(ScheduleProposal.id == proposal_id, ScheduleProposal.org_id == ctx.org_id)
        .with_for_update()
    )
    if proposal is None or proposal.location_id not in scope.location_ids:
        raise not_found("Schedule proposal not found")
    if proposal.status != "pending":
        return proposal
    if proposal.expires_at <= _now():
        proposal.status = "expired"
        proposal.decided_by = ctx.user_id
        proposal.decided_at = _now()
        await audit.record(
            db,
            org_id=ctx.org_id,
            actor_user_id=ctx.user_id,
            entity_type="schedule_proposal",
            entity_id=proposal.id,
            action="schedule.proposal_expired",
            location_id=proposal.location_id,
            details={"schedule_id": str(proposal.schedule_id)},
        )
        await db.flush()
        return proposal
    sched = await db.scalar(
        select(Schedule).where(Schedule.id == proposal.schedule_id).with_for_update()
    )
    if sched is None:
        raise not_found("Schedule not found")
    if sched.version != proposal.expected_version:
        proposal.status = "stale"
        proposal.decided_by = ctx.user_id
        proposal.decided_at = _now()
        await audit.record(
            db,
            org_id=ctx.org_id,
            actor_user_id=ctx.user_id,
            entity_type="schedule_proposal",
            entity_id=proposal.id,
            action="schedule.proposal_stale",
            location_id=proposal.location_id,
            details={"schedule_id": str(sched.id)},
        )
        await db.flush()
        return proposal
    if approve:
        week_start = sched.week_start
        if proposal.action == "add":
            if proposal.payload is None:
                raise ProblemError(409, "stale", "The proposal has no shift payload.")
            await schedule_service.create_shift(
                db,
                scope,
                proposal.location_id,
                week_start,
                ShiftIn.model_validate(proposal.payload),
            )
        elif proposal.action == "update":
            if proposal.target_shift_id is None or proposal.payload is None:
                raise ProblemError(409, "stale", "The proposal target or shift payload is missing.")
            target = await db.scalar(
                select(Shift)
                .where(
                    Shift.id == proposal.target_shift_id,
                    Shift.org_id == ctx.org_id,
                    Shift.schedule_id == sched.id,
                )
                .with_for_update()
            )
            if target is None:
                proposal.status = "stale"
                proposal.decided_by = ctx.user_id
                proposal.decided_at = _now()
                await audit.record(
                    db,
                    org_id=ctx.org_id,
                    actor_user_id=ctx.user_id,
                    entity_type="schedule_proposal",
                    entity_id=proposal.id,
                    action="schedule.proposal_stale",
                    location_id=proposal.location_id,
                    details={"schedule_id": str(sched.id), "reason": "target_shift_missing"},
                )
                await db.flush()
                return proposal
            if proposal.before_snapshot != await _snapshot(db, target):
                proposal.status = "stale"
                proposal.decided_by = ctx.user_id
                proposal.decided_at = _now()
                await audit.record(
                    db,
                    org_id=ctx.org_id,
                    actor_user_id=ctx.user_id,
                    entity_type="schedule_proposal",
                    entity_id=proposal.id,
                    action="schedule.proposal_stale",
                    location_id=proposal.location_id,
                    details={"schedule_id": str(sched.id), "reason": "target_snapshot_changed"},
                )
                await db.flush()
                return proposal
            data = ShiftIn.model_validate(proposal.payload)
            await schedule_service.update_shift(
                db,
                scope,
                target.id,
                ShiftPatch(
                    **data.model_dump(),
                    expected_updated_at=target.updated_at,
                ),
            )
        elif proposal.action == "remove":
            if proposal.target_shift_id is None:
                raise ProblemError(409, "stale", "The proposal target is missing.")
            target = await db.scalar(
                select(Shift)
                .where(
                    Shift.id == proposal.target_shift_id,
                    Shift.org_id == ctx.org_id,
                    Shift.schedule_id == sched.id,
                )
                .with_for_update()
            )
            if target is None:
                proposal.status = "stale"
                proposal.decided_by = ctx.user_id
                proposal.decided_at = _now()
                await audit.record(
                    db,
                    org_id=ctx.org_id,
                    actor_user_id=ctx.user_id,
                    entity_type="schedule_proposal",
                    entity_id=proposal.id,
                    action="schedule.proposal_stale",
                    location_id=proposal.location_id,
                    details={"schedule_id": str(sched.id), "reason": "target_shift_missing"},
                )
                await db.flush()
                return proposal
            if proposal.before_snapshot != await _snapshot(db, target):
                proposal.status = "stale"
                proposal.decided_by = ctx.user_id
                proposal.decided_at = _now()
                await audit.record(
                    db,
                    org_id=ctx.org_id,
                    actor_user_id=ctx.user_id,
                    entity_type="schedule_proposal",
                    entity_id=proposal.id,
                    action="schedule.proposal_stale",
                    location_id=proposal.location_id,
                    details={"schedule_id": str(sched.id), "reason": "target_snapshot_changed"},
                )
                await db.flush()
                return proposal
            await schedule_service.delete_shift(db, scope, target.id)
        else:
            raise ProblemError(409, "stale", "The proposal action is no longer supported.")
        proposal.status = "approved"
    else:
        proposal.status = "denied"
    proposal.decided_by = ctx.user_id
    proposal.decided_at = _now()
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="schedule_proposal",
        entity_id=proposal.id,
        action=f"schedule.proposal_{proposal.status}",
        location_id=proposal.location_id,
        details={
            "schedule_id": str(sched.id),
            "action": proposal.action,
            "target_shift_id": str(proposal.target_shift_id) if proposal.target_shift_id else None,
        },
    )
    await db.flush()
    return proposal


async def list_pending(db: AsyncSession, ctx: AuthContext) -> list[ScheduleProposal]:
    scope = await team_service.scope_for(ctx)
    return list(
        await db.scalars(
            select(ScheduleProposal)
            .where(
                ScheduleProposal.org_id == ctx.org_id,
                ScheduleProposal.location_id.in_(scope.location_ids),
                ScheduleProposal.status == "pending",
            )
            .order_by(ScheduleProposal.created_at.desc())
        )
    )
