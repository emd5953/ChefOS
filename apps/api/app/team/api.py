"""Team HTTP API (docs/api.md, Team section).

Every /v1/team route requires owner, admin, or manager (`StaffDep`); employees get 403.
"""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from app.identity import service as identity
from app.identity.schemas import OrgOut, SessionOut, UserOut
from app.security.sessions import AuthDep, OrgSession, StaffDep, set_session_cookie
from app.team import invitations, me, notify, service
from app.team.models import StaffMember

# `availability` registers the time-off and availability conflict checks with the builder, and
# `coverage` registers the removed-shift cancellation hook. Both must load with this module.
from app.team.requests import availability, coverage, trades
from app.team.scheduling import attendance
from app.team.scheduling import builder as schedule
from app.team.schemas import (
    AttendanceOut,
    AvailabilityIn,
    AvailabilityOut,
    CopyOut,
    CopyRequest,
    Decision,
    EmployeeCreate,
    EmployeeList,
    EmployeeOut,
    EmployeeUpdate,
    InvitationAccept,
    InvitationCreate,
    InvitationCreated,
    InvitationList,
    InvitationOut,
    InvitationPreview,
    MarkRead,
    MergeRequest,
    MyAvailability,
    MyWeek,
    NotificationList,
    OpenShiftList,
    PositionCreate,
    PositionList,
    PositionOut,
    PositionUpdate,
    PublishOut,
    PublishRequest,
    RequestsOut,
    ScheduleOut,
    ShiftCoverageDecision,
    ShiftCoverageRequestIn,
    ShiftCoverageRequestList,
    ShiftCoverageRequestOut,
    ShiftIn,
    ShiftOut,
    ShiftPatch,
    ShiftTradeCreate,
    ShiftTradeDecision,
    ShiftTradeList,
    ShiftTradeManagerDecision,
    ShiftTradeOut,
    StationCreate,
    StationList,
    StationOut,
    StationUpdate,
    TimeOffIn,
    TimeOffList,
    TimeOffOut,
    UnreadCount,
)

router = APIRouter()
team = APIRouter(prefix="/v1/team", tags=["team"])
public = APIRouter(prefix="/v1/invitations", tags=["team"])
# Any signed-in role, including employees.
mine = APIRouter(prefix="/v1", tags=["team"])

LocationId = Annotated[uuid.UUID, Query()]
WeekStart = Annotated[date, Query()]


# ---------------------------------------------------------------- employees


@team.get("/employees", response_model=EmployeeList)
async def list_employees(
    ctx: StaffDep,
    db: OrgSession,
    location_id: uuid.UUID | None = None,
    include_inactive: bool = False,
) -> EmployeeList:
    scope = await service.scope_for(ctx)
    return EmployeeList(
        data=await service.list_employees(
            db, scope, location_id=location_id, include_inactive=include_inactive
        )
    )


@team.post("/employees", status_code=201, response_model=EmployeeOut)
async def create_employee(body: EmployeeCreate, ctx: StaffDep, db: OrgSession) -> EmployeeOut:
    return await service.create_employee(db, await service.scope_for(ctx), body)


@team.patch("/employees/{id}", response_model=EmployeeOut)
async def update_employee(
    id: uuid.UUID, body: EmployeeUpdate, ctx: StaffDep, db: OrgSession
) -> EmployeeOut:
    return await service.update_employee(db, await service.scope_for(ctx), id, body)


@team.post("/employees/{id}/merge", response_model=EmployeeOut)
async def merge_employee(
    id: uuid.UUID, body: MergeRequest, ctx: StaffDep, db: OrgSession
) -> EmployeeOut:
    return await service.merge_employees(db, await service.scope_for(ctx), id, body.into_id)


# ---------------------------------------------------------------- positions


@team.get("/positions", response_model=PositionList)
async def list_positions(
    ctx: StaffDep, db: OrgSession, include_archived: bool = False
) -> PositionList:
    rows = await service.list_positions(db, ctx.org_id, include_archived=include_archived)
    return PositionList(data=[PositionOut.model_validate(r) for r in rows])


@team.post("/positions", status_code=201, response_model=PositionOut)
async def create_position(body: PositionCreate, ctx: StaffDep, db: OrgSession) -> PositionOut:
    pos = await service.create_position(db, await service.scope_for(ctx), body)
    return PositionOut.model_validate(pos)


