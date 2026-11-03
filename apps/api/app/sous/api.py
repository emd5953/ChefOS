"""Sous API (docs/api.md, Sous section)."""

import asyncio
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import UTC, date, datetime
from typing import Annotated

import anyio
import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Send

from app.commerce import service
from app.db import scoped_session
from app.errors import ProblemError
from app.identity.models import Location
from app.integrations.providers.square.business_date import business_date
from app.security.sessions import (
    STAFF_ROLES,
    AuthContext,
    OrgSession,
    StaffDep,
    require_auth,
    require_role,
)
from app.sous import conversations, fallback, orchestrator, prompt, proposals
from app.sous import service as briefings
from app.sous.llm import LLMProvider, get_provider, sous_unavailable
from app.sous.models import ScheduleProposal
from app.sous.schemas import (
    AskIn,
    BriefingOut,
    ConversationEvent,
    ErrorEvent,
    Heartbeat,
    ScheduleProposalDecision,
    ScheduleProposalIn,
    ScheduleProposalOut,
    SousAnswer,
    SousEvent,
    StatusEvent,
    StatusOut,
    ToolEvent,
)

log = structlog.get_logger()

router = APIRouter(prefix="/v1/sous", tags=["sous"])


async def _proposal_out(db: AsyncSession, row: ScheduleProposal) -> ScheduleProposalOut:
    from app.team.models import Position, Schedule, StaffMember

    # week_start is part of the persisted schedule; callers don't choose it at approval time.
    schedule = await db.get(Schedule, row.schedule_id)
    position = await db.get(Position, uuid.UUID(row.payload["position_id"]))
    employee = (
        await db.get(StaffMember, uuid.UUID(row.payload["employee_id"]))
        if row.payload.get("employee_id")
        else None
    )
    return ScheduleProposalOut(
        id=row.id,
        location_id=row.location_id,
        schedule_id=row.schedule_id,
        week_start=schedule.week_start if schedule else date.min,
        expected_version=row.expected_version,
        payload=row.payload,
        position_name=position.name if position else "Position",
        employee_name=employee.display_name if employee else None,
        status=row.status,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


@router.post("/proposals", status_code=201, response_model=ScheduleProposalOut)
async def create_proposal(
    body: ScheduleProposalIn, ctx: StaffDep, db: OrgSession
) -> ScheduleProposalOut:
    row = await proposals.create(db, ctx, body.location_id, body.week_start, body.shift)
    return await _proposal_out(db, row)


@router.get("/proposals", response_model=list[ScheduleProposalOut])
async def list_proposals(ctx: StaffDep, db: OrgSession) -> list[ScheduleProposalOut]:
    rows = await proposals.list_pending(db, ctx)
    out = []
    for row in rows:
        out.append(await _proposal_out(db, row))
    return out


@router.post("/proposals/{proposal_id}/decision", response_model=ScheduleProposalOut)
async def decide_proposal(
    proposal_id: uuid.UUID, body: ScheduleProposalDecision, ctx: StaffDep, db: OrgSession
) -> ScheduleProposalOut:
    row = await proposals.decide(db, ctx, proposal_id, body.approve)
    return await _proposal_out(db, row)


RATE_LIMIT = 20  # questions per user per window
RATE_WINDOW_SECONDS = 60.0
_asked: defaultdict[uuid.UUID, deque[float]] = defaultdict(deque)


def check_rate_limit(user_id: uuid.UUID) -> None:
    """In-memory sliding window, per process."""
    now = time.monotonic()
    window = _asked[user_id]
    while window and now - window[0] >= RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise ProblemError(
            429,
            "rate_limited",
            f"Sous is limited to {RATE_LIMIT} questions per minute. Try again shortly.",
            title="Too many requests",
        )
    window.append(now)


def provider_dep() -> LLMProvider:
    return get_provider()


Provider = Annotated[LLMProvider, Depends(provider_dep)]


async def _location(ctx: AuthContext, location_id: uuid.UUID) -> Location:
    """Authorize in a short session; questions never hold a connection while the model runs."""
    allowed = await ctx.allowed_location_ids()
    async with scoped_session(ctx.org_id) as db:
        return await service.get_location(db, allowed, location_id)


@router.post("/ask", response_model=SousAnswer)
async def ask(ctx: StaffDep, body: AskIn, provider: Provider, request: Request) -> SousAnswer:
    check_rate_limit(ctx.user_id)
    loc = await _location(ctx, body.location_id)
    async with aclosing(_authorized_events(ctx, loc, body, provider, request)) as events:
        async for event in events:
            if isinstance(event, SousAnswer):
                return event
    raise RuntimeError("Sous finished without an answer")


async def _authorized_events(
    ctx: AuthContext, loc: Location, body: AskIn, provider: LLMProvider, request: Request
) -> AsyncGenerator[SousEvent]:
    reservation = await conversations.begin(ctx, body)
    completed = reservation.replay is not None
    try:
        yield ConversationEvent(
            conversation_id=reservation.conversation_id, turn_id=reservation.turn_id
        )
        if reservation.replay is not None:
            current = await require_auth(request)
            require_role(current, STAFF_ROLES)
            await _location(current, body.location_id)
            yield reservation.replay
            return

        async def authorize_current() -> None:
            current = await require_auth(request)
            require_role(current, STAFF_ROLES)
            await _location(current, body.location_id)

        # Reauthorize before another model round and before committing/releasing the answer.
        async with aclosing(
            orchestrator.events(
                ctx, loc, body, provider, history=reservation.history, authorize=authorize_current
            )
        ) as events:
            async for event in events:
                if isinstance(event, ToolEvent | SousAnswer):
                    current = await require_auth(request)
                    require_role(current, STAFF_ROLES)
                    await _location(current, body.location_id)
                if isinstance(event, SousAnswer):
                    event = await conversations.complete(ctx, reservation, event)
                    completed = True
                yield event
    finally:
        if not completed:
            # Starlette's SSE disconnect cancels an AnyIO scope repeatedly at each await.
            # Shield that scope as well as one-shot asyncio task cancellation until release
            # commits, so Stop/Retry never has to wait out the crashed-worker lease.
            with anyio.CancelScope(shield=True):
                cleanup = asyncio.create_task(conversations.release(ctx, reservation))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise


@router.delete("/conversations/{conversation_id}", status_code=204)
async def clear_conversation(ctx: StaffDep, conversation_id: uuid.UUID) -> Response:
    await conversations.remove(ctx, conversation_id)
    return Response(status_code=204)


_EVENT_NAMES: dict[type, str] = {
    ConversationEvent: "conversation",
    StatusEvent: "status",
    ToolEvent: "tool",
    SousAnswer: "answer",
    ErrorEvent: "error",
}


def sse(event: SousEvent | ErrorEvent) -> str:
    if isinstance(event, Heartbeat):
        return ": ping\n\n"
    return f"event: {_EVENT_NAMES[type(event)]}\ndata: {event.model_dump_json(by_alias=True)}\n\n"


async def _stream(events: AsyncGenerator[SousEvent]) -> AsyncGenerator[str]:
    try:
        async with aclosing(events):
            async for event in events:
                yield sse(event)
    except ProblemError as exc:
        yield sse(ErrorEvent(status=exc.status, code=exc.code, detail=exc.detail or exc.title))
    except Exception:
        log.exception("sous_stream_error")
        yield sse(
            ErrorEvent(status=500, code="internal_error", detail="Sous hit an unexpected error.")
        )


class SousStreamingResponse(StreamingResponse):
    """Close the generator on send failure as well as cancellation during its next event."""

    def __init__(self, events: AsyncGenerator[str]) -> None:
        self.events = events
        super().__init__(
            events,
            media_type="text/event-stream",
            # no-transform prevents Next/CDNs from buffering progress behind gzip.
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        finally:
            # ASGI 2.4 reports disconnect as a send error while our generator is suspended
            # at a yield; Starlette does not itself close that iterator.
            with anyio.CancelScope(shield=True):
                await self.events.aclose()


@router.post("/ask/stream", response_class=StreamingResponse)
async def ask_stream(
    ctx: StaffDep, body: AskIn, provider: Provider, request: Request
) -> StreamingResponse:
    """Server-sent events: status, tool, then answer or error (docs/specs/sous.md, v1.1)."""
    check_rate_limit(ctx.user_id)
    loc = await _location(ctx, body.location_id)
    breaker = provider.breaker
    if breaker is not None and breaker.state == "open":
        # Fail before the stream starts when nothing could answer.
        today = business_date(datetime.now(UTC), loc.timezone, loc.business_day_start)
        if fallback.match(body.question, today) is None and not await conversations.has_replay(
            ctx, body
        ):
            raise sous_unavailable()
    return SousStreamingResponse(_stream(_authorized_events(ctx, loc, body, provider, request)))


@router.get("/status", response_model=StatusOut)
async def status(_: StaffDep, provider: Provider) -> StatusOut:
    health = await provider.health()
    return StatusOut(
        provider=provider.name,
        model=provider.model,
        available=health.reachable and health.model_pulled,
        model_pulled=health.model_pulled,
        prompt_version=prompt.PROMPT_VERSION,
        breaker=provider.breaker.state if provider.breaker else "closed",
    )


@router.get("/briefing", response_model=BriefingOut)
async def briefing(
    ctx: StaffDep,
    location_id: Annotated[uuid.UUID, Query()],
    day: Annotated[date | None, Query(alias="date")] = None,
) -> BriefingOut:
    """Deterministic daily briefing (SOUS-2). Never calls the model, so it works offline."""
    allowed = await ctx.allowed_location_ids()
    async with scoped_session(ctx.org_id) as db:
        loc = await service.get_location(db, allowed, location_id)
        return await briefings.briefing(db, ctx.org_id, loc, day)
