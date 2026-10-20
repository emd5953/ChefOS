"""Fast schema checks for the Sous eval set (no DB, no model)."""

from datetime import date, timedelta

import pytest

from evals.sous.run import (
    CASES_PATH,
    CaseError,
    date_tokens,
    extract_field,
    format_question,
    load_cases,
    numbers_in,
    resolve_args,
    validate_case,
    value_in_text,
)


def test_cases_file_is_valid() -> None:
    cases = load_cases(CASES_PATH)
    assert 30 <= len(cases) <= 45
    categories = {c["category"] for c in cases}
    assert {"sales", "comparison", "items", "clarify", "unsupported", "refuse", "injection"} <= (
        categories
    )


def test_every_token_resolves_to_a_valid_range_on_any_day() -> None:
    cases = load_cases(CASES_PATH)
    start = date(2026, 1, 1)
    for offset in range(400):
        today = start + timedelta(days=offset)
        tokens = date_tokens(today)
        assert all(d <= today for d in tokens.values())
        for case in cases:
            format_question(case["question"], tokens)
            for v in case["expect"].get("values", []):
                args = resolve_args(v["args"], tokens)
                if "from" in args:
                    assert args["from"] <= args["to"], (case["id"], today)


def test_date_tokens_known_values() -> None:
    t = date_tokens(date(2026, 10, 20))  # a Tuesday
    assert t["this_week.start"] == date(2026, 10, 19)
    assert (t["last_week.start"], t["last_week.end"]) == (date(2026, 10, 12), date(2026, 10, 18))
    assert (t["last_month.start"], t["last_month.end"]) == (date(2026, 9, 1), date(2026, 9, 30))
    assert t["last_saturday"] == date(2026, 10, 17)
    assert t["last_tuesday"] == date(2026, 10, 13)
    assert (t["last_7.start"], t["last_7.end"]) == (date(2026, 10, 13), date(2026, 10, 19))


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "x", "category": "sales", "question": "q", "expect": {"kind": "answer"}},
        {"id": "x", "category": "nope", "question": "q", "expect": {"kind": "answer", "tools": []}},
        {"id": "x", "category": "sales", "question": "q", "expect": {"kind": "maybe", "tools": []}},
        {
            "id": "x",
            "category": "sales",
            "question": "q",
            "expect": {"kind": "answer", "tools": ["drop_tables"]},
        },
        {
            "id": "x",
            "category": "sales",
            "question": "q",
            "expect": {
                "kind": "answer",
                "tools": [],
                "values": [
                    {
                        "tool": "get_today",
                        "args": {"from": "someday"},
                        "field": "a",
                        "format": "usd",
                    }
                ],
            },
        },
    ],
)
def test_validator_rejects_bad_cases(bad: dict[str, object]) -> None:
    with pytest.raises(CaseError):
        validate_case(bad)


def test_value_matching() -> None:
    assert numbers_in("Sales were $12,345.67, up 3.2%.") == [12345.67, 3.2]
    assert value_in_text("usd", 12345.67, "Sales were $12,345.67")
    assert value_in_text("usd", 12345.67, "about $12,346")
    assert not value_in_text("usd", 12345.67, "about $12,000")
    assert value_in_text("pct1", -32.0 / 10, "down 3.2%")
    assert value_in_text("count", 214, "We rang 214 orders")
    assert not value_in_text("count", 214, "We rang 2140 orders")
    assert value_in_text("text", "dine_in", "Dine-in led at 60%")


def test_extract_field() -> None:
    data = {"channels": [{"channel": "dine_in", "share_pct": 61.2}], "current": {"orders": 3}}
    assert extract_field(data, "current.orders") == 3
    assert extract_field(data, "channels.0.share_pct") == 61.2
    assert extract_field(data, "channels.channel=dine_in.share_pct") == 61.2
    with pytest.raises(LookupError, match="unresolved field"):
        extract_field(data, "current.nope")
