"""Integrations HTTP API (docs/api.md, Integrations section)."""

import uuid
from typing import Literal
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.config import get_settings
from app.db import scoped_session
from app.errors import ProblemError, not_found, unavailable, validation_error
from app.integrations import service, temporal
from app.integrations.models import IntegrationConnection
from app.integrations.schemas import (
    AuthorizeOut,
    BackfillRequest,
    ConnectionDetailOut,
    ConnectionList,
    ConnectionOut,
    LinksPatch,
    MappingCandidateList,
    MappingIssueList,
    MappingIssueOut,
    ResolveRequest,
    SyncRequest,
    WorkflowStarted,
)
from app.security.sessions import OrgSession, StaffDep, require_role

log = structlog.get_logger()
router = APIRouter(prefix="/v1", tags=["integrations"])
_ADMINS = {"owner", "admin"}
MAX_BACKFILL_DAYS = 800


@router.post("/integrations/square/authorize", response_model=AuthorizeOut)
async def authorize(ctx: StaffDep, db: OrgSession) -> AuthorizeOut:
    require_role(ctx, _ADMINS)
    return AuthorizeOut(authorize_url=await service.start_authorize(db, ctx.org_id, ctx.user_id))


@router.get("/integrations/square/fake-consent", include_in_schema=False)
async def fake_consent(state: str) -> RedirectResponse:
    """Stands in for Square's consent page in fake mode: approves immediately."""
    if get_settings().square_mode != "fake":
        raise not_found()
    if service.verify_state(state) is None:
        raise validation_error({"state": "Invalid or expired state"})
    query = urlencode({"code": "fake-code", "state": state})
    return RedirectResponse(f"{service.redirect_uri()}?{query}", status_code=302)


