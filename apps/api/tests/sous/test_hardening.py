"""Untrusted provider output, cancellation, deadlines and mid-request access revocation."""

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.errors import ProblemError
from app.sous.agent import llm, orchestrator, tools
from app.sous.agent.llm import CircuitBreaker, FakeProvider, LLMReply, OllamaProvider, ToolCall
from app.sous.schemas import AskIn, Heartbeat
from tests.integrations.conftest import Clock, Connected
from tests.sous.test_api import UseProvider, body, mock_ollama, post
from tests.sous.test_orchestrator import ask, say, tool_round
from tests.sous.test_resilience import ctx_for, location, parse_sse


@pytest.mark.parametrize(
    "message",
    [
        {"content": 42},
        {"content": {"answer": "done"}},
        {"content": "x" * (llm.MAX_REPLY_CHARS + 1)},
        {"tool_calls": "not a list"},
        {"tool_calls": {}},
        {"tool_calls": False},
        {"tool_calls": [{"function": {"name": "get_today", "arguments": "{bad"}}]},
        {"tool_calls": [{"function": {"name": "get_today", "arguments": []}}]},
        {"tool_calls": [{"function": {"name": 17, "arguments": {}}}]},
        {"tool_calls": [{"function": {"name": "get_today"}}] * 17},
        {"tool_calls": [{"function": {"name": "get_today", "arguments": {"x": "a" * 4001}}}]},
    ],
)
async def test_malformed_reply_fails_closed(
    monkeypatch: pytest.MonkeyPatch, message: dict[str, Any]
) -> None:
    mock_ollama(monkeypatch, lambda _: httpx.Response(200, json={"message": message}))
    breaker = CircuitBreaker(threshold=1)
    with pytest.raises(ProblemError, match="language model"):
        await OllamaProvider("http://ollama.test", "m", 1, breaker=breaker).chat([], [])
    assert breaker.state == "open"


async def test_response_size_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ollama(monkeypatch, lambda _: httpx.Response(200, content=b" " * 128_001))
    with pytest.raises(ProblemError):
        await OllamaProvider("http://ollama.test", "m", 1).chat([], [])


async def test_context_budget_stops_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        pytest.fail("Oversized context reached the model")

    mock_ollama(monkeypatch, unexpected)
    breaker = CircuitBreaker(threshold=1)
    with pytest.raises(ProblemError) as exc:
        await OllamaProvider("http://ollama.test", "m", 1, breaker=breaker).chat(
            [{"role": "user", "content": "x" * 48_001}], []
        )
    assert exc.value.code == "sous_context_limit" and breaker.state == "closed"


async def test_truncated_generation_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ollama(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            json={
                "message": {"content": "I placed your order"},
                "done_reason": "length",
            },
        ),
    )
    with pytest.raises(ProblemError):
        await OllamaProvider("http://ollama.test", "m", 1).chat([], [])


def test_context_budget_includes_schemas_and_multibyte_text() -> None:
    assert llm.context_fits([{"role": "user", "content": "hello"}], [])
    assert not llm.context_fits([{"role": "user", "content": "🍽" * 8000}], [])
    assert not llm.context_fits([], [{"description": "x" * llm.MAX_CONTEXT_BYTES}])


async def test_admission_limit_releases_capacity_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm, "MAX_IN_FLIGHT", 1)
    started = asyncio.Event()

    async def waiting(self: Any, messages: Any, specs: Any) -> LLMReply:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(OllamaProvider, "_chat", waiting)
    breaker = CircuitBreaker(threshold=1)
    provider = OllamaProvider("http://ollama.test", "m", 1, breaker=breaker)
    task = asyncio.create_task(provider.chat([], []))
    try:
        await started.wait()
        with pytest.raises(ProblemError) as exc:
            await provider.chat([], [])
        assert exc.value.code == "sous_busy" and breaker.state == "closed"
        assert llm._in_flight == 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert llm._in_flight == 0


async def test_stale_success_cannot_close_breaker_during_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.sous.test_resilience import Clockwork

    started, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def overlapping(self: Any, messages: Any, specs: Any) -> LLMReply:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await finish.wait()
            return LLMReply(content="UNSUPPORTED: old result")
        raise llm.sous_unavailable()

    monkeypatch.setattr(OllamaProvider, "_chat", overlapping)
    clock = Clockwork()
    breaker = CircuitBreaker(threshold=1, clock=clock)
    provider = OllamaProvider("http://ollama.test", "m", 1, breaker=breaker)
    old = asyncio.create_task(provider.chat([], []))
    try:
        await started.wait()
        with pytest.raises(ProblemError):
            await provider.chat([], [])
        clock.t += 31
        assert breaker.allow()  # one recovery probe owns the slot
        finish.set()
        await old
        assert breaker.state == "half_open" and not breaker.allow()
    finally:
        finish.set()
        await old


