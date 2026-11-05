"""The live quality gate must not reward wrong facts, dropped failures, or canned replies."""

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.sous.agent.tools import ToolResult
from app.sous.schemas import Evidence, SousAnswer, ToolUse
from evals.sous.run import (
    CaseResult,
    UnresolvedFieldError,
    ValueCheck,
    expected_fact,
    report_payload,
    score,
    summarize,
    threshold_misses,
    write_json,
)

PERIOD = (date(2026, 10, 12), date(2026, 10, 18))


def result(data: dict[str, Any], tool: str = "get_sales_summary") -> ToolResult:
    return ToolResult(tool=tool, data=data, period=PERIOD, evidence=[], notes=[], ok=True)


def res() -> CaseResult:
    return CaseResult(
        id="sales",
        category="sales",
        question="Net sales last week?",
        expected_kinds=["answer"],
        tools_expected=["get_sales_summary"],
        values=[
            ValueCheck(
                tool="get_sales_summary",
                field="net_sales_usd",
                format="usd",
                expected=10,
                fact=expected_fact(result({"net_sales_usd": 10}), "net_sales_usd"),
            )
        ],
    )


def reply(text: str) -> SousAnswer:
    return SousAnswer(
        kind="answer",
        answer=text,
        period=None,
        evidence=[
            Evidence(
                label="Net sales",
                value=1000,
                unit="usd_cents",
                source="get_sales_summary",
                as_of=None,
            )
        ],
        limitations=[],
        tools_used=[ToolUse(name="get_sales_summary", args={}, ms=1, ok=True)],
        model="qwen3:8b",
        prompt_version="test",
        request_id="test",
    )


def scored(answer: SousAnswer) -> CaseResult:
    r = res()
    score({"expect": {"kind": "answer"}}, r, answer)
    return r


@pytest.mark.parametrize(
    "bad",
    [
        "Orders (2026-10-12 to 2026-10-18): 10.",
        "Net sales (2026-10-05 to 2026-10-11): $10.00.",
        "Net sales (2026-10-12 to 2026-10-18): $-10.00.",
        "Net sales (2026-10-12 to 2026-10-18): 10%.",
        "Net sales: $10.00.",
    ],
)
def test_numeric_coincidence_is_not_semantic_correctness(bad: str) -> None:
    r = scored(reply(bad))
    assert not r.passed and not r.values_ok
    assert summarize([r])["values_found"] == 0


def test_exact_fact_passes_and_percentage_points_keep_their_meaning() -> None:
    r = scored(reply(res().values[0].fact or ""))
    assert r.passed and not threshold_misses(summarize([r]))
    comparison = result(
        {
            "comparison": {
                "period": {"from": "2026-10-05", "to": "2026-10-11"},
                "change": {"labor_pct_pts": -3.2},
            }
        }
    )
    fact = expected_fact(comparison, "comparison.change.labor_pct_pts")
    assert "-3.2 percentage points" in fact and "vs 2026-10-05" in fact


def test_row_expectations_bind_entity_and_all_values() -> None:
    tool = result(
        {
            "channels": [
                {"channel": "dine_in", "net_sales_usd": 10, "share_pct": 50},
                {"channel": "delivery", "net_sales_usd": 10, "share_pct": 50},
            ]
        }
    )
    dine_in = expected_fact(tool, "channels.channel=dine_in.share_pct")
    assert dine_in != expected_fact(tool, "channels.1.share_pct")
    assert dine_in == expected_fact(tool, "channels.0.channel")
    with pytest.raises(UnresolvedFieldError, match="semantic fact"):
        expected_fact(result({"unknown": 10}), "unknown")


@pytest.mark.parametrize("kind", ["unsupported", "clarify", "evidence_only"])
def test_unexpected_declines_stay_in_value_denominator(kind: str) -> None:
    a = reply(res().values[0].fact or "").model_copy(update={"kind": kind})
    r = scored(a)
    s = summarize([r])
    assert not r.passed and s["values_checked"] == 1 and s["values_found"] == 0
    assert threshold_misses(s)


def test_permitted_refusal_does_not_need_numeric_facts_but_missing_truth_is_an_error() -> None:
    r = replace(res(), expected_kinds=["answer", "refuse"], tools_expected=[])
    case = {"expect": {"kind_any": ["answer", "refuse"]}}
    a = reply("No").model_copy(update={"kind": "refuse", "evidence": [], "tools_used": []})
    score(case, r, a)
    assert r.passed and summarize([r])["values_checked"] == 0
    r.values[0].error = "ground truth unavailable"
    score(case, r, a)
    assert not r.passed and any("unresolved" in m for m in threshold_misses(summarize([r])))


def test_empty_all_error_and_unresolved_runs_cannot_pass() -> None:
    assert threshold_misses(summarize([]))
    r = replace(res(), error="provider unavailable")
    s = summarize([r])
    assert s["values_checked"] == 1 and s["errors"] == 1
    assert any("errors" in m for m in threshold_misses(s))
    r = scored(reply(res().values[0].fact or ""))
    r.values[0].error = "missing fixture field"
    assert any("unresolved" in m for m in threshold_misses(summarize([r])))