@team.patch("/positions/{id}", response_model=PositionOut)
async def update_position(
    id: uuid.UUID, body: PositionUpdate, ctx: StaffDep, db: OrgSession
) -> PositionOut:
    pos = await service.update_position(db, await service.scope_for(ctx), id, body)
    return PositionOut.model_validate(pos)


# ---------------------------------------------------------------- stations


@team.get("/stations", response_model=StationList)
async def list_stations(
    ctx: StaffDep, db: OrgSession, location_id: LocationId, include_archived: bool = False
) -> StationList:
    rows = await service.list_stations(
        db, await service.scope_for(ctx), location_id, include_archived=include_archived
    )
    return StationList(data=[StationOut.model_validate(r) for r in rows])


@team.post("/stations", status_code=201, response_model=StationOut)
async def create_station(body: StationCreate, ctx: StaffDep, db: OrgSession) -> StationOut:
    station = await service.create_station(db, await service.scope_for(ctx), body)
    return StationOut.model_validate(station)


@team.patch("/stations/{id}", response_model=StationOut)
async def update_station(
    id: uuid.UUID, body: StationUpdate, ctx: StaffDep, db: OrgSession
) -> StationOut:
    station = await service.update_station(db, await service.scope_for(ctx), id, body)
    return StationOut.model_validate(station)


# ---------------------------------------------------------------- schedule


@team.get("/schedule", response_model=ScheduleOut)
async def read_schedule(
    ctx: StaffDep, db: OrgSession, location_id: LocationId, week_start: WeekStart
) -> ScheduleOut:
    return await schedule.read_schedule(db, await service.scope_for(ctx), location_id, week_start)


@team.post("/schedule/shifts", status_code=201, response_model=ShiftOut)
async def create_shift(
    body: ShiftIn, ctx: StaffDep, db: OrgSession, location_id: LocationId, week_start: WeekStart
) -> ShiftOut:
    scope = await service.scope_for(ctx)
    return await schedule.create_shift(db, scope, location_id, week_start, body)


@team.patch("/shifts/{id}", response_model=ShiftOut)
async def update_shift(id: uuid.UUID, body: ShiftPatch, ctx: StaffDep, db: OrgSession) -> ShiftOut:
    return await schedule.update_shift(db, await service.scope_for(ctx), id, body)


@team.delete("/shifts/{id}", status_code=204)
async def delete_shift(id: uuid.UUID, ctx: StaffDep, db: OrgSession) -> Response:
    await schedule.delete_shift(db, await service.scope_for(ctx), id)
    return Response(status_code=204)


@team.post("/schedule/copy", response_model=CopyOut)
async def copy_week(
    body: CopyRequest, ctx: StaffDep, db: OrgSession, location_id: LocationId, week_start: WeekStart
) -> CopyOut:
    scope = await service.scope_for(ctx)
    return await schedule.copy_week(db, scope, location_id, week_start, body.from_week_start)


@team.post("/schedule/publish", response_model=PublishOut)
async def publish(
    body: PublishRequest,
    ctx: StaffDep,
    db: OrgSession,
    location_id: LocationId,
    week_start: WeekStart,
) -> PublishOut:
    return await schedule.publish(
        db,
        await service.scope_for(ctx),
        location_id,
        week_start,
        expected_version=body.expected_version,
        acknowledge_conflicts=body.acknowledge_conflicts,
    )


# ---------------------------------------------------------------- attendance


@team.get("/attendance", response_model=AttendanceOut)
async def read_attendance(
    ctx: StaffDep,
    db: OrgSession,
    location_id: LocationId,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
) -> AttendanceOut:
    """Published shifts vs imported timecards per business day (default: today)."""
    return await attendance.attendance(db, await service.scope_for(ctx), location_id, from_, to)


# ---------------------------------------------------------------- requests inbox


@team.get("/requests", response_model=RequestsOut)
async def list_requests(
    ctx: StaffDep,
    db: OrgSession,
    location_id: uuid.UUID | None = None,
    status: Literal["pending", "history"] = "pending",
) -> RequestsOut:
    return await availability.list_requests(db, await service.scope_for(ctx), location_id, status)


@team.get("/shift-coverage-requests", response_model=ShiftCoverageRequestList)
async def list_shift_coverage_requests(
    ctx: StaffDep, db: OrgSession, location_id: LocationId
) -> ShiftCoverageRequestList:
    return ShiftCoverageRequestList(
        data=await coverage.list_coverage_requests(db, await service.scope_for(ctx), location_id)
    )


