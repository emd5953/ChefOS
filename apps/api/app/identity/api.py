import uuid

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.identity import service
from app.identity.schemas import (
    LocationCreate,
    LocationList,
    LocationOut,
    LocationUpdate,
    LoginRequest,
    OrgOut,
    OrgUpdate,
    SessionOut,
    SignupOut,
    SignupRequest,
    UserOut,
)
from app.security.sessions import (
    AuthDep,
    OrgSession,
    clear_session_cookie,
    require_role,
    revoke_session,
    set_session_cookie,
)
from app.team.models import StaffMember

router = APIRouter(prefix="/v1", tags=["identity"])

_ADMINS = {"owner", "admin"}


def _client(request: Request) -> tuple[str | None, str | None]:
    return request.headers.get("user-agent"), request.client.host if request.client else None


@router.post("/auth/signup", status_code=201, response_model=SignupOut)
async def signup(body: SignupRequest, request: Request, response: Response) -> SignupOut:
    user_agent, ip = _client(request)
    reg, token, record = await service.signup(body, user_agent=user_agent, ip=ip)
    set_session_cookie(response, token)
    return SignupOut(
        user=UserOut.model_validate(reg.user),
        org=OrgOut.model_validate(reg.org),
        location=LocationOut.model_validate(reg.location),
        role="owner",
        csrf_token=record.csrf_token,
    )


@router.post("/auth/login", response_model=SessionOut)
async def login(body: LoginRequest, request: Request, response: Response) -> SessionOut:
    user_agent, ip = _client(request)
    result = await service.login(body.email, body.password, user_agent=user_agent, ip=ip)
    set_session_cookie(response, result.token)
    return SessionOut(
        user=UserOut.model_validate(result.user),
        org=OrgOut.model_validate(result.org),
        role=result.role,
        csrf_token=result.record.csrf_token,
    )


@router.post("/auth/logout", status_code=204)
async def logout(ctx: AuthDep, db: OrgSession) -> Response:
    await service.logout_audit(db, ctx.org_id, ctx.user_id)
    await revoke_session(ctx.session_id)
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/auth/me", response_model=SessionOut)
async def me(ctx: AuthDep, db: OrgSession) -> SessionOut:
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
        user=UserOut.model_validate(await service.get_user(db, ctx.user_id)),
        org=OrgOut.model_validate(await service.get_org(db, ctx.org_id)),
        role=ctx.role,
        csrf_token=ctx.csrf_token,
        has_employee=has_employee,
    )


@router.get("/org", response_model=OrgOut)
async def get_org(ctx: AuthDep, db: OrgSession) -> OrgOut:
    return OrgOut.model_validate(await service.get_org(db, ctx.org_id))


@router.patch("/org", response_model=OrgOut)
async def patch_org(body: OrgUpdate, ctx: AuthDep, db: OrgSession) -> OrgOut:
    require_role(ctx, _ADMINS)
    return OrgOut.model_validate(await service.update_org(db, ctx.org_id, ctx.user_id, body))


@router.get("/locations", response_model=LocationList)
async def list_locations(
    ctx: AuthDep, db: OrgSession, include_archived: bool = False
) -> LocationList:
    rows = await service.list_locations(db, ctx.org_id, include_archived=include_archived)
    return LocationList(data=[LocationOut.model_validate(r) for r in rows])


@router.post("/locations", status_code=201, response_model=LocationOut)
async def create_location(body: LocationCreate, ctx: AuthDep, db: OrgSession) -> LocationOut:
    require_role(ctx, _ADMINS)
    return LocationOut.model_validate(
        await service.create_location(db, ctx.org_id, ctx.user_id, body)
    )


@router.patch("/locations/{location_id}", response_model=LocationOut)
async def patch_location(
    location_id: uuid.UUID, body: LocationUpdate, ctx: AuthDep, db: OrgSession
) -> LocationOut:
    require_role(ctx, _ADMINS)
    return LocationOut.model_validate(
        await service.update_location(db, ctx.org_id, ctx.user_id, location_id, body)
    )


@router.post("/locations/{location_id}/archive", response_model=LocationOut)
async def archive_location(location_id: uuid.UUID, ctx: AuthDep, db: OrgSession) -> LocationOut:
    require_role(ctx, _ADMINS)
    return LocationOut.model_validate(
        await service.archive_location(db, ctx.org_id, ctx.user_id, location_id)
    )
