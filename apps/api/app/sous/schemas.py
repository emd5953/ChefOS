import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.team.schemas import ShiftIn

Kind = Literal["answer", "clarify", "refuse", "unsupported", "evidence_only"]
Mode = Literal["model", "fallback"]
BreakerState = Literal["closed", "open", "half_open"]
Unit = Literal["usd_cents", "count", "percent", "percent_pts", "hours", "g", "ml", "ea"]


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: Annotated[str, StringConstraints(max_length=2000)]


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: uuid.UUID | None = None
    turn_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    location_id: uuid.UUID
    history: list[HistoryTurn] = Field(default_factory=list, max_length=0)


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


class ScheduleProposalOut(BaseModel):
    id: uuid.UUID
    location_id: uuid.UUID
    schedule_id: uuid.UUID
    week_start: date
    expected_version: int
    payload: dict[str, Any]
    position_name: str
    employee_name: str | None
    status: str
    expires_at: datetime
    created_at: datetime


class SousAnswer(BaseModel):
    conversation_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    conversation_version: int | None = None
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
    proposals: list[ScheduleProposalOut] = Field(default_factory=list)


class ScheduleProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: uuid.UUID
    week_start: date
    shift: ShiftIn


class ScheduleProposalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool


class StatusOut(BaseModel):
    provider: Literal["ollama", "fake"]
    model: str
    available: bool  # the provider is reachable and the configured model is pulled
    model_pulled: bool
    prompt_version: str
    breaker: BreakerState = "closed"


# --- stream events (POST /v1/sous/ask/stream) ------------------------------------------------


class ConversationEvent(BaseModel):
    conversation_id: uuid.UUID
    turn_id: uuid.UUID


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


SousEvent = ConversationEvent | StatusEvent | ToolEvent | SousAnswer | Heartbeat


# --- daily briefing (GET /v1/sous/briefing, SOUS-2) ------------------------------------------
# Deterministic: built from domain services, never from the model.

SectionStatus = Literal["ok", "no_data", "unavailable"]


class BriefingSection(BaseModel):
    """Common envelope. `unavailable` means the source failed or isn't connected; `no_data` means
    it answered with nothing (no orders, no published schedule, no pars)."""

    status: SectionStatus
    source: str  # the underlying service, named when the section is unavailable
    business_date: date | None  # None for current-state sections (stock levels)
    as_of: datetime | None
    note: str | None = None


class BriefingSales(BriefingSection):
    net_sales: int | None = None
    orders: int | None = None
    avg_check: int | None = None
    compare_date: date | None = None  # the same weekday a week earlier
    compare_net_sales: int | None = None
    net_sales_change_pct: float | None = None


class BriefingLabor(BriefingSection):
    actual_hours: float | None = None
    actual_cost: int | None = None
    labor_pct: float | None = None  # of the same day's net sales


class BriefingShift(BaseModel):
    employee_name: str | None  # None: an open (unassigned) shift
    position_name: str
    starts_at: datetime
    ends_at: datetime
    paid_hours: float


class BriefingStaffing(BriefingSection):
    published_at: datetime | None = None
    shifts: int | None = None
    scheduled_hours: float | None = None
    unassigned_shifts: int | None = None
    first_start: datetime | None = None
    last_end: datetime | None = None
    by_position: dict[str, int] = Field(default_factory=dict)
    shift_list: list[BriefingShift] = Field(default_factory=list)


class BriefingItem(BaseModel):
    item_id: uuid.UUID
    name: str
    category: str
    base_unit: str
    on_hand: float
    par: float
    shortfall: float
    as_of: datetime | None


class BriefingStock(BriefingSection):
    below_par_count: int | None = None
    items: list[BriefingItem] = Field(default_factory=list)
    items_without_par: int | None = None


class BriefingFreshness(BaseModel):
    stream: Literal["orders", "timecards", "catalog", "team_members"]
    data_as_of: datetime | None
    status: Literal["fresh", "stale", "failing", "not_connected"]


class BriefingOut(BaseModel):
    location_id: uuid.UUID
    location_name: str
    timezone: str
    business_date: date  # the briefing's day: staffing is for this date
    results_date: date  # the last completed business day: sales and labor
    generated_at: datetime
    currency: Literal["USD"] = "USD"
    highlights: list[str]
    sales: BriefingSales
    labor: BriefingLabor
    staffing: BriefingStaffing
    stock: BriefingStock
    evidence: list[Evidence]
    freshness: list[BriefingFreshness]
    limitations: list[str]
    unavailable: list[str]  # sources that failed or aren't connected


# --- Team and Inventory tool facts (get_labor_summary, get_schedule, find_items_below_par) ----
# Typed tool data, converted to a server-rendered fact catalog before model use.
# `*_usd` is dollars, `*_pct` percent; hours and quantities are plain numbers.


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
    shifts: list[ScheduleShiftFacts]  # ranges of up to 2 days only, capped; see `shifts_shown`
    shifts_shown: int
    shifts_note: str | None = None  # why `shifts` is empty for a longer range
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