@team.post("/shift-coverage-requests/{id}/decide", response_model=ShiftCoverageRequestOut)
async def decide_shift_coverage_request(
    id: uuid.UUID, body: ShiftCoverageDecision, ctx: StaffDep, db: OrgSession
) -> ShiftCoverageRequestOut:
    return await coverage.decide_coverage_request(
        db, await service.scope_for(ctx), id, body.decision, body.note
    )


@team.get("/shift-trades", response_model=ShiftTradeList)
async def list_shift_trades(
    ctx: StaffDep, db: OrgSession, location_id: LocationId
) -> ShiftTradeList:
    return ShiftTradeList(
        data=await trades.list_for_manager(db, await service.scope_for(ctx), location_id)
    )


@team.post("/shift-trades/{id}/decide", response_model=ShiftTradeOut)
async def decide_shift_trade(
    id: uuid.UUID, body: ShiftTradeManagerDecision, ctx: StaffDep, db: OrgSession
) -> ShiftTradeOut:
    return await trades.decide(db, await service.scope_for(ctx), id, body.decision, body.note)


@team.post("/time-off/{id}/decide", response_model=TimeOffOut)
async def decide_time_off(
    id: uuid.UUID, body: Decision, ctx: StaffDep, db: OrgSession
) -> TimeOffOut:
    scope = await service.scope_for(ctx)
    return await availability.decide_time_off(db, scope, id, body.decision, body.note)


@team.post("/availability/{id}/decide", response_model=AvailabilityOut)
async def decide_availability(
    id: uuid.UUID, body: Decision, ctx: StaffDep, db: OrgSession
) -> AvailabilityOut:
    scope = await service.scope_for(ctx)
    return await availability.decide_availability(db, scope, id, body.decision, body.note)


# ---------------------------------------------------------------- invitations


@team.post("/invitations", status_code=201, response_model=InvitationCreated)
async def create_invitation(
    body: InvitationCreate, ctx: StaffDep, db: OrgSession
) -> InvitationCreated:
    inv, token = await invitations.create_invitation(db, await service.scope_for(ctx), body)
    return InvitationCreated(invitation=inv, accept_url=invitations.accept_url(token))


@team.get("/invitations", response_model=InvitationList)
async def list_invitations(
    ctx: StaffDep,
    db: OrgSession,
    status: Literal["pending", "accepted", "revoked", "expired"] | None = None,
) -> InvitationList:
    rows = await invitations.list_invitations(db, await service.scope_for(ctx), status)
    return InvitationList(data=rows)


@team.post("/invitations/{id}/revoke", response_model=InvitationOut)
async def revoke_invitation(id: uuid.UUID, ctx: StaffDep, db: OrgSession) -> InvitationOut:
    return await invitations.revoke_invitation(db, await service.scope_for(ctx), id)


@public.get("/{token}", response_model=InvitationPreview)
async def preview_invitation(token: str) -> InvitationPreview:
    """No auth: the token is the credential."""
    return await invitations.preview(token)