def test_fallback_and_failed_tool_calls_are_not_live_success() -> None:
    a = reply(res().values[0].fact or "")
    r = scored(a.model_copy(update={"mode": "fallback"}))
    assert not r.passed and any("fallbacks" in m for m in threshold_misses(summarize([r])))
    r = scored(
        a.model_copy(update={"tools_used": [a.tools_used[0].model_copy(update={"ok": False})]})
    )
    assert not r.tools_ok and not r.passed


def test_checkpoints_preserve_repeats_metadata_and_incomplete_gate(tmp_path: Path) -> None:
    r = scored(reply(res().values[0].fact or ""))
    metadata = {"planned_cases": 2, "provider": "ollama", "model": "qwen3:8b"}
    payload = report_payload([r], metadata)
    assert not payload["complete"] and any("incomplete" in m for m in payload["gate_misses"])
    second = replace(r, repeat=2)
    payload = report_payload([r, second], metadata)
    assert payload["complete"] and not payload["gate_misses"]
    assert payload["by_repeat"]["1"]["cases"] == payload["by_repeat"]["2"]["cases"] == 1
    assert payload["by_category"]["sales"]["passed"] == 2
    path = tmp_path / "nested" / "eval.json"
    write_json(path, payload)
    assert path.exists() and not path.with_suffix(".json.tmp").exists()
    assert report_payload([r, second], {**metadata, "provider": "fake"})["gate_misses"]


async def test_preflight_checks_model_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.sous.agent import llm
    from evals.sous.run import preflight

    class MissingModel:
        async def health(self) -> llm.ProviderHealth:
            return llm.ProviderHealth(reachable=True, model_pulled=False)

    monkeypatch.setattr(llm, "get_provider", MissingModel)
    assert not await preflight()


async def test_provider_errors_are_recorded_as_failed_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid
    from datetime import UTC, datetime, time
    from types import SimpleNamespace

    from app.errors import ProblemError
    from app.sous.agent import orchestrator
    from app.sous.agent.llm import FakeProvider
    from evals.sous.run import Env, run_case

    async def unavailable(*args: object, **kwargs: object) -> None:
        raise ProblemError(503, "sous_unavailable", "Model unavailable")

    monkeypatch.setattr(orchestrator, "ask", unavailable)
    loc = SimpleNamespace(id=uuid.uuid4(), timezone="UTC", business_day_start=time(4))
    env = Env(org_id=uuid.uuid4(), loc=loc, ctx=None)
    case = {
        "id": "down",
        "category": "sales",
        "question": "Sales?",
        "expect": {"kind": "answer", "tools": []},
    }
    r = await run_case(case, env, FakeProvider([]), datetime.now(UTC))
    assert r.error and "Model unavailable" in r.error and not r.passed
    assert summarize([r])["errors"] == 1


async def test_eval_recording_preserves_provider_calls_and_raw_replies() -> None:
    from app.sous.agent.llm import FakeProvider, LLMReply, ToolCall
    from evals.sous.run import RecordingProvider

    reply = LLMReply(tool_calls=[ToolCall("get_sales_summary", {"from": "2026-10-21"})])
    source = FakeProvider([reply])
    recorded: list[dict[str, Any]] = []
    provider = RecordingProvider(source, recorded)
    messages = [{"role": "user", "content": "Confirm yesterday's sales"}]
    specs = [{"type": "function"}]
    assert await provider.chat(messages, specs) is reply
    assert source.calls == [messages]
    assert recorded == [
        {
            "content": "",
            "tool_calls": [
                {"name": "get_sales_summary", "arguments": {"from": "2026-10-21"}},
            ],
        }
    ]
    assert provider.name == source.name and provider.model == source.model
    assert provider.breaker is source.breaker
    assert await provider.health() == await source.health()


def test_freshness_metadata_is_grounded_without_numeric_cards() -> None:
    tool = result(
        {
            "streams": [
                {
                    "stream": "orders",
                    "status": "ok",
                    "updated_at_local": "Oct 22, 9:00 AM",
                    "minutes_since_update": 5,
                }
            ]
        },
        "get_data_freshness",
    )
    fact = expected_fact(tool, "streams.stream=orders.status")
    r = replace(
        res(),
        category="freshness",
        tools_expected=["get_data_freshness"],
        values=[
            ValueCheck(
                tool="get_data_freshness",
                field="streams.stream=orders.status",
                format="text",
                expected="ok",
                fact=fact,
            ),
        ],
    )
    a = reply(fact).model_copy(
        update={
            "evidence": [],
            "tools_used": [
                ToolUse(name="get_data_freshness", args={}, ms=1, ok=True),
            ],
        }
    )
    score({"expect": {"kind": "answer"}}, r, a)
    assert r.passed and r.grounded and not threshold_misses(summarize([r]))
    r.values[0].found = False
    assert summarize([r])["evidence_covered"] == 0
