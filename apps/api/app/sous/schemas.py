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
