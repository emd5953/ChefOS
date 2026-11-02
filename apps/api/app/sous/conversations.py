"""Short transactions for history, replay and fenced turn execution.

A lease outlives the orchestrator deadline. A crashed worker can be replaced after expiry;
its attempt token can never commit over a replacement. Failed turns are not model history.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import scoped_session, set_scope
from app.errors import ProblemError
from app.security.sessions import AuthContext
from app.sous.models import Conversation, Turn
from app.sous.schemas import AskIn, HistoryTurn, SousAnswer

LEASE_SECONDS = 180
HISTORY_PAIRS = 5
MAX_TURNS = 200


@asynccontextmanager
async def session(ctx: AuthContext) -> AsyncIterator[AsyncSession]:
    async with scoped_session(ctx.org_id) as db:
        await set_scope(db, user_id=ctx.user_id)
        yield db


def conflict(code: str, detail: str) -> ProblemError:
    return ProblemError(409, code, detail)


@dataclass(frozen=True)
class Reservation:
    conversation_id: uuid.UUID
    turn_id: uuid.UUID
    attempt_id: uuid.UUID
    history: list[HistoryTurn]
    replay: SousAnswer | None = None


async def begin(ctx: AuthContext, req: AskIn) -> Reservation:
    async with session(ctx) as db:
        # Serialize identical requests even when the client hasn't received a conversation ID.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"sous:{ctx.org_id}:{ctx.user_id}:{req.turn_id}"},
        )
        turn = await db.scalar(select(Turn).where(Turn.client_turn_id == req.turn_id))
        cid = turn.conversation_id if turn else req.conversation_id
        if turn and (
            turn.question != req.question or (req.conversation_id and cid != req.conversation_id)
        ):
            raise conflict(
                "sous_turn_conflict", "This turn ID was already used for another request."
            )
        conv = (
            await db.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
            if cid
            else None
        )
        if cid and (conv is None or conv.location_id != req.location_id):
            raise ProblemError(404, "not_found", "Conversation not found.")
        if conv is None:
            conv = Conversation(org_id=ctx.org_id, user_id=ctx.user_id, location_id=req.location_id)
            db.add(conv)
            await db.flush()
        now = await db.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        if conv.expires_at <= now:
            raise ProblemError(
                410,
                "sous_conversation_expired",
                "This conversation has expired. Clear it to start a new conversation.",
            )
        if turn:
            # The active worker may have committed while we waited for the conversation lock.
            await db.refresh(turn)
        if turn and turn.answer is not None:
            return Reservation(
                conv.id, req.turn_id, turn.attempt_id, [], SousAnswer.model_validate(turn.answer)
            )
        if conv.active_attempt_id and conv.lease_until and conv.lease_until > now:
            raise conflict(
                "sous_turn_running",
                "A question is still running in this conversation. Try again shortly.",
            )
        if turn and turn.parent_version != conv.version:
            raise conflict(
                "sous_turn_stale", "This conversation has moved on. Send a new question."
            )
        if conv.version >= MAX_TURNS:
            raise conflict("sous_conversation_full", "Start a new conversation to continue.")
        attempt = uuid.uuid4()
        conv.active_attempt_id, conv.lease_until = attempt, now + timedelta(seconds=LEASE_SECONDS)
        if turn:
            turn.attempt_id, turn.state = attempt, "running"
        else:
            turn = Turn(
                org_id=ctx.org_id,
                user_id=ctx.user_id,
                conversation_id=conv.id,
                client_turn_id=req.turn_id,
                question=req.question,
                parent_version=conv.version,
                attempt_id=attempt,
                state="running",
            )
            db.add(turn)
        prior = (
            await db.scalars(
                select(Turn)
                .where(Turn.conversation_id == conv.id, Turn.state == "completed")
                .order_by(Turn.parent_version.desc())
                .limit(HISTORY_PAIRS)
            )
        ).all()
        history: list[HistoryTurn] = []
        for previous in reversed(prior):
            assert previous.answer is not None
            history.extend(
                [
                    HistoryTurn(role="user", content=previous.question),
                    HistoryTurn(role="assistant", content=str(previous.answer["answer"])[:2000]),
                ]
            )
        return Reservation(conv.id, req.turn_id, attempt, history)


async def complete(ctx: AuthContext, reservation: Reservation, answer: SousAnswer) -> SousAnswer:
    async with session(ctx) as db:
        conv = await db.scalar(
            select(Conversation)
            .where(Conversation.id == reservation.conversation_id)
            .with_for_update()
        )
        if conv is None or conv.active_attempt_id != reservation.attempt_id:
            raise conflict(
                "sous_turn_stale", "This question was replaced or its conversation was cleared."
            )
        turn = (
            await db.scalars(
                select(Turn).where(
                    Turn.conversation_id == reservation.conversation_id,
                    Turn.client_turn_id == reservation.turn_id,
                    Turn.attempt_id == reservation.attempt_id,
                )
            )
        ).one()
        if conv.version == 0:
            # A fixed lifetime from creation: retries and follow-ups never extend retention.
            conv.expires_at = conv.created_at + timedelta(days=30)
        conv.version += 1
        conv.active_attempt_id = conv.lease_until = None
        answer = answer.model_copy(
            update={
                "conversation_id": conv.id,
                "turn_id": reservation.turn_id,
                "conversation_version": conv.version,
            }
        )
        turn.state, turn.answer = "completed", answer.model_dump(mode="json", by_alias=True)
    return answer


async def release(ctx: AuthContext, reservation: Reservation) -> None:
    async with session(ctx) as db:
        conv = await db.scalar(
            select(Conversation)
            .where(Conversation.id == reservation.conversation_id)
            .with_for_update()
        )
        if conv and conv.active_attempt_id == reservation.attempt_id:
            conv.active_attempt_id = conv.lease_until = None
            turn = await db.scalar(
                select(Turn).where(
                    Turn.conversation_id == reservation.conversation_id,
                    Turn.client_turn_id == reservation.turn_id,
                    Turn.attempt_id == reservation.attempt_id,
                )
            )
            if turn:
                turn.state = "failed"


async def remove(ctx: AuthContext, conversation_id: uuid.UUID) -> uuid.UUID:
    allowed = await ctx.allowed_location_ids()
    async with session(ctx) as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None or conv.location_id not in allowed:
            raise ProblemError(404, "not_found", "Conversation not found.")
        await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
        return conv.id


async def has_replay(ctx: AuthContext, req: AskIn) -> bool:
    """Let a committed retry pass an open provider breaker without reserving new work."""
    async with session(ctx) as db:
        stmt = (
            select(Turn.id)
            .join(Conversation, Conversation.id == Turn.conversation_id)
            .where(
                Turn.client_turn_id == req.turn_id,
                Turn.question == req.question,
                Turn.state == "completed",
                Conversation.location_id == req.location_id,
                Conversation.expires_at > func.statement_timestamp(),
            )
        )
        if req.conversation_id:
            stmt = stmt.where(Conversation.id == req.conversation_id)
        return await db.scalar(stmt) is not None
