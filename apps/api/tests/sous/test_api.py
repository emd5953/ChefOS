"""HTTP surface: auth, CSRF, scope, rate limit, provider outages, status, Ollama wire format."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.main import app
from app.security import passwords
from app.sous import llm, prompt
from app.sous.llm import FakeProvider, LLMProvider, LLMReply, OllamaProvider, ToolCall
from tests.conftest import PASSWORD
from tests.integrations.conftest import Connected

UseProvider = Callable[[LLMProvider], None]


def body(conn: Connected, question: str = "How was last week?", **extra: Any) -> dict[str, Any]:
    return {"question": question, "location_id": str(conn.location_id), **extra}


async def post(conn: Connected, payload: dict[str, Any]) -> httpx.Response:
    return await conn.client.post("/v1/sous/ask", json=payload, headers=conn.headers)


def problem(r: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    out: dict[str, Any] = r.json()
    assert out["code"] == code
    return out


async def test_ask_returns_the_sous_answer_shape(
    bare: Connected, use_provider: UseProvider
) -> None:
    use_provider(FakeProvider([LLMReply(content="CLARIFY: Which week do you mean?")]))
    r = await post(bare, body(bare))
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {
        "kind", "answer", "period", "evidence", "limitations", "tools_used", "model",
        "prompt_version", "request_id", "mode", "conversation_id", "turn_id", "conversation_version",
    }  # fmt: skip
    assert out["mode"] == "model"
    assert out["kind"] == "clarify" and "date range" in out["answer"]
    assert out["period"] is None and out["evidence"] == [] and out["tools_used"] == []
    assert out["prompt_version"] == prompt.PROMPT_VERSION
    assert out["request_id"] == r.headers["x-request-id"]


async def test_ask_with_tools_serializes_period_and_evidence(
    bare: Connected, use_provider: UseProvider
) -> None:
    use_provider(
        FakeProvider(
            [
                LLMReply(
                    tool_calls=[
                        ToolCall("get_sales_summary", {"from": "2026-03-01", "to": "2026-03-01"})
                    ]
                ),
                LLMReply(content='{"kind":"answer","claims":["r0.net_sales_usd"]}'),
            ]
        )
    )
    # `now` is the wall clock here, so 2026-03-01 is in the past
    r = await post(bare, body(bare))
    out = r.json()
    assert r.status_code == 200 and out["kind"] == "answer"
    assert out["period"] == {
        "from": "2026-03-01",
        "to": "2026-03-01",
        "timezone": "America/New_York",
    }
    assert out["tools_used"][0]["name"] == "get_sales_summary" and out["tools_used"][0]["ok"]
    assert {e["unit"] for e in out["evidence"]} <= {"usd_cents", "count", "percent"}
    assert all(set(e) == {"label", "value", "unit", "source", "as_of"} for e in out["evidence"])
    assert any("not connected" in line for line in out["limitations"])


async def test_authentication_and_csrf_are_required(
    bare: Connected, use_provider: UseProvider
) -> None:
    use_provider(FakeProvider([LLMReply(content="CLARIFY: Which?")] * 3))
    r = await bare.client.post("/v1/sous/ask", json=body(bare))  # no CSRF header
    problem(r, 403, "csrf_failed")
    r = await bare.client.post("/v1/sous/ask", json=body(bare), headers={"X-CSRF-Token": "nope"})
    problem(r, 403, "csrf_failed")
    ok = await post(bare, body(bare))
    assert ok.status_code == 200
    bare.client.cookies.clear()
    problem(await post(bare, body(bare)), 401, "unauthenticated")
    problem(await bare.client.get("/v1/sous/status"), 401, "unauthenticated")


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "", "location_id": str(uuid.uuid4())},
        {"question": "x" * 501, "location_id": str(uuid.uuid4())},
        {"question": "   ", "location_id": str(uuid.uuid4())},
        {"question": "hi"},
        {"question": "hi", "location_id": "not-a-uuid"},
        {
            "question": "hi",
            "location_id": str(uuid.uuid4()),
            "history": [{"role": "system", "content": "x"}],
        },
        {
            "question": "hi",
            "location_id": str(uuid.uuid4()),
            "history": [{"role": "user", "content": "x"}] * 11,
        },
    ],
)
async def test_invalid_requests_are_422(
    bare: Connected, use_provider: UseProvider, payload: dict[str, Any]
) -> None:
    use_provider(FakeProvider([LLMReply(content="CLARIFY: Which?")]))
    problem(await post(bare, payload), 422, "validation_error")


async def test_client_history_is_rejected_before_inference(
    bare: Connected, use_provider: UseProvider
) -> None:
    fake = FakeProvider([LLMReply(content="UNSUPPORTED: No.")])
    use_provider(fake)
    turns = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}] * 5
    r = await post(bare, body(bare, history=turns))
    assert r.status_code == 422 and fake.calls == []


async def test_other_orgs_location_is_not_found(
    bare: Connected, connect: Any, use_provider: UseProvider
) -> None:
    use_provider(FakeProvider([LLMReply(content="CLARIFY: Which?")]))
    other = await connect(complete=False)
    problem(await post(other, body(bare)), 404, "not_found")  # same as /v1/commerce/*


async def test_location_outside_a_members_scope_matches_commerce(
    bare: Connected, owner_engine: AsyncEngine, use_provider: UseProvider
) -> None:
    use_provider(FakeProvider([LLMReply(content="CLARIFY: Which?")] * 2))
    r = await bare.client.post(
        "/v1/locations",
        json={"name": "Second", "timezone": "America/Chicago"},
        headers=bare.headers,
    )
    second = r.json()["id"]
    email = f"mgr-{uuid.uuid4().hex[:8]}@example.com"
    user_id = uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, display_name) VALUES (:i, :e, :p, 'Mgr')"
            ),
            {"i": user_id, "e": email, "p": await passwords.hash_password(PASSWORD)},
        )
        await conn.execute(
            text(
                "INSERT INTO memberships (id, org_id, user_id, role) VALUES (:i, :o, :u, 'manager')"
            ),
            {"i": uuid.uuid4(), "o": bare.org_id, "u": user_id},
        )
        await conn.execute(
            text("INSERT INTO location_access (org_id, user_id, location_id) VALUES (:o, :u, :l)"),
            {"o": bare.org_id, "u": user_id, "l": second},
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        login = await c.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert login.status_code == 200, login.text
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        # The manager can't ask about the location they have no access to ...
        r = await c.post("/v1/sous/ask", json=body(bare), headers=headers)
        problem(r, 404, "not_found")
        assert (
            await c.get("/v1/commerce/today", params={"location_id": str(bare.location_id)})
        ).status_code == 404
        # ... but can about their own.
        ok = await c.post(
            "/v1/sous/ask", json={"question": "hi", "location_id": second}, headers=headers
        )
        assert ok.status_code == 200, ok.text


async def test_rate_limit_is_20_questions_a_minute_per_user(
    bare: Connected, connect: Any, use_provider: UseProvider
) -> None:

    class Repeating(FakeProvider):
        async def chat(self, messages: Any, tools: Any) -> LLMReply:
            return LLMReply(content="UNSUPPORTED: No.")

    use_provider(Repeating([]))
    for _ in range(20):
        assert (await post(bare, body(bare))).status_code == 200
    limited = problem(await post(bare, body(bare)), 429, "rate_limited")
    assert "20 questions" in limited["detail"]
    other = await connect(complete=False)  # another user is unaffected
    assert (await post(other, body(other))).status_code == 200


async def test_unreachable_ollama_is_503_sous_unavailable(
    bare: Connected, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "sous_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_url", "http://127.0.0.1:9")  # nothing listens here
    out = problem(await post(bare, body(bare)), 503, "sous_unavailable")
    assert out["title"] == "Sous unavailable" and "127.0.0.1" not in out["detail"]
    status = await bare.client.get("/v1/sous/status")
    assert status.status_code == 200
    assert status.json() == {
        "provider": "ollama",
        "model": settings.sous_model,
        "available": False,
        "model_pulled": False,
        "prompt_version": prompt.PROMPT_VERSION,
        "breaker": "closed",  # one failure doesn't open it
    }


async def test_status_with_the_fake_provider(bare: Connected, use_provider: UseProvider) -> None:
    use_provider(FakeProvider([], model="fake-1"))
    r = await bare.client.get("/v1/sous/status")
    assert r.json() == {
        "provider": "fake",
        "model": "fake-1",
        "available": True,
        "model_pulled": True,
        "prompt_version": prompt.PROMPT_VERSION,
        "breaker": "closed",
    }


def test_get_provider_follows_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "sous_provider", "fake")
    assert llm.get_provider().name == "fake"
    monkeypatch.setattr(settings, "sous_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_url", "http://ollama.test:11434/")
    monkeypatch.setattr(settings, "sous_model", "qwen3:8b")
    monkeypatch.setattr(settings, "sous_timeout_seconds", 7)
    p = llm.get_provider()
    assert isinstance(p, OllamaProvider)
    assert (p.url, p.model, p.timeout) == ("http://ollama.test:11434", "qwen3:8b", 7.0)


def mock_ollama(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    real = httpx.AsyncClient
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


async def test_ollama_chat_wire_format(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "get_today", "arguments": {}}},
                        {"function": {"name": "get_top_items", "arguments": '{"limit": 3}'}},
                    ],
                }
            },
        )

    mock_ollama(monkeypatch, handler)
    provider = OllamaProvider("http://ollama.test:11434", "qwen3:8b", 5)
    reply = await provider.chat([{"role": "user", "content": "hi"}], [{"type": "function"}])
    assert reply.tool_calls == [ToolCall("get_today", {}), ToolCall("get_top_items", {"limit": 3})]
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "http://ollama.test:11434/api/chat"
    assert json.loads(req.content) == {
        "model": "qwen3:8b",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function"}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": 2048, "num_ctx": 32768},
    }


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, json={"error": "model not found"}),
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"unexpected": True}),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_ollama_failures_are_sous_unavailable(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response | Exception
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    mock_ollama(monkeypatch, handler)
    with pytest.raises(llm.ProblemError) as exc:
        await OllamaProvider("http://ollama.test", "m", 1).chat([], [])
    assert (exc.value.status, exc.value.code) == (503, "sous_unavailable")


@pytest.mark.parametrize(
    ("model", "tags", "pulled"),
    [
        ("qwen3:8b", ["qwen3:8b", "llama3:8b"], True),
        ("qwen3:8b", ["qwen3:4b"], False),
        ("qwen3", ["qwen3:latest"], True),
        ("qwen3:8b", [], False),
    ],
)
async def test_ollama_health_reports_whether_the_model_is_pulled(
    monkeypatch: pytest.MonkeyPatch, model: str, tags: list[str], pulled: bool
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": t, "model": t} for t in tags]})

    mock_ollama(monkeypatch, handler)
    h = await OllamaProvider("http://ollama.test", model, 1).health()
    assert (h.reachable, h.model_pulled) == (True, pulled)
