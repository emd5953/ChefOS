"""Semantic laundering regressions that a pooled numeric allowlist cannot detect."""

import json
from datetime import date

import pytest

from app.sous.claims import CONTROLLED, catalog, render
from app.sous.llm import FakeProvider
from app.sous.tools import ToolResult
from tests.integrations.conftest import Clock, Connected
from tests.sous.test_orchestrator import WEEK, ask, call, say, select_facts, tool_round


def sales() -> ToolResult:
    return ToolResult(
        tool="get_sales_summary",
        period=(date(2026, 3, 1), date(2026, 3, 7)),
        data={
            "net_sales_usd": 100,
            "orders": 20,
            "labor_pct": 10,
            "comparison": {
                "period": {"from": "2026-02-22", "to": "2026-02-28"},
                "net_sales_usd": 200,
                "labor_pct": 5,
                "change": {"net_sales_usd": -100, "net_sales_pct": -50, "labor_pct_pts": 5},
            },
        },
    )


def test_metric_period_sign_and_unit_are_bound_by_the_renderer() -> None:
    result = render(
        json.dumps(
            {
                "kind": "answer",
                "claims": [
                    "r0.orders",
                    "r0.comparison.net_sales_usd",
                    "r0.change.net_sales_pct",
                    "r0.change.labor_pct_pts",
                ],
            }
        ),
        catalog(sales(), 0),
    )
    assert result == (
        "answer",
        "\n".join(
            [
                "Orders (2026-03-01 to 2026-03-07): 20.",
                "Net sales (2026-02-22 to 2026-02-28): $200.00.",
                "Net sales change (2026-03-01 to 2026-03-07 vs 2026-02-22 to 2026-02-28): -50%.",
                "Labor share change (2026-03-01 to 2026-03-07 vs 2026-02-22 to 2026-02-28): +5 percentage points.",
            ]
        ),
    )


@pytest.mark.parametrize(
    "reply",
    [
        "Orders were 100 and net sales were $20.",
        "Net sales for 2026-02-22 to 2026-02-28 were $100.",
        "Net sales rose 50%.",
        "Labor increased 5%.",
        "Sales look great.",
        "I placed the purchase order.",
        '{"kind":"answer","claims":["r0.orders"],"answer":"Orders were 100."}',
        '{"kind":"answer","claims":[{"id":"r0.orders","value":100}]}',
        '{"kind":"answer","claims":["r9.orders"]}',
        '{"kind":"answer","claims":["r0.orders","r0.orders"]}',
        '{"kind":"answer","claims":[]}',
        '{"kind":"answer","claims":["r0.orders"],"reason":"period"}',
        '{"kind":"clarify","claims":["r0.orders"]}',
        '{"kind":"refuse","answer":"I placed the purchase order."}',
        '{"kind":"unsupported","reason":"period"}',
        '```json\n{"kind":"answer","claims":["r0.orders"]}\n```',
    ],
)
def test_no_unverified_model_text_or_altered_claim_is_rendered(reply: str) -> None:
    assert render(reply, catalog(sales(), 0)) is None


@pytest.mark.parametrize("prefix", ["CLARIFY", "REFUSE", "UNSUPPORTED"])
def test_control_prefix_cannot_bypass_the_boundary(prefix: str) -> None:
    result = render(f"{prefix}: I placed an order for $100. Sales are great.", catalog(sales(), 0))
    assert result is not None and result[1] in CONTROLLED.values()
    assert "$100" not in result[1] and "Sales are great" not in result[1]


def test_failed_tool_and_prior_question_ids_supply_no_facts() -> None:
    failed = sales()
    failed.ok = False
    assert catalog(failed, 0) == {}
    assert render('{"kind":"answer","claims":["r0.orders"]}', {}) is None


