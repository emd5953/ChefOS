"""Semantic output boundary: the model selects facts; only code writes their text.

IDs are local to the current question, never resolved against conversation history. Each
fact binds its metric, entity, period, sign and unit. No model-authored prose is displayed.
"""

import json
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.sous.schemas import Kind
from app.sous.tools import ToolResult

METRICS = {
    "net_sales_usd": "Net sales",
    "gross_sales_usd": "Gross sales",
    "discounts_usd": "Discounts",
    "returns_usd": "Returns",
    "tips_usd": "Tips",
    "orders": "Orders",
    "avg_check_usd": "Average check",
    "labor_cost_usd": "Labor cost",
    "labor_hours": "Actual labor hours",
    "labor_pct": "Labor cost as a share of net sales",
    "total_net_sales_usd": "Net sales",
    "total_orders": "Orders",
    "total_labor_cost_usd": "Labor cost",
    "total_labor_pct": "Labor cost as a share of net sales",
    "days_with_timecards": "Days with timecards",
    "shift_count": "Published shifts",
    "scheduled_hours": "Published scheduled hours",
    "people_scheduled": "People scheduled",
    "items_tracked": "Inventory items tracked",
    "items_with_par": "Items with par levels",
    "below_par_count": "Items below par",
    "items_without_par": "Items without a par level",
    "unmapped_net_sales_usd": "Unmapped item net sales",
}
CONTROLLED = {
    "period": "Which day or date range do you mean?",
    "metric": "Which metric would you like to check?",
    "comparison": "Which periods would you like to compare?",
    "question": "What would you like to check, and for which day or date range?",
    "refuse": "I can help with restaurant operations using the available read-only tools.",
    "unsupported": "I can check sales, labor, published schedules, inventory below par, and data "
    "freshness for this location. I cannot make changes or perform actions.",
}


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["answer", "clarify", "refuse", "unsupported"]
    claims: list[str] = Field(default_factory=list, max_length=128)
    reason: Literal["period", "metric", "comparison", "question"] | None = None


def render(text: str, facts: dict[str, str]) -> tuple[Kind, str] | None:
    # Legacy control prefixes remain safe: discard all model prose, even numbers/actions.
    for kind in ("clarify", "refuse", "unsupported"):
        if text.upper().startswith(kind.upper() + ":"):
            if kind == "clarify":
                return "clarify", CONTROLLED["question"]
            if kind == "refuse":
                return "refuse", CONTROLLED[kind]
            return "unsupported", CONTROLLED[kind]
    try:
        selected = Selection.model_validate_json(text)
    except ValidationError:
        return None
    if selected.kind == "answer":
        ids = selected.claims
        if (
            selected.reason
            or not ids
            or len(set(ids)) != len(ids)
            or any(i not in facts for i in ids)
        ):
            return None
        return "answer", "\n".join(facts[i] for i in ids)
    if selected.claims:
        return None
    if selected.kind == "clarify":
        return "clarify", CONTROLLED[selected.reason or "question"]
    if selected.reason:
        return None
    return selected.kind, CONTROLLED[selected.kind]


def _quote(value: Any) -> str:
    # Quotes and escapes make external text an explicitly attributed name, not a sentence.
    return json.dumps(str(value), ensure_ascii=False)


