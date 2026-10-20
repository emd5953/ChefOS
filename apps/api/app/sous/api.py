"""Sous API (docs/api.md, Sous section)."""

import time
import uuid
from collections import defaultdict, deque
from typing import Annotated

from fastapi import APIRouter, Depends

from app.commerce import service
from app.errors import ProblemError
from app.security.sessions import AuthDep, OrgSession
from app.sous import orchestrator, prompt
from app.sous.llm import LLMProvider, get_provider
from app.sous.schemas import AskIn, SousAnswer, StatusOut

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


@router.post("/ask", response_model=SousAnswer)
async def ask(ctx: AuthDep, db: OrgSession, body: AskIn, provider: Provider) -> SousAnswer:
    check_rate_limit(ctx.user_id)
    loc = await service.get_location(db, await ctx.allowed_location_ids(), body.location_id)
    return await orchestrator.ask(db, ctx, loc, body, provider)


@router.get("/status", response_model=StatusOut)
async def status(_: AuthDep, provider: Provider) -> StatusOut:
    health = await provider.health()
    return StatusOut(
        provider=provider.name,
        model=provider.model,
        available=health.reachable and health.model_pulled,
        model_pulled=health.model_pulled,
        prompt_version=prompt.PROMPT_VERSION,
    )