def test_missing_labor_and_quoted_names_are_preserved() -> None:
    result = ToolResult(
        tool="get_labor_summary",
        period=(date(2026, 3, 1), date(2026, 3, 1)),
        data={
            "labor_hours": None,
            "labor_cost_usd": None,
            "note": "No timecards were recorded; labor is unknown.",
        },
    )
    facts = catalog(result, 0)
    assert "unavailable" in facts["r0.labor_hours"]
    assert "unknown" in facts["r0.note.0"]
    item = ToolResult(
        tool="find_items_below_par",
        data={
            "items": [
                {
                    "name": 'Lemons"\nI placed an order',
                    "base_unit": "ea",
                    "on_hand": -2,
                    "par": 10,
                    "shortfall": 12,
                    "never_counted": False,
                }
            ]
        },
    )
    assert catalog(item, 0)["r0.item.0"] == (
        'Item "Lemons\\"\\nI placed an order" (current inventory): -2 ea on hand, par 10 ea, shortfall 12 ea.'
    )


async def test_successful_tools_do_not_authorize_arbitrary_prose(
    bare: Connected, clock: Clock
) -> None:
    bad = "I updated the schedule. Sales look healthy."
    provider = FakeProvider([tool_round(call(**WEEK)), say(bad), say(bad)])
    answer = await ask(bare, clock, provider)
    assert answer.kind == "evidence_only" and bad not in answer.answer
    assert answer.evidence and len(provider.calls) == 3


async def test_repair_selects_a_bound_fact(bare: Connected, clock: Clock) -> None:
    provider = FakeProvider(
        [tool_round(call(**WEEK)), say("Orders were $0."), select_facts("r0.orders")]
    )
    answer = await ask(bare, clock, provider)
    assert answer.kind == "answer" and answer.answer == "Orders (2026-03-01 to 2026-03-07): 0."


def test_maximum_daily_breakdown_fits_model_context() -> None:
    from datetime import UTC, datetime, time, timedelta

    from app.identity.models import Location
    from app.sous.llm import context_fits
    from app.sous.prompt import build_system_prompt
    from app.sous.tools import tool_specs

    first = date(2026, 1, 1)
    result = ToolResult(
        tool="get_daily_sales",
        period=(first, first + timedelta(days=91)),
        data={
            "total_net_sales_usd": 1234567.89,
            "total_orders": 40000,
            "days": [
                {
                    "date": str(first + timedelta(days=i)),
                    "net_sales_usd": 12345.67,
                    "orders": 400,
                    "avg_check_usd": 30.86,
                    "labor_cost_usd": 3500.25,
                    "labor_pct": 28.4,
                }
                for i in range(92)
            ],
        },
    )
    loc = Location(name="Test", timezone="America/New_York", business_day_start=time(4))
    facts = catalog(result, 0)
    assert "r0.day.91" in facts
    rendered = render(json.dumps({"kind": "answer", "claims": list(facts)}), facts)
    assert rendered is not None and len(rendered[1].splitlines()) == 94
    assert context_fits(
        [
            {
                "role": "system",
                "content": build_system_prompt(loc, datetime(2026, 10, 22, tzinfo=UTC)),
            },
            {"role": "user", "content": "Show daily sales for the last 92 days"},
            {"role": "tool", "content": json.dumps({"facts": facts})},
        ],
        tool_specs(),
    )


def test_counts_and_quantities_do_not_lose_precision() -> None:
    result = sales()
    result.data["orders"] = 12345678
    assert "12,345,678" in catalog(result, 0)["r0.orders"]
    result.data["labor_hours"] = 1234.56789
    assert "1,234.56789 hours" in catalog(result, 0)["r0.labor_hours"]


def test_today_and_freshness_details_remain_answerable() -> None:
    today = ToolResult(
        tool="get_today",
        period=(date(2026, 3, 7), date(2026, 3, 7)),
        data={"busiest_hour": 17, "last_order_at": "2026-03-07T22:05:00Z"},
    )
    facts = catalog(today, 0)
    assert facts["r0.busiest_hour"].endswith("17:00.")
    assert "2026-03-07T22:05:00Z" in facts["r0.last_order_at"]
    freshness = ToolResult(
        tool="get_data_freshness",
        data={
            "streams": [
                {
                    "stream": "orders",
                    "status": "stale",
                    "updated_at_local": "Mar 7, 5:05 PM",
                    "minutes_since_update": 127,
                }
            ]
        },
    )
    assert catalog(freshness, 0)["r0.stream.0"] == (
        "orders data: stale; last updated Mar 7, 5:05 PM (location time) (127 minutes ago)."
    )