def _number(value: Any) -> str:
    rendered = format(Decimal(str(value)), ",f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _value(key: str, value: Any) -> str:
    if value is None:
        return "unavailable"
    if key.endswith("_usd"):
        return f"${value:,.2f}"
    if key.endswith("_pts"):
        return f"{'+' if value >= 0 else ''}{_number(value)} percentage points"
    if key.endswith("_pct"):
        return f"{_number(value)}%"
    if "hours" in key:
        return f"{_number(value)} hours"
    return _number(value)


def catalog(result: ToolResult, index: int) -> dict[str, str]:
    """Explicit field allowlist; arbitrary data keys/strings never become output instructions."""
    if not result.ok:
        return {}
    d = result.data
    facts: dict[str, str] = {}
    span = f"{result.period[0]} to {result.period[1]}" if result.period else "current inventory"
    if result.tool == "get_today":
        span += " so far"

    def add(key: str, sentence: str) -> None:
        facts[f"r{index}.{key}"] = sentence

    def metrics(data: dict[str, Any], period: str, prefix: str = "") -> None:
        for key, label in METRICS.items():
            if key in data:
                add(prefix + key, f"{label} ({period}): {_value(key, data[key])}.")

    metrics(d, span)
    # These notes are authored by our tools, never external names or provider prose.
    for i, note in enumerate([d.get("note"), *result.notes, d.get("shifts_note")]):
        if note:
            add(f"note.{i}", f"{span}: {note}")
    comparison = d.get("comparison")
    if comparison:
        p = comparison["period"]
        previous = f"{p['from']} to {p['to']}"
        metrics(comparison, previous, "comparison.")
        labels = {
            "net_sales_usd": "Net sales change",
            "net_sales_pct": "Net sales change",
            "orders": "Orders change",
            "orders_pct": "Orders change",
            "avg_check_pct": "Average check change",
            "labor_pct_pts": "Labor share change",
        }
        for key, label in labels.items():
            value = comparison["change"].get(key)
            if value is not None:
                add(f"change.{key}", f"{label} ({span} vs {previous}): {_value(key, value)}.")
    for i, day in enumerate(d.get("days") or []):
        day_span = str(day["date"])
        if "published" in day and not day["published"]:
            add(f"day.{i}", f"No schedule is published for {day_span}.")
            continue
        fields = [
            f"{label.lower()} {_value(key, day[key])}"
            for key, label in METRICS.items()
            if key in day
        ]
        if "published" in day:
            fields.insert(0, f"published shifts {day['shifts']}")
        add(f"day.{i}", f"{day_span}: {', '.join(fields)}.")
    for key, label in (
        ("best_day", "Highest net sales day"),
        ("slowest_day", "Lowest net sales day"),
    ):
        if key in d:
            day = d[key]
            add(
                key,
                f"{label} among days with orders ({span}): {day['date']}, "
                f"{_value('net_sales_usd', day['net_sales_usd'])}.",
            )
    for i, item in enumerate(d.get("items", [])):
        name = _quote(item["name"])
        if result.tool == "get_top_items":
            add(
                f"item.{i}",
                f"Item {name} ({span}): {_number(item['quantity'])} sold, "
                f"{_value('net_sales_usd', item['net_sales_usd'])} net sales, "
                f"{_value('share_pct', item['share_pct'])} of item net sales.",
            )
        elif result.tool == "find_items_below_par":
            unit = item["base_unit"]
            add(
                f"item.{i}",
                f"Item {name} (current inventory): {_number(item['on_hand'])} {unit} "
                f"on hand, par {_number(item['par'])} {unit}, "
                f"shortfall {_number(item['shortfall'])} {unit}."
                + (
                    " This item has never been counted or received here."
                    if item["never_counted"]
                    else ""
                ),
            )
    for i, shift in enumerate(d.get("shifts", [])):
        station = f", station {_quote(shift['station'])}" if shift["station"] else ""
        overnight = " next day" if shift["ends_next_day"] else ""
        add(
            f"shift.{i}",
            f"Published shift ({shift['date']}): {_quote(shift['employee'])}, "
            f"{_quote(shift['position'])}{station}, "
            f"{shift['starts']} to {shift['ends']}{overnight}, "
            f"{_number(shift['paid_hours'])} paid hours.",
        )
    for i, channel in enumerate(d.get("channels", [])):
        add(
            f"channel.{i}",
            f"Channel {_quote(channel['channel'])} ({span}): "
            f"{_value('net_sales_usd', channel['net_sales_usd'])} net sales, "
            f"{_value('share_pct', channel['share_pct'])} of net sales.",
        )
    for i, hour in enumerate(d.get("by_hour", [])):
        add(
            f"hour.{i}",
            f"Local hour {hour['hour']:02d}:00 ({span}): "
            f"{_value('net_sales_usd', hour['net_sales_usd'])} net sales, {hour['orders']} orders.",
        )
    if "busiest_hour" in d:
        add("busiest_hour", f"Highest net sales local hour ({span}): {d['busiest_hour']:02d}:00.")
    if "last_order_at" in d:
        add(
            "last_order_at", f"Last order closed ({span}): {d['last_order_at'] or 'none recorded'}."
        )
    if d.get("without_par_names"):
        add(
            "without_par_names",
            "Items without par levels (listed names): "
            + ", ".join(_quote(name) for name in d["without_par_names"])
            + ".",
        )
    if "stock_as_of_local" in d:
        add(
            "stock_as_of_local",
            "Stock last updated (location time): "
            + (d["stock_as_of_local"] or "no updates recorded")
            + ".",
        )
    for i, stream in enumerate(d.get("streams", [])):
        updated = stream["updated_at_local"] or "never"
        age = stream.get("minutes_since_update")
        elapsed = f" ({_number(age)} minutes ago)" if age is not None else ""
        add(
            f"stream.{i}",
            f"{stream['stream']} data: {stream['status']}; last updated {updated} "
            f"(location time){elapsed}.",
        )
    return facts