async def test_context_growth_preserves_evidence_without_another_model_call(
    bare: Connected, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def large(*args: Any, **kwargs: Any) -> tools.ToolResult:
        return tools.ToolResult(tool="get_today", data={"note": "x" * 48_001})

    monkeypatch.setattr(tools, "run_tool", large)
    provider = FakeProvider([tool_round(ToolCall("get_today"))])
    answer = await ask(bare, clock, provider)
    assert answer.kind == "evidence_only" and len(provider.calls) == 1


async def test_absolute_timeout_stops_trickling_response(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = asyncio.Event()

    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.005)
                yield b" "

        async def aclose(self) -> None:
            closed.set()

    mock_ollama(monkeypatch, lambda _: httpx.Response(200, stream=Trickle()))
    breaker = CircuitBreaker(threshold=1)
    with pytest.raises(ProblemError):
        await asyncio.wait_for(
            OllamaProvider("http://ollama.test", "m", 0.03, breaker=breaker).chat([], []), 1
        )
    assert closed.is_set() and breaker.state == "open"


@pytest.mark.parametrize("reply", ["I placed your order.", "Sales look great."])
async def test_answer_without_successful_data_is_withheld(
    bare: Connected, clock: Clock, reply: str
) -> None:
    answer = await ask(bare, clock, FakeProvider([say(reply), say(reply)]))
    assert answer.kind == "evidence_only" and reply not in answer.answer


async def test_all_failed_tools_cannot_support_an_answer(bare: Connected, clock: Clock) -> None:
    reply = "I updated the schedule."
    provider = FakeProvider([tool_round(ToolCall("edit_schedule")), say(reply), say(reply)])
    answer = await ask(bare, clock, provider)
    assert answer.kind == "evidence_only"
    assert not answer.tools_used[0].ok
    assert any("failed" in line for line in answer.limitations)


class WaitingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__([])
        self.stopped = asyncio.Event()

    async def chat(self, messages: Any, specs: Any) -> LLMReply:
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped.set()
        raise AssertionError("unreachable")


async def test_stream_close_joins_the_model_task(
    bare: Connected, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator, "HEARTBEAT_SECONDS", 0.01)
    provider = WaitingProvider()
    stream = orchestrator.events(
        ctx_for(bare), await location(bare), AskIn(**body(bare)), provider, now=clock.now
    )
    await anext(stream)  # thinking
    assert isinstance(await anext(stream), Heartbeat)
    await stream.aclose()
    assert provider.stopped.is_set()


async def test_total_request_deadline_cancels_model(
    bare: Connected, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator, "REQUEST_TIMEOUT_SECONDS", 0.03)
    provider = WaitingProvider()
    with pytest.raises(ProblemError) as exc:
        await ask(bare, clock, provider)
    assert (exc.value.status, exc.value.code) == (504, "sous_timeout")
    assert provider.stopped.is_set()


async def test_tool_timeout_is_a_failed_step(
    bare: Connected, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator, "TOOL_TIMEOUT_SECONDS", 0.05)
    stopped = asyncio.Event()

    async def waiting(*args: Any, **kwargs: Any) -> tools.ToolResult:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(tools, "run_tool", waiting)
    answer = await ask(
        bare,
        clock,
        FakeProvider(
            [tool_round(ToolCall("get_today")), say("Sales look great."), say("Sales look great.")]
        ),
    )
    assert stopped.is_set() and not answer.tools_used[0].ok
    assert answer.kind == "evidence_only"
    assert any("timed out" in line for line in answer.limitations)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("change", ["revoke", "demote", "remove_location"])
@pytest.mark.parametrize("stage", ["tool", "answer"])
async def test_access_revoked_while_model_runs_is_not_released(
    bare: Connected,
    owner_engine: AsyncEngine,
    use_provider: UseProvider,
    monkeypatch: pytest.MonkeyPatch,
    streaming: bool,
    change: str,
    stage: str,
) -> None:
    ran_tools = 0
    original_run_tool = tools.run_tool

    async def count_tool(*args: Any, **kwargs: Any) -> Any:
        nonlocal ran_tools
        ran_tools += 1
        return await original_run_tool(*args, **kwargs)

    monkeypatch.setattr(tools, "run_tool", count_tool)

    class RevokingProvider(FakeProvider):
        async def chat(self, messages: Any, specs: Any) -> LLMReply:
            if stage == "answer" and not self.calls:
                return await super().chat(messages, specs)
            async with owner_engine.begin() as db:
                if change == "revoke":
                    await db.execute(
                        text("UPDATE sessions SET revoked_at=now() WHERE user_id=:u"),
                        {"u": bare.user_id},
                    )
                else:
                    role = "employee" if change == "demote" else "manager"
                    await db.execute(
                        text("UPDATE memberships SET role=:r WHERE user_id=:u"),
                        {"u": bare.user_id, "r": role},
                    )
                    if change == "remove_location":
                        await db.execute(
                            text("DELETE FROM location_access WHERE user_id=:u"),
                            {"u": bare.user_id},
                        )
            return await super().chat(messages, specs)

    provider = RevokingProvider(
        [tool_round(ToolCall("get_today")), say('{"kind":"answer","claims":["r0.net_sales_usd"]}')]
    )
    use_provider(provider)
    expected = {"revoke": 401, "demote": 403, "remove_location": 404}[change]
    if streaming:
        response = await bare.client.post(
            "/v1/sous/ask/stream", json=body(bare), headers=bare.headers
        )
        events = parse_sse(response.text)
        assert events[-1][0] == "error" and events[-1][1]["status"] == expected
        assert all(name != "answer" for name, _ in events)
        if stage == "tool":
            assert all(name != "tool" for name, _ in events)
    else:
        response = await post(bare, body(bare))
        assert response.status_code == expected, response.text
    assert len(provider.calls) == (1 if stage == "tool" else 2)
    assert ran_tools == (0 if stage == "tool" else 1)
