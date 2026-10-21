import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Kind = Literal["answer", "clarify", "refuse", "unsupported", "evidence_only"]
Mode = Literal["model", "fallback"]
BreakerState = Literal["closed", "open", "half_open"]
Unit = Literal["usd_cents", "count", "percent", "percent_pts", "hours", "g", "ml", "ea"]


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: Annotated[str, StringConstraints(max_length=2000)]


class AskIn(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    location_id: uuid.UUID
    history: list[HistoryTurn] = Field(default_factory=list, max_length=10)


class Period(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from")
    to: date
    timezone: str


class Evidence(BaseModel):
    label: str
    value: int | float
    unit: Unit
    source: str  # tool name
    as_of: datetime | None


class ToolUse(BaseModel):
    name: str
    args: dict[str, Any]
    ms: int
    ok: bool


class SousAnswer(BaseModel):
    kind: Kind
    answer: str
    period: Period | None
    evidence: list[Evidence]
    limitations: list[str]
    tools_used: list[ToolUse]
    model: str
    prompt_version: str
    request_id: str
    mode: Mode = "model"


class StatusOut(BaseModel):
    provider: Literal["ollama", "fake"]
    model: str
    available: bool  # the provider is reachable and the configured model is pulled
    model_pulled: bool
    prompt_version: str
    breaker: BreakerState = "closed"


# --- stream events (POST /v1/sous/ask/stream) ------------------------------------------------


class StatusEvent(BaseModel):
    label: str


class ToolEvent(ToolUse):
    label: str


class ErrorEvent(BaseModel):
    status: int
    code: str
    detail: str


class Heartbeat(BaseModel):
    """Sent as an SSE comment while the model is thinking, so proxies keep the stream open."""


SousEvent = StatusEvent | ToolEvent | SousAnswer | Heartbeat


# --- Team and Inventory tool facts (get_labor_summary, get_schedule, find_items_below_par) ----
# What these tools hand the model, dumped to JSON. Field names carry their unit for the grounding
# check: `*_usd` is dollars, `*_pct` percent; hours and quantities are plain numbers.


class FactsPeriod(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from")
    to: date
    days: int


class LaborDay(BaseModel):
    date: date
    weekday: str
    timecards: bool  # False: no timecards, so labor is unknown (not zero)
    labor_hours: float | None
    labor_cost_usd: float | None
    net_sales_usd: float
    labor_pct: float | None


class LaborFacts(BaseModel):
    period: FactsPeriod
    days_with_timecards: int
    days_without_timecards: list[date]  # capped; the count is the complement of the above
    labor_hours: float | None  # actual paid hours from timecards; None when none were recorded
    labor_cost_usd: float | None
    net_sales_usd: float
    labor_pct: float | None  # labor cost / net sales, as in get_sales_summary
    days: list[LaborDay] | None  # day by day for short ranges only
    note: str | None = None


class ScheduleShiftFacts(BaseModel):
    date: date  # business date the shift starts on
    weekday: str
    employee: str  # "Unassigned" for an open shift
    position: str
    station: str | None
    starts: str  # local clock time, "5pm"
    ends: str
    ends_next_day: bool  # overnight
    paid_hours: float


class ScheduleDayFacts(BaseModel):
    date: date
    weekday: str
    published: bool  # False: no published schedule covers this day
    shifts: int
    scheduled_hours: float


class ScheduleFacts(BaseModel):
    period: FactsPeriod
    status: Literal["published"] = "published"  # drafts are never shown
    shift_count: int
    scheduled_hours: float
    people_scheduled: int
    days: list[ScheduleDayFacts]
    shifts: list[ScheduleShiftFacts]  # capped; see `shifts_shown`
    shifts_shown: int
    unpublished_dates: list[date]
    draft_has_unpublished_changes: bool
    note: str | None = None


class ParItemFacts(BaseModel):
    name: str
    category: str
    base_unit: Literal["g", "ml", "ea"]
    on_hand: float
    par: float
    shortfall: float  # par - on_hand
    never_counted: bool  # no stock record here yet, so on hand reads 0


class ItemFacts(BaseModel):
    items_tracked: int  # active inventory items
    items_with_par: int
    below_par_count: int
    items: list[ParItemFacts]  # most depleted first, capped; see `items_shown`
    items_shown: int
    items_without_par: int
    without_par_names: list[str]  # capped
    stock_as_of_local: str | None  # latest stock change at this location
    note: str | None = None