@router.get("/integrations/square/callback", include_in_schema=False)
async def callback(
    code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    """OAuth return. Authenticated by the signed `state`, not the session cookie."""
    web = get_settings().web_origin.rstrip("/")
    base = f"{web}/app/integrations"
    if error or not code or not state:
        return RedirectResponse(
            f"{base}?error={'access_denied' if error else 'invalid_request'}", 302
        )
    try:
        connection_id = await service.complete_oauth(state, code)
    except ProblemError as exc:
        return RedirectResponse(f"{base}?error={exc.detail or exc.code}", 302)
    except (OSError, RuntimeError) as exc:
        log.error("connect_kickoff_failed", error=str(exc))
        return RedirectResponse(f"{base}?error=unavailable", 302)
    return RedirectResponse(f"{base}?connected={connection_id}", 302)


@router.get("/integrations/connections", response_model=ConnectionList)
async def list_connections(ctx: StaffDep, db: OrgSession) -> ConnectionList:
    rows = await db.scalars(
        select(IntegrationConnection)
        .where(IntegrationConnection.status != "disconnected")
        .order_by(IntegrationConnection.created_at)
    )
    return ConnectionList(data=[await service.connection_out(db, c) for c in rows])


@router.get("/integrations/connections/{connection_id}", response_model=ConnectionDetailOut)
async def get_connection(
    connection_id: uuid.UUID, ctx: StaffDep, db: OrgSession
) -> ConnectionDetailOut:
    conn = await service.get_live_connection(db, connection_id)
    return await service.connection_detail(db, conn)


@router.patch("/integrations/connections/{connection_id}/locations", response_model=ConnectionOut)
async def patch_locations(
    connection_id: uuid.UUID, body: LinksPatch, ctx: StaffDep
) -> ConnectionOut:
    require_role(ctx, _ADMINS)
    newly = await service.patch_links(
        ctx.org_id, connection_id, [(i.external_location_id, i.location_id) for i in body.links]
    )
    if newly:
        try:
            await temporal.start_sync(ctx.org_id, connection_id, deferred=True)
            await service.maybe_start_backfill(ctx.org_id, connection_id)
        except (OSError, RuntimeError) as exc:
            log.error("link_kickoff_failed", error=str(exc))
    async with scoped_session(ctx.org_id) as db:
        conn = await service.get_live_connection(db, connection_id)
        return await service.connection_out(db, conn)


@router.post(
    "/integrations/connections/{connection_id}/sync",
    status_code=202,
    response_model=WorkflowStarted,
)
async def sync_now(
    connection_id: uuid.UUID, ctx: StaffDep, db: OrgSession, body: SyncRequest | None = None
) -> WorkflowStarted:
    require_role(ctx, _ADMINS)
    conn = await service.get_live_connection(db, connection_id)
    if conn.status != "active":
        raise ProblemError(409, "conflict", f"Connection is {conn.status}")
    try:
        wid = await temporal.start_sync(
            ctx.org_id, connection_id, list(body.streams) if body and body.streams else None
        )
    except (OSError, RuntimeError) as exc:
        raise unavailable("Temporal is unreachable") from exc
    return WorkflowStarted(workflow_id=wid)


@router.post(
    "/integrations/connections/{connection_id}/backfill",
    status_code=202,
    response_model=WorkflowStarted,
)
async def backfill(
    connection_id: uuid.UUID, body: BackfillRequest, ctx: StaffDep, db: OrgSession
) -> WorkflowStarted:
    require_role(ctx, _ADMINS)
    if body.from_date > body.to_date:
        raise validation_error({"from": "Must be on or before `to`"})
    if (body.to_date - body.from_date).days >= MAX_BACKFILL_DAYS:
        raise validation_error({"to": f"Range is limited to {MAX_BACKFILL_DAYS} days"})
    conn = await service.get_live_connection(db, connection_id)
    if conn.status != "active":
        raise ProblemError(409, "conflict", f"Connection is {conn.status}")
    try:
        wid = await temporal.start_backfill(ctx.org_id, connection_id, body.from_date, body.to_date)
    except (OSError, RuntimeError) as exc:
        raise unavailable("Temporal is unreachable") from exc
    except WorkflowAlreadyStartedError as exc:
        raise ProblemError(409, "conflict", "A backfill is already running") from exc
    return WorkflowStarted(workflow_id=wid)


@router.delete("/integrations/connections/{connection_id}", status_code=204)
async def disconnect(connection_id: uuid.UUID, ctx: StaffDep) -> Response:
    require_role(ctx, _ADMINS)
    await service.disconnect(ctx.org_id, connection_id)
    return Response(status_code=204)


@router.get("/integrations/mapping-issues", response_model=MappingIssueList)
async def mapping_issues(
    ctx: StaffDep, db: OrgSession, status: Literal["open", "resolved"] = Query("open")
) -> MappingIssueList:
    return MappingIssueList(data=await service.list_issues(db, status))


@router.get(
    "/integrations/mapping-issues/{issue_id}/candidates", response_model=MappingCandidateList
)
async def mapping_candidates(
    issue_id: uuid.UUID, ctx: StaffDep, db: OrgSession
) -> MappingCandidateList:
    require_role(ctx, _ADMINS)
    return MappingCandidateList(data=await service.mapping_candidates(db, issue_id))


@router.post("/integrations/mapping-issues/{issue_id}/resolve", response_model=MappingIssueOut)
async def resolve_issue(
    issue_id: uuid.UUID, body: ResolveRequest, ctx: StaffDep, db: OrgSession
) -> MappingIssueOut:
    require_role(ctx, _ADMINS)
    return await service.resolve_issue(
        db, ctx.org_id, issue_id, ctx.user_id, entity_id=body.entity_id
    )


@router.post("/webhooks/square", include_in_schema=True)
async def square_webhook(request: Request) -> dict[str, str]:
    """Unauthenticated; HMAC-verified. Sandbox mode only."""
    body = await request.body()
    await service.handle_webhook(dict(request.headers), body)
    return {"status": "ok"}
