"""No-model fallback: answers a few fixed question shapes when the language model is unavailable.

Only runs when the model can't be reached (docs/specs/sous.md, v1.1). It matches one named period
plus a totals-style question, runs the same tool the model would, and fills a fixed template
with the tool's exact figures. Anything it doesn't recognize gets no answer, never a guess.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.sous.agent import prompt

LIMITATION = (
    "The assistant model is unavailable, so this is a fixed-format answer from the same figures."
)

_METRIC = re.compile(
    r"\b(sales|revenue|sold|sell|orders?|tickets|covers|average check|avg check|labou?r|"
    r"compare[sd]?|"
    r"how (?:did|are|were|is) we (?:do|doing))\b"
)
_LABOR = re.compile(r"\blabou?r\b")
# Questions the fixed templates can't answer, even though they mention sales.
_OUT_OF_SCOPE = re.compile(
    r"\b(items?|dish(?:es)?|menu|best.?sellers?|top|worst|schedule|shifts?|working|inventory|"
    r"par|waste|payroll|wages?|staff|employees?|channels?|delivery|pickup|dine.?in|other|"
    r"another|competitors?|forecast|tomorrow|next)\b"
)
_NAMED_PLACE = re.compile(r"\b[A-Z][a-z]+ (?:location|branch|store|restaurant)\b")
_PREVIOUS_YEAR = re.compile(r"\b(last year|a year ago|year over year|yoy|same \w+ last year)\b")
_PREVIOUS_PERIOD = re.compile(
    r"\b(compare[sd]?|comparison|vs\.?|versus|week before|month before|previous|prior|than)\b"
)

# Pattern -> the name used in prompt.named_ranges.
_PERIODS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(today|tonight)\b"), "today", "Today so far"),
    (re.compile(r"\byesterday\b"), "yesterday", "Yesterday"),
    (re.compile(r"\bthis week\b"), "this week so far (Monday to today)", "This week so far"),
    (re.compile(r"\blast week\b"), "last week (Monday to Sunday)", "Last week"),
    (re.compile(r"\b(?:last|past) (?:7|seven) days\b"), "last 7 days", "The last 7 days"),
    (re.compile(r"\b(?:last|past) (?:30|thirty) days\b"), "last 30 days", "The last 30 days"),
    (
        re.compile(r"\b(this month|month to date|mtd)\b"),
        "this month so far",
        "This month so far",
    ),
    (re.compile(r"\blast month\b"), "last month", "Last month"),
]


@dataclass(frozen=True)
class Plan:
    tool: str
    args: dict[str, Any]
    title: str  # "Last week"
    labor: bool


def match(question: str, today: date) -> Plan | None:
    """The tool call for a question the templates can answer, or None."""
    if _NAMED_PLACE.search(question):
        return None
    q = question.lower()
    if not _METRIC.search(q) or _OUT_OF_SCOPE.search(q):
        return None
    hits = [(name, title) for pattern, name, title in _PERIODS if pattern.search(q)]
    if len(hits) != 1:
        return None
    name, title = hits[0]
    compare = (
        "previous_year"
        if _PREVIOUS_YEAR.search(q)
        else "previous_period"
        if _PREVIOUS_PERIOD.search(q)
        else None
    )
    labor = bool(_LABOR.search(q))
    if name == "today":
        # get_today has no comparison or labor figures.
        return None if compare or labor else Plan("get_today", {}, title, labor=False)
    first, last = dict(prompt.named_ranges(today))[name]
    args: dict[str, Any] = {"from": first.isoformat(), "to": last.isoformat()}
    if compare:
        args["compare"] = compare
    return Plan("get_sales_summary", args, title, labor)


def money(dollars: float) -> str:
    sign = "-" if dollars < 0 else ""
    return f"{sign}${abs(dollars):,.2f}"


def _span(period: dict[str, Any]) -> str:
    first, last = date.fromisoformat(period["from"]), date.fromisoformat(period["to"])
    if first == last:
        return f"{first:%b} {first.day}"
    return f"{first:%b} {first.day} \u2013 {last:%b} {last.day}"


def _change(label: str, value: float | None, unit: str = "%") -> str | None:
    if value is None:
        return None
    if value == 0:
        return f"{label} flat"
    word = "up" if value > 0 else "down"
    return f"{label} {word} {abs(value):.1f}{unit}"


def render(plan: Plan, data: dict[str, Any]) -> str:
    """The answer text, built only from the tool's own figures."""
    if plan.tool == "get_today":
        parts = [f"net sales {money(data['net_sales_usd'])}", f"{data['orders']} orders"]
        if data.get("avg_check_usd") is not None:
            parts.append(f"average check {money(data['avg_check_usd'])}")
        day = date.fromisoformat(data["business_date"])
        return f"{plan.title} ({day:%b} {day.day}): {', '.join(parts)}."

    parts = [f"net sales {money(data['net_sales_usd'])}", f"{data['orders']} orders"]
    if data.get("avg_check_usd") is not None:
        parts.append(f"average check {money(data['avg_check_usd'])}")
    if data.get("labor_pct") is not None:
        labor = f"labor {data['labor_pct']:.1f}% ({money(data['labor_cost_usd'])})"
        # Lead with labor when that's what was asked.
        parts = [labor, *parts] if plan.labor else [*parts, labor]
    text = f"{plan.title} ({_span(data['period'])}): {', '.join(parts)}."

    cmp = data.get("comparison")
    if cmp:
        ch = cmp["change"]
        changes = [
            c
            for c in (
                _change("net sales", ch.get("net_sales_pct")),
                _change("orders", ch.get("orders_pct")),
                _change("average check", ch.get("avg_check_pct")),
                _change("labor %", ch.get("labor_pct_pts"), " pts"),
            )
            if c
        ]
        against = (
            "the same days last year" if cmp["kind"] == "previous_year" else "the period before"
        )
        text += (
            f" Compared with {against} ({_span(cmp['period'])}, net sales "
            f"{money(cmp['net_sales_usd'])}): {', '.join(changes) or 'no comparable figures'}."
        )
    return text
