"""Sous API (docs/api.md, Sous section)."""

import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.commerce import service
from app.db import scoped_session
from app.errors import ProblemError
from app.identity.models import Location
from app.integrations.providers.square.business_date import business_date
from app.security.sessions import AuthContext, StaffDep
from app.sous import fallback, orchestrator, prompt
from app.sous import service as briefings
from app.sous.llm import LLMProvider, get_provider, sous_unavailable
from app.sous.schemas import (
    AskIn,
    BriefingOut,
    ErrorEvent,
    Heartbeat,
    SousAnswer,
    SousEvent,
    StatusEvent,
    StatusOut,
    ToolEvent,
)

log = structlog.get_logger()

router = APIRouter(prefix="/v1/sous", tags=["sous"])

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
async def ask(ctx: StaffDep, body: AskIn, provider: Provider) -> SousAnswer:
    check_rate_limit(ctx.user_id)
    loc = await _location(ctx, body.location_id)
    return await orchestrator.ask(ctx, loc, body, provider)


_EVENT_NAMES: dict[type, str] = {
    StatusEvent: "status",
    ToolEvent: "tool",
    SousAnswer: "answer",
    ErrorEvent: "error",
}


def sse(event: SousEvent | ErrorEvent) -> str:
    if isinstance(event, Heartbeat):
        return ": ping\n\n"
    return f"event: {_EVENT_NAMES[type(event)]}\ndata: {event.model_dump_json(by_alias=True)}\n\n"


async def _stream(events: AsyncIterator[SousEvent]) -> AsyncIterator[str]:
    try:
        async for event in events:
            yield sse(event)
    except ProblemError as exc:
        yield sse(ErrorEvent(status=exc.status, code=exc.code, detail=exc.detail or exc.title))
    except Exception:
        log.exception("sous_stream_error")
        yield sse(
            ErrorEvent(status=500, code="internal_error", detail="Sous hit an unexpected error.")
        )


@router.post("/ask/stream", response_class=StreamingResponse)
async def ask_stream(ctx: StaffDep, body: AskIn, provider: Provider) -> StreamingResponse:
    """Server-sent events: status, tool, then answer or error (docs/specs/sous.md, v1.1)."""
    check_rate_limit(ctx.user_id)
    loc = await _location(ctx, body.location_id)
    breaker = provider.breaker
    if breaker is not None and breaker.state == "open":
        # Fail before the stream starts when nothing could answer.
        today = business_date(datetime.now(UTC), loc.timezone, loc.business_day_start)
        if fallback.match(body.question, today) is None:
            raise sous_unavailable()
    return StreamingResponse(
        _stream(orchestrator.events(ctx, loc, body, provider)),
        media_type="text/event-stream",
        # no-transform stops gzip in front of us (Next's dev proxy, CDNs) buffering the events.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


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
