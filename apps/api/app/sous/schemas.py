import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Kind = Literal["answer", "clarify", "refuse", "unsupported", "evidence_only"]
Mode = Literal["model", "fallback"]
BreakerState = Literal["closed", "open", "half_open"]
Unit = Literal["usd_cents", "count", "percent", "percent_pts", "hours"]


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
