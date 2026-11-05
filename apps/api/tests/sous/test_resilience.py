"""Streaming, short sessions, the circuit breaker and the no-model fallback (sous.md, v1.1)."""

import asyncio
import json
import uuid
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import scoped_session
from app.errors import ProblemError
from app.identity.models import Location
from app.security.sessions import AuthContext
from app.sous.agent import fallback, orchestrator
from app.sous.agent.llm import (
    CircuitBreaker,
    FakeProvider,
    LLMReply,
    Message,
    OllamaProvider,
    ProviderHealth,
    ToolCall,
    sous_unavailable,
)
from app.sous.schemas import AskIn, Heartbeat, SousAnswer, StatusEvent, ToolEvent
from tests.integrations.conftest import Clock, Connected
from tests.sous.conftest import LAST
from tests.sous.test_api import UseProvider, body, mock_ollama, problem

DASH = "\u2013"

# --- helpers -----------------------------------------------------------------------------------


class Clockwork:
    """A manual monotonic clock for the breaker."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class DownProvider:
    """A model that is never reachable."""

    name = "ollama"
    model = "down-model"
    breaker: CircuitBreaker | None = None

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMReply:
        self.calls += 1
        raise sous_unavailable()

    async def health(self) -> ProviderHealth:
        return ProviderHealth(reachable=False, model_pulled=False)


def ctx_for(conn: Connected) -> AuthContext:
    return AuthContext(
        user_id=conn.user_id,
        org_id=conn.org_id,
        role="owner",
        session_id=uuid.uuid4(),
        csrf_token="x",
    )


async def location(conn: Connected) -> Location:
    async with scoped_session(conn.org_id) as db:
        return await db.get_one(Location, conn.location_id)


def parse_sse(raw: str) -> list[tuple[str, Any]]:
    """[(event, data)], with ("ping", None) for heartbeat comments."""
    out: list[tuple[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        if block.startswith(":"):
            out.append(("ping", None))
            continue
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((fields["event"], json.loads(fields["data"])))
    return out


async def stream(conn: Connected, payload: dict[str, Any]) -> httpx.Response:
    return await conn.client.post("/v1/sous/ask/stream", json=payload, headers=conn.headers)


# --- circuit breaker ---------------------------------------------------------------------------


def test_breaker_opens_after_threshold_and_fails_fast() -> None:
    clock = Clockwork()
    b = CircuitBreaker(threshold=3, cooldown=30, clock=clock)
    for _ in range(2):
        assert b.allow()
        b.record_failure()
    assert b.state == "closed"
    assert b.allow()
    b.record_failure()
    assert b.state == "open"
    assert not b.allow()


def test_breaker_half_open_lets_one_trial_through() -> None:
    clock = Clockwork()
    b = CircuitBreaker(threshold=1, cooldown=30, clock=clock)
    b.record_failure()
    clock.t += 29.9
    assert b.state == "open" and not b.allow()
    clock.t += 0.2
    assert b.state == "half_open"
    assert b.allow()  # the trial
    assert not b.allow()  # everyone else waits for its verdict
    b.record_success()
    assert b.state == "closed" and b.allow()


def test_failed_trial_reopens_for_a_full_cooldown() -> None:
    clock = Clockwork()
    b = CircuitBreaker(threshold=3, cooldown=30, clock=clock)
    for _ in range(3):
        b.record_failure()
    clock.t += 30
    assert b.allow()
    b.record_failure()  # one failed trial is enough, below the threshold
    assert b.state == "open"
    clock.t += 29
    assert b.state == "open"


def test_a_success_resets_the_failure_count() -> None:
    b = CircuitBreaker(threshold=3, cooldown=30, clock=Clockwork())
    b.record_failure()
    b.record_failure()
    b.record_success()
    b.record_failure()
    b.record_failure()
    assert b.state == "closed"


def test_release_frees_the_trial_slot() -> None:
    clock = Clockwork()
    b = CircuitBreaker(threshold=1, cooldown=30, clock=clock)
    b.record_failure()
    clock.t += 30
    assert b.allow()
    b.release()
    assert b.allow()


async def test_open_breaker_skips_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    hits: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request)
        return httpx.Response(500, text="boom")

    mock_ollama(monkeypatch, handler)
    breaker = CircuitBreaker(threshold=2, cooldown=30, clock=Clockwork())
    provider = OllamaProvider("http://ollama.test", "m", 1, breaker=breaker)
    for _ in range(2):
        with pytest.raises(ProblemError):
            await provider.chat([], [])
    assert len(hits) == 2 and breaker.state == "open"
    with pytest.raises(ProblemError) as exc:
        await provider.chat([], [])
    assert exc.value.code == "sous_unavailable"
    assert len(hits) == 2  # failed fast


async def test_successful_call_closes_a_half_open_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ollama(monkeypatch, lambda _: httpx.Response(200, json={"message": {"content": "hi"}}))
    clock = Clockwork()
    breaker = CircuitBreaker(threshold=1, cooldown=30, clock=clock)
    breaker.record_failure()
    clock.t += 30
    provider = OllamaProvider("http://ollama.test", "m", 1, breaker=breaker)
    assert (await provider.chat([], [])).content == "hi"
    assert breaker.state == "closed"


# --- fallback matcher --------------------------------------------------------------------------

TODAY = date(2026, 10, 20)  # a Tuesday


@pytest.mark.parametrize(
    ("question", "tool", "args"),
    [
        (
            "What were net sales yesterday?",
            "get_sales_summary",
            {"from": "2026-10-19", "to": "2026-10-19"},
        ),
        (
            "How did last week compare to the week before?",
            "get_sales_summary",
            {"from": "2026-10-12", "to": "2026-10-18", "compare": "previous_period"},
        ),
        (
            "Labor % last month vs last year?",
            "get_sales_summary",
            {"from": "2026-09-01", "to": "2026-09-30", "compare": "previous_year"},
        ),
        (
            "orders over the last 7 days",
            "get_sales_summary",
            {"from": "2026-10-13", "to": "2026-10-19"},
        ),
        ("How are we doing today?", "get_today", {}),
        (
            "Revenue this month so far",
            "get_sales_summary",
            {"from": "2026-10-01", "to": "2026-10-20"},
        ),
    ],
)
def test_fallback_matches_simple_totals_questions(
    question: str, tool: str, args: dict[str, Any]
) -> None:
    plan = fallback.match(question, TODAY)
    assert plan is not None
    assert (plan.tool, plan.args) == (tool, args)


@pytest.mark.parametrize(
    "question",
    [
        "How did we do?",  # no period
        "What were our top items last week?",  # items
        "Who is working tonight?",  # schedule
        "Sales last week and this week?",  # two periods
        "How did our Manhattan location do yesterday?",  # another place
        "And the week before that?",  # needs history
        "Labor today?",  # get_today has no labor
        "Write me a poem about last week",  # no metric
        "Which channel grew the most last month?",
    ],
)
def test_fallback_declines_anything_else(question: str) -> None:
    assert fallback.match(question, TODAY) is None


def test_render_uses_exact_figures_and_comparison() -> None:
    plan = fallback.match("How did last week compare to the week before?", TODAY)
    assert plan is not None
    data = {
        "period": {"from": "2026-10-12", "to": "2026-10-18", "days": 7},
        "net_sales_usd": 55967.5,
        "orders": 933,
        "avg_check_usd": 59.99,
        "labor_cost_usd": 15664.21,
        "labor_pct": 28.0,
        "comparison": {
            "kind": "previous_period",
            "period": {"from": "2026-10-05", "to": "2026-10-11", "days": 7},
            "net_sales_usd": 54631.77,
            "change": {
                "net_sales_pct": 2.4,
                "orders_pct": 1.5,
                "avg_check_pct": 0.0,
                "labor_pct_pts": -0.7,
            },
        },
    }
    assert fallback.render(plan, data) == (
        f"Last week (Oct 12 {DASH} Oct 18): net sales $55,967.50, 933 orders, average check "
        f"$59.99, labor 28.0% ($15,664.21). Compared with the period before (Oct 5 {DASH} Oct 11, "
        "net sales $54,631.77): net sales up 2.4%, orders up 1.5%, average check flat, "
        "labor % down 0.7 pts."
    )


def test_render_today_handles_negative_sales_and_no_orders() -> None:
    plan = fallback.match("How are we doing today?", TODAY)
    assert plan is not None
    text_ = fallback.render(
        plan,
        {"business_date": "2026-10-20", "net_sales_usd": -6.0, "orders": 0, "avg_check_usd": None},
    )
    assert text_ == "Today so far (Oct 20): net sales -$6.00, 0 orders."


# --- orchestrator: events, fallback, short sessions --------------------------------------------


async def test_events_stream_status_tool_then_answer(loaded: Connected, clock: Clock) -> None:
    provider = FakeProvider(
        [
            LLMReply(
                tool_calls=[
                    ToolCall("get_sales_summary", {"from": "2026-03-01", "to": "2026-03-07"})
                ]
            ),
            LLMReply(content="CLARIFY: Which number matters most to you?"),
        ]
    )
    req = AskIn(question="How was last week?", location_id=loaded.location_id)
    events = [
        e
        async for e in orchestrator.events(
            ctx_for(loaded), await location(loaded), req, provider, now=clock.now
        )
    ]
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["StatusEvent", "ToolEvent", "StatusEvent", "SousAnswer"]
    tool = events[1]
    assert isinstance(tool, ToolEvent) and tool.ok
    assert tool.label == f"Checked sales for Mar 1 {DASH} Mar 7"
    assert isinstance(events[0], StatusEvent) and events[0].label == "Thinking"


async def test_heartbeats_while_the_model_thinks(
    loaded: Connected, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator, "HEARTBEAT_SECONDS", 0.01)

    class Slow(FakeProvider):
        async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMReply:
            await asyncio.sleep(0.05)
            return await super().chat(messages, tools)

    provider = Slow([LLMReply(content="UNSUPPORTED: Only sales questions.")])
    req = AskIn(question="Weather?", location_id=loaded.location_id)
    events = [
        e
        async for e in orchestrator.events(
            ctx_for(loaded), await location(loaded), req, provider, now=clock.now
        )
    ]
    assert any(isinstance(e, Heartbeat) for e in events)
    assert isinstance(events[-1], SousAnswer) and events[-1].kind == "unsupported"


async def test_no_transaction_is_held_while_the_model_thinks(
    loaded: Connected, clock: Clock, owner_engine: AsyncEngine
) -> None:
    held: list[int] = []

    class Probe(FakeProvider):
        async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMReply:
            async with owner_engine.connect() as conn:
                held.append(
                    await conn.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE usename = 'chefos_app' "
                            "AND state LIKE 'idle in transaction%'"
                        )
                    )
                    or 0
                )
            return await super().chat(messages, tools)

    provider = Probe(
        [
            LLMReply(
                tool_calls=[
                    ToolCall("get_sales_summary", {"from": "2026-03-01", "to": "2026-03-07"})
                ]
            ),
            LLMReply(content="CLARIFY: Which number?"),
        ]
    )
    req = AskIn(question="How was last week?", location_id=loaded.location_id)
    await orchestrator.ask(ctx_for(loaded), await location(loaded), req, provider, now=clock.now)
    assert held == [0, 0]  # before the first round and after the tool call


async def test_fallback_answers_when_the_model_is_down(loaded: Connected, clock: Clock) -> None:
    provider = DownProvider()
    req = AskIn(question="What were net sales yesterday?", location_id=loaded.location_id)
    loc = await location(loaded)
    answer = await orchestrator.ask(ctx_for(loaded), loc, req, provider, now=clock.now)
    assert answer.mode == "fallback" and answer.kind == "answer" and answer.model == "fallback"
    assert answer.limitations[0] == fallback.LIMITATION
    assert [t.name for t in answer.tools_used] == ["get_sales_summary"]
    yesterday = LAST.replace(day=LAST.day - 1)
    assert answer.period is not None and answer.period.from_ == yesterday
    net = next(e for e in answer.evidence if e.label.startswith("Net sales"))
    assert fallback.money(net.value / 100) in answer.answer


async def test_unmatched_question_still_fails_when_the_model_is_down(
    loaded: Connected, clock: Clock
) -> None:
    req = AskIn(question="What were our top items last week?", location_id=loaded.location_id)
    with pytest.raises(ProblemError) as exc:
        await orchestrator.ask(
            ctx_for(loaded), await location(loaded), req, DownProvider(), now=clock.now
        )
    assert exc.value.code == "sous_unavailable"


# --- HTTP stream -------------------------------------------------------------------------------


async def test_stream_endpoint_sends_events_then_answer(
    bare: Connected, use_provider: UseProvider
) -> None:
    use_provider(
        FakeProvider(
            [
                LLMReply(tool_calls=[ToolCall("get_data_freshness", {})]),
                LLMReply(content="CLARIFY: Which stream do you care about?"),
            ]
        )
    )
    r = await stream(bare, body(bare))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache, no-transform"
    events = parse_sse(r.text)
    assert [name for name, _ in events] == ["conversation", "status", "tool", "status", "answer"]
    assert events[2][1]["label"] == "Checked how current the data is"
    answer = events[-1][1]
    assert answer["kind"] == "clarify" and answer["mode"] == "model"
    assert answer["request_id"] == r.headers["x-request-id"]


async def test_stream_reports_model_outage_as_an_error_event(
    bare: Connected, use_provider: UseProvider
) -> None:
    use_provider(DownProvider())
    r = await stream(bare, body(bare, "What were our top items last week?"))
    assert r.status_code == 200  # the breaker wasn't open, so the stream started
    events = parse_sse(r.text)
    assert events[-1] == (
        "error",
        {
            "status": 503,
            "code": "sous_unavailable",
            "detail": "Sous can't reach its language model right now. Try again in a moment.",
        },
    )


async def test_stream_falls_back_when_the_model_is_down(
    bare: Connected, use_provider: UseProvider
) -> None:
    use_provider(DownProvider())
    r = await stream(bare, body(bare, "How are we doing today?"))
    events = parse_sse(r.text)
    names = [name for name, _ in events]
    assert names[-3:] == ["status", "tool", "answer"]
    assert events[-1][1]["mode"] == "fallback"


async def test_open_breaker_without_fallback_is_503_before_streaming(
    bare: Connected, use_provider: UseProvider
) -> None:
    breaker = CircuitBreaker(threshold=1, cooldown=30, clock=Clockwork())
    breaker.record_failure()
    use_provider(OllamaProvider("http://127.0.0.1:9", "m", 1, breaker=breaker))
    problem(await stream(bare, body(bare, "Top items last week?")), 503, "sous_unavailable")
    status = await bare.client.get("/v1/sous/status")
    assert status.json()["breaker"] == "open"


async def test_stream_needs_auth_csrf_and_scope(bare: Connected, use_provider: UseProvider) -> None:
    use_provider(FakeProvider([]))
    r = await bare.client.post("/v1/sous/ask/stream", json=body(bare))  # no CSRF header
    assert r.status_code == 403
    other = body(bare, location_id=str(uuid.uuid4()))
    problem(await stream(bare, other), 404, "not_found")
