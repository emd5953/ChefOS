"""Manager reviewed schedule proposals from Sous. A proposal never changes a schedule."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.errors import ProblemError, not_found
from app.security.sessions import AuthContext
from app.sous.models import ScheduleProposal
from app.team import schedule as schedule_service
from app.team import service as team_service
from app.team.models import Schedule
from app.team.schemas import ShiftIn


def _now() -> datetime:
    return datetime.now(UTC)


async def create(
    db: AsyncSession,
    ctx: AuthContext,
    location_id: uuid.UUID,
    week_start: date,
    shift: ShiftIn,
) -> ScheduleProposal:
    scope = await team_service.scope_for(ctx)
    location = await team_service.get_location(db, scope, location_id)
    if week_start.weekday() != 0:
        raise ProblemError(422, "invalid_week", "Schedule weeks start on Monday.")
    sched = await schedule_service.lock_schedule(db, ctx.org_id, location.id, week_start)
    times = schedule_service._resolve_times(
        location, week_start, shift.starts_at, shift.ends_at, shift.break_minutes
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
        await schedule_service._check_overlap(db, employee, times.starts_at, times.ends_at, None)
    proposal = ScheduleProposal(
        org_id=ctx.org_id,
        location_id=location.id,
        schedule_id=sched.id,
        created_by=ctx.user_id,
        expected_version=sched.version,
        payload=shift.model_dump(mode="json"),
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
        details={"schedule_id": str(sched.id), "week_start": week_start.isoformat()},
    )
    return proposal


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
        shift = ShiftIn.model_validate(proposal.payload)
        week_start = sched.week_start
        await schedule_service.create_shift(db, scope, proposal.location_id, week_start, shift)
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
        details={"schedule_id": str(sched.id)},
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
