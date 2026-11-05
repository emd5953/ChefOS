"""Number grounding: every figure in an answer must appear in the tool results.

Pure functions, no I/O. Tool data carries money in dollars, percents in percent units and counts
as integers, so a figure is grounded when it equals a tool value of the same unit exactly or
rounded (money as dollars at 0 or 2 decimals, percents at 0 or 1, counts exact). A "$" figure must
match a money field and a "%" figure a percent field. Dates, clock times, years, ordinals, list
markers and digits inside item names are exempt. Shorthand such as "$52.9k" is never accepted: the
prompt tells the model to write exact figures.
"""

import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_ORD = r"(?:st|nd|rd|th)?"
_YEAR = r"(?:19|20)\d{2}"
_DAY = rf"\d{{1,2}}{_ORD}(?!\d|[,.]\d|%)"

_EXEMPT = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO date
    # "March 2", "Mar 2-8", "March 2 to March 8", "March 2, 2026"
    re.compile(
        rf"\b{_MONTH}\.?\s+{_DAY}"
        rf"(?:\s*(?:-|\u2013|\u2014|to|through|thru)\s*(?:{_MONTH}\.?\s+)?{_DAY})?"
        rf"(?:,?\s+{_YEAR}\b)?"
    ),
    re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH}\b(?:,?\s+{_YEAR}\b)?"),  # "2 March"
    re.compile(
        r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:\s*(?:-|\u2013|\u2014|to)\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?)?"
    ),
    re.compile(r"\b\d{1,2}(?::\d{2})?\s?[ap]\.?m\b\.?", re.IGNORECASE),  # 7pm, 11:30 a.m.
    re.compile(r"\b\d{1,2}:\d{2}\b"),  # 04:00
    re.compile(r"\b\d{1,2}(?:st|nd|rd|th)\b"),  # 3rd
    re.compile(rf"(?<![\w$.,]){_YEAR}(?![\d,%]|\.\d)"),  # bare year
    re.compile(r"(?m)^\s*\d{1,2}[.)](?=\s)"),  # "1." list markers
]
_NUMBER = re.compile(
    r"(?<![\w.,])(\$)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(%|k\b|m\b|\s(?:thousand|million)\b)?",
    re.IGNORECASE,
)


def _numeric_leaves(value: Any, key: str = "") -> Iterable[tuple[str, int | float]]:
    """Numeric leaves with the name of the field that holds them (lists inherit it)."""
    if isinstance(value, bool):
        return
    if isinstance(value, int | float):
        yield key, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _numeric_leaves(v, str(k))
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _numeric_leaves(v, key)


def _text_leaves(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _text_leaves(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _text_leaves(v)


def _kind(key: str) -> str:
    """Tool fields are named for their unit: `*_usd` is dollars, `*_pct` / `*_pts` are percents."""
    if key.endswith("_usd"):
        return "money"
    return "percent" if "pct" in key or key.endswith("pts") else "other"


def allowed_numbers(results: Iterable[Any]) -> dict[str, set[Decimal]]:
    """Values the tools returned, by unit, as absolute values with their allowed roundings.

    Money is accepted at 0 or 2 decimals, percents at 0 or 1, anything else (counts, hours,
    quantities) exactly or rounded to 0 to 2 decimals. The exact value is always accepted.
    """
    places = {"money": ("1", "0.01"), "percent": ("1", "0.1"), "other": ("1", "0.1", "0.01")}
    allowed: dict[str, set[Decimal]] = {"money": set(), "percent": set(), "other": set()}
    for result in results:
        for key, v in _numeric_leaves(result):
            kind = _kind(key)
            d = abs(Decimal(str(v)))
            allowed[kind].add(d)
            for p in places[kind]:
                allowed[kind].add(d.quantize(Decimal(p), rounding=ROUND_HALF_UP))
    return allowed


def _blank(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def ungrounded_numbers(answer: str, results: Iterable[Any]) -> list[str]:
    """Figures in `answer` that don't match any tool value. Empty means the answer is grounded."""
    results = list(results)
    text = answer
    # Digits inside names the tools returned ("12oz Ribeye") are data, not claims.
    names = {s for r in results for s in _text_leaves(r) if len(s) >= 3 and re.search(r"\d", s)}
    for name in sorted(names, key=len, reverse=True):
        text = _blank(text, re.compile(re.escape(name), re.IGNORECASE))
    for pattern in _EXEMPT:
        text = _blank(text, pattern)
    allowed = allowed_numbers(results)
    bad: list[str] = []
    for m in _NUMBER.finditer(text):
        dollar, whole, frac, suffix = m.groups()
        shorthand = bool(suffix) and suffix != "%"
        value = Decimal(whole.replace(",", "") + (frac or ""))
        if dollar:  # a $ figure must be a money field, a % figure a percent field
            pool = allowed["money"]
        elif suffix == "%":
            pool = allowed["percent"]
        else:
            pool = allowed["money"] | allowed["percent"] | allowed["other"]
        if (shorthand or value not in pool) and m.group(0).strip() not in bad:
            bad.append(m.group(0).strip())
    return bad
