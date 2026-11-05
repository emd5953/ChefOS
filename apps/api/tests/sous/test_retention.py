"""Retention boundaries use real PostgreSQL, private RLS, and the production purge function."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import get_engine, purge_expired_sous_conversations, scoped_session
from app.errors import ProblemError
from app.sous import conversations, retention
from app.sous.agent.llm import FakeProvider
from app.sous.models import Conversation, Turn
from app.sous.schemas import AskIn
from tests.integrations.conftest import Connect, Connected
from tests.sous.test_api import UseProvider, body
from tests.sous.test_conversations import answer
from tests.sous.test_resilience import ctx_for


async def expire(bare: Connected, cid: object, *, lease: str = "NULL") -> None:
    async with conversations.session(ctx_for(bare)) as db:
        await db.execute(
            text(
                "UPDATE sous_conversations SET expires_at=now()-interval '1 second', "
                f"lease_until={lease} WHERE id=:id"
            ),
            {"id": cid},
        )


async def test_fixed_lifetime_promotion_and_replay_do_not_extend_retention(bare: Connected) -> None:
    ctx, req = ctx_for(bare), AskIn(**body(bare))
    r = await conversations.begin(ctx, req)
    async with conversations.session(ctx) as db:
        conv = await db.get_one(Conversation, r.conversation_id)
        created = conv.created_at
        assert conv.expires_at == created + timedelta(hours=24)
    await conversations.complete(ctx, r, answer())
    replay = await conversations.begin(ctx, req)
    assert replay.replay
    followup = await conversations.begin(
        ctx, AskIn(**body(bare, conversation_id=r.conversation_id))
    )
    await conversations.complete(ctx, followup, answer())
    async with conversations.session(ctx) as db:
        assert (
            await db.get_one(Conversation, r.conversation_id)
        ).expires_at == created + timedelta(days=30)


async def test_expired_history_is_not_replayed_or_sent_to_model(bare: Connected) -> None:
    ctx, req = ctx_for(bare), AskIn(**body(bare))
    r = await conversations.begin(ctx, req)
    await conversations.complete(ctx, r, answer())
    await expire(bare, r.conversation_id)
    assert not await conversations.has_replay(ctx, req)
    for request in (req, AskIn(**body(bare, conversation_id=r.conversation_id))):
        with pytest.raises(ProblemError) as exc:
            await conversations.begin(ctx, request)
        assert exc.value.status == 410 and exc.value.code == "sous_conversation_expired"
    # Clear remains available before cleanup runs.
    await conversations.remove(ctx, r.conversation_id)


async def test_purge_cascades_across_owners_but_preserves_live_and_recent(
    bare: Connected, connect: Connect, owner_engine: AsyncEngine
) -> None:
    other = await connect(complete=False)
    old = []
    for acct in (bare, other):
        ctx = ctx_for(acct)
        r = await conversations.begin(ctx, AskIn(**body(acct)))
        await conversations.complete(ctx, r, answer())
        await expire(acct, r.conversation_id)
        old.append(r)
    ctx = ctx_for(bare)
    recent = await conversations.begin(ctx, AskIn(**body(bare)))
    active = await conversations.begin(ctx, AskIn(**body(bare)))
    await expire(bare, active.conversation_id, lease="now()+interval '1 minute'")
    # No private history can be enumerated through an unscoped or org-only session.
    async with scoped_session(bare.org_id) as db:
        assert await db.scalar(select(Conversation.id)) is None
    assert await purge_expired_sous_conversations(1) == 1
    assert await purge_expired_sous_conversations(1) == 1
    assert await purge_expired_sous_conversations(1) == 0
    async with owner_engine.connect() as db:
        for r in old:
            assert (
                await db.scalar(select(Turn.id).where(Turn.conversation_id == r.conversation_id))
                is None
            )
        for r in (recent, active):
            assert await db.scalar(
                select(Conversation.id).where(Conversation.id == r.conversation_id)
            )
    # In-flight completion is allowed, but it doesn't extend an already completed history.
    await conversations.complete(ctx, active, answer())
    await expire(bare, recent.conversation_id, lease="now()-interval '1 second'")
    assert await purge_expired_sous_conversations() == 1
    with pytest.raises(ProblemError, match="replaced"):
        await conversations.complete(ctx, recent, answer())
    await conversations.release(ctx, recent)  # safe after cascade deletion


async def test_purge_skips_locked_conversations_and_retries_next_sweep(bare: Connected) -> None:
    ctx = ctx_for(bare)
    r = await conversations.begin(ctx, AskIn(**body(bare)))
    await expire(bare, r.conversation_id)
    async with conversations.session(ctx) as db:
        await db.execute(
            select(Conversation).where(Conversation.id == r.conversation_id).with_for_update()
        )
        assert await asyncio.wait_for(purge_expired_sous_conversations(), 2) == 0
    assert await purge_expired_sous_conversations() == 1


@pytest.mark.parametrize("size", [0, -1, 1001, None])
async def test_database_rejects_unbounded_cleanup(size: int | None) -> None:
    async with get_engine().begin() as db:
        with pytest.raises(DBAPIError, match="between 1 and 1000"):
            await db.execute(
                text("SELECT public.purge_expired_sous_conversations(:n)"), {"n": size}
            )


async def test_cleanup_stops_at_batch_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def purge(size: int) -> int:
        calls.append(size)
        return size

    monkeypatch.setattr(retention, "purge_expired_sous_conversations", purge)
    assert await retention.cleanup() == retention.BATCH_SIZE * retention.MAX_BATCHES
    assert len(calls) == retention.MAX_BATCHES


async def test_maintenance_survives_failure_retries_and_joins_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retried = asyncio.Event()
    cancelled = asyncio.Event()
    calls = 0

    async def cleanup() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database temporarily unavailable")
        retried.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return 0

    monkeypatch.setattr(retention, "cleanup", cleanup)
    monkeypatch.setattr(retention, "INTERVAL_SECONDS", 0)
    async with retention.run_cleanup():
        await asyncio.wait_for(retried.wait(), 2)
    assert cancelled.is_set() and calls == 2


@pytest.mark.parametrize("streaming", [False, True])
async def test_expiry_is_visible_over_http_and_clear_still_works(
    bare: Connected, use_provider: UseProvider, streaming: bool
) -> None:
    from tests.sous.test_api import post, problem
    from tests.sous.test_resilience import parse_sse, stream

    fake = FakeProvider([])
    use_provider(fake)
    ctx = ctx_for(bare)
    r = await conversations.begin(ctx, AskIn(**body(bare)))
    await conversations.release(ctx, r)
    await expire(bare, r.conversation_id)
    payload = body(bare, conversation_id=str(r.conversation_id))
    if streaming:
        events = parse_sse((await stream(bare, payload)).text)
        assert len(events) == 1 and events[0][0] == "error"
        assert events[0][1]["code"] == "sous_conversation_expired"
        assert events[0][1]["status"] == 410
    else:
        problem(await post(bare, payload), 410, "sous_conversation_expired")
    cleared = await bare.client.delete(
        f"/v1/sous/conversations/{r.conversation_id}", headers=bare.headers
    )
    assert cleared.status_code == 204
    problem(await post(bare, payload), 404, "not_found")
    assert fake.calls == []


async def test_completion_at_retention_boundary_does_not_renew_history(bare: Connected) -> None:
    ctx = ctx_for(bare)
    first = await conversations.begin(ctx, AskIn(**body(bare)))
    await conversations.complete(ctx, first, answer())
    active = await conversations.begin(
        ctx, AskIn(**body(bare, conversation_id=first.conversation_id))
    )
    await expire(bare, first.conversation_id, lease="now()+interval '1 minute'")
    assert await purge_expired_sous_conversations() == 0
    await conversations.complete(ctx, active, answer())
    assert await purge_expired_sous_conversations() == 1


async def test_definer_has_locked_path_and_no_public_execute(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as db:
        row = (
            await db.execute(
                text("""
            SELECT prosecdef, proconfig, pg_get_userbyid(proowner) AS owner,
                   EXISTS (SELECT 1 FROM aclexplode(proacl) WHERE grantee=0) AS public_access
            FROM pg_proc WHERE oid='public.purge_expired_sous_conversations(integer)'::regprocedure
        """)
            )
        ).one()
        assert row.prosecdef and row.owner == "chefos_owner"
        assert row.proconfig == ["search_path=pg_catalog"] and not row.public_access