@public.post("/{token}/accept", status_code=201, response_model=SessionOut)
async def accept_invitation(
    token: str, body: InvitationAccept, request: Request, response: Response
) -> SessionOut:
    """No auth or CSRF: the single-use token is the credential. Throttled per IP like login."""
    result = await invitations.accept(
        token,
        body.display_name,
        body.password,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    set_session_cookie(response, result.token)
    return SessionOut(
        user=UserOut.model_validate(result.user),
        org=OrgOut.model_validate(result.org),
        role=result.role,
        csrf_token=result.record.csrf_token,
    )


# ---------------------------------------------------------------- self-service


@mine.get("/auth/me", response_model=SessionOut)
async def session_me(ctx: AuthDep, db: OrgSession) -> SessionOut:
    has_employee = (
        await db.scalar(
            select(StaffMember.id).where(
                StaffMember.org_id == ctx.org_id,
                StaffMember.user_id == ctx.user_id,
                StaffMember.merged_into_id.is_(None),
            )
        )
        is not None
    )
    return SessionOut(
        user=UserOut.model_validate(await identity.get_user(db, ctx.user_id)),
        org=OrgOut.model_validate(await identity.get_org(db, ctx.org_id)),
        role=ctx.role,
        csrf_token=ctx.csrf_token,
        has_employee=has_employee,
    )


@mine.get("/me/week", response_model=MyWeek)
async def my_week(ctx: AuthDep, db: OrgSession, week_start: date | None = None) -> MyWeek:
    return await me.my_week(db, ctx.org_id, ctx.user_id, week_start)


@mine.get("/me/open-shifts", response_model=OpenShiftList)
async def list_open_shifts(ctx: AuthDep, db: OrgSession) -> OpenShiftList:
    return OpenShiftList(data=await coverage.open_shifts(db, ctx.org_id, ctx.user_id))


@mine.post("/me/shift-coverage-requests", status_code=201, response_model=ShiftCoverageRequestOut)
async def create_shift_coverage_request(
    body: ShiftCoverageRequestIn, ctx: AuthDep, db: OrgSession
) -> ShiftCoverageRequestOut:
    return await coverage.request_open_shift(db, ctx.org_id, ctx.user_id, body.published_shift_id)


@mine.post(
    "/me/shift-coverage-requests/{id}/cancel",
    response_model=ShiftCoverageRequestOut,
)
async def cancel_shift_coverage_request(
    id: uuid.UUID, ctx: AuthDep, db: OrgSession
) -> ShiftCoverageRequestOut:
    return await coverage.cancel_coverage_request(db, ctx.org_id, ctx.user_id, id)


@mine.get("/me/shift-trades", response_model=ShiftTradeList)
async def list_my_shift_trades(ctx: AuthDep, db: OrgSession) -> ShiftTradeList:
    return ShiftTradeList(data=await trades.list_for_employee(db, ctx.org_id, ctx.user_id))


@mine.post("/me/shift-trades", status_code=201, response_model=ShiftTradeOut)
async def create_shift_trade(body: ShiftTradeCreate, ctx: AuthDep, db: OrgSession) -> ShiftTradeOut:
    return await trades.create(
        db, ctx.org_id, ctx.user_id, body.offered_shift_id, body.requested_shift_id
    )


@mine.post("/me/shift-trades/{id}/respond", response_model=ShiftTradeOut)
async def respond_to_shift_trade(
    id: uuid.UUID, body: ShiftTradeDecision, ctx: AuthDep, db: OrgSession
) -> ShiftTradeOut:
    return await trades.respond(db, ctx.org_id, ctx.user_id, id, body.decision, body.note)


@mine.post("/me/shift-trades/{id}/cancel", response_model=ShiftTradeOut)
async def cancel_shift_trade(id: uuid.UUID, ctx: AuthDep, db: OrgSession) -> ShiftTradeOut:
    return await trades.cancel(db, ctx.org_id, ctx.user_id, id)


@mine.get("/me/availability", response_model=MyAvailability)
async def my_availability(ctx: AuthDep, db: OrgSession) -> MyAvailability:
    return await availability.my_availability(db, ctx.org_id, ctx.user_id)


@mine.put("/me/availability", response_model=AvailabilityOut)
async def submit_availability(
    body: AvailabilityIn, ctx: AuthDep, db: OrgSession
) -> AvailabilityOut:
    return await availability.submit_availability(db, ctx.org_id, ctx.user_id, body)


@mine.get("/me/time-off", response_model=TimeOffList)
async def my_time_off(ctx: AuthDep, db: OrgSession) -> TimeOffList:
    return TimeOffList(data=await availability.my_time_off(db, ctx.org_id, ctx.user_id))


@mine.post("/me/time-off", status_code=201, response_model=TimeOffOut)
async def request_time_off(body: TimeOffIn, ctx: AuthDep, db: OrgSession) -> TimeOffOut:
    return await availability.request_time_off(db, ctx.org_id, ctx.user_id, body)


@mine.post("/me/time-off/{id}/cancel", response_model=TimeOffOut)
async def cancel_time_off(id: uuid.UUID, ctx: AuthDep, db: OrgSession) -> TimeOffOut:
    return await availability.cancel_time_off(db, ctx.org_id, ctx.user_id, id)


@mine.get("/notifications", response_model=NotificationList)
async def list_notifications(
    ctx: AuthDep, db: OrgSession, unread_only: bool = False
) -> NotificationList:
    rows, unread = await notify.list_for(db, ctx.org_id, ctx.user_id, unread_only=unread_only)
    return NotificationList(data=rows, unread_count=unread)


@mine.post("/notifications/read", response_model=UnreadCount)
async def read_notifications(body: MarkRead, ctx: AuthDep, db: OrgSession) -> UnreadCount:
    n = await notify.mark_read(db, ctx.org_id, ctx.user_id, ids=body.ids, all_=body.all)
    return UnreadCount(unread_count=n)


router.include_router(team)
router.include_router(mine)
router.include_router(public)
