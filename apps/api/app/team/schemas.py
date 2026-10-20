import re
import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.identity.schemas import Email, Name

_PHONE_RE = re.compile(r"^[0-9+()\-. ]{3,40}$")
_COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")


def _phone(value: str) -> str:
    value = value.strip()
    if not _PHONE_RE.match(value):
        raise ValueError("Enter a valid phone number")
    return value


def _color(value: str) -> str:
    value = value.strip().lower()
    if not _COLOR_RE.match(value):
        raise ValueError("Must be a hex color like #2861f5")
    return value


def _name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Must not be blank")
    return value


Phone = Annotated[str, AfterValidator(_phone)]
Color = Annotated[str, AfterValidator(_color)]
Label = Annotated[str, Field(min_length=1, max_length=80), AfterValidator(_name)]
PersonName = Annotated[Name, AfterValidator(_name)]
# Hourly rate in cents; $1,000/h is a typo guard, not a policy.
Rate = Annotated[int, Field(ge=0, le=100_000)]
LoginStatus = Literal["none", "invited", "active"]


# ---------------------------------------------------------------- positions and stations


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    color: str
    archived_at: datetime | None


class PositionList(BaseModel):
    data: list[PositionOut]


class PositionCreate(BaseModel):
    name: Label
    color: Color | None = None


class PositionUpdate(BaseModel):
    name: Label | None = None
    color: Color | None = None
    archived: bool | None = None


class StationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    location_id: uuid.UUID
    name: str
    archived_at: datetime | None


class StationList(BaseModel):
    data: list[StationOut]


class StationCreate(BaseModel):
    location_id: uuid.UUID
    name: Label


class StationUpdate(BaseModel):
    name: Label | None = None
    archived: bool | None = None


# ---------------------------------------------------------------- employees


class EmployeePositionOut(BaseModel):
    id: uuid.UUID
    name: str
    color: str
    is_primary: bool


class EmployeeOut(BaseModel):
    id: uuid.UUID
    display_name: str
    email: str | None
    phone: str | None
    source: Literal["square", "manual"]
    active: bool
    job_title: str | None
    pos_hourly_rate: int | None
    rate_override: int | None
    effective_rate: int | None
    currency: Literal["USD"] = "USD"
    positions: list[EmployeePositionOut]
    location_ids: list[uuid.UUID]
    login: LoginStatus
    merged_into_id: uuid.UUID | None
    updated_at: datetime


class EmployeeList(BaseModel):
    data: list[EmployeeOut]


class PositionRef(BaseModel):
    position_id: uuid.UUID
    is_primary: bool = False


def _one_primary(refs: list[PositionRef]) -> list[PositionRef]:
    ids = [r.position_id for r in refs]
    if len(set(ids)) != len(ids):
        raise ValueError("Each position can be listed once")
    primaries = [r for r in refs if r.is_primary]
    if len(primaries) > 1:
        raise ValueError("Only one position can be primary")
    if refs and not primaries:  # the first one listed is primary unless one is marked
        refs = [refs[0].model_copy(update={"is_primary": True}), *refs[1:]]
    return refs


Positions = Annotated[list[PositionRef], Field(max_length=20), AfterValidator(_one_primary)]
LocationIds = Annotated[list[uuid.UUID], Field(max_length=200)]


class EmployeeCreate(BaseModel):
    display_name: PersonName
    email: Email | None = None
    phone: Phone | None = None
    location_ids: Annotated[LocationIds, Field(min_length=1)]
    positions: Positions = []
    rate_override: Rate | None = None


class EmployeeUpdate(BaseModel):
    """Team-owned fields. `display_name` and `active` are editable only for manual employees;
    `job_title` and `hourly_rate` are POS-owned and never editable (422 `pos_owned_field`)."""

    display_name: PersonName | None = None
    active: bool | None = None
    job_title: str | None = None
    hourly_rate: int | None = None
    email: Email | None = None
    phone: Phone | None = None
    rate_override: Rate | None = None
    location_ids: LocationIds | None = None
    positions: Positions | None = None

    @field_validator("location_ids")
    @classmethod
    def _locations(cls, value: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        if value is not None and not value:
            raise ValueError("Choose at least one location")
        return value


class MergeRequest(BaseModel):
    into_id: uuid.UUID


# ---------------------------------------------------------------- invitations

InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]


class InvitationCreate(BaseModel):
    """Invite an employee record (email defaults to the employee's) or, with no employee, a
    manager by email."""

    employee_id: uuid.UUID | None = None
    email: Email | None = None
    role: Literal["manager", "employee"] = "employee"
    location_ids: Annotated[LocationIds, Field(min_length=1)]


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: Literal["manager", "employee"]
    employee_id: uuid.UUID | None
    employee_name: str | None
    location_ids: list[uuid.UUID]
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None


class InvitationList(BaseModel):
    data: list[InvitationOut]


class InvitationCreated(BaseModel):
    invitation: InvitationOut
    accept_url: str  # shown once; only its hash is stored


class InvitationPreview(BaseModel):
    org_name: str
    email: str
    role: Literal["manager", "employee"]
    employee_name: str | None
    expires_at: datetime


class InvitationAccept(BaseModel):
    display_name: PersonName
    password: Annotated[str, Field(max_length=256)]


# ---------------------------------------------------------------- schedule

ConflictKind = Literal[
    "overlap",
    "time_off_approved",
    "time_off_pending",
    "unavailable",
    "inactive_employee",
    "not_assigned_to_location",
    "unassigned",
]
Notes = Annotated[str, Field(max_length=500)]
BreakMinutes = Annotated[int, Field(ge=0, le=600)]


class ShiftIn(BaseModel):
    """Times are ISO 8601 with an offset; the API derives the business date."""

    employee_id: uuid.UUID | None = None
    position_id: uuid.UUID
    station_id: uuid.UUID | None = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    break_minutes: BreakMinutes = 0
    notes: Notes = ""


class ShiftPatch(BaseModel):
    employee_id: uuid.UUID | None = None
    position_id: uuid.UUID | None = None
    station_id: uuid.UUID | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    break_minutes: BreakMinutes | None = None
    notes: Notes | None = None
    expected_updated_at: AwareDatetime


class ShiftOut(BaseModel):
    id: uuid.UUID
    location_id: uuid.UUID
    employee_id: uuid.UUID | None
    position_id: uuid.UUID
    station_id: uuid.UUID | None
    business_date: date
    starts_at: datetime
    ends_at: datetime
    break_minutes: int
    notes: str
    paid_hours: float
    cost: int | None  # cents; null when the employee has no rate (or the shift is unassigned)
    updated_at: datetime


class ConflictOut(BaseModel):
    shift_id: uuid.UUID
    kind: ConflictKind
    detail: str


class ScheduleMeta(BaseModel):
    id: uuid.UUID | None
    week_start: date
    version: int
    published_version: int | None
    published_at: datetime | None
    has_unpublished_changes: bool
    unpublished_changes: int  # shifts added, removed, or changed since the last publish


class ScheduleEmployee(BaseModel):
    id: uuid.UUID
    display_name: str
    active: bool
    primary_position_id: uuid.UUID | None
    position_ids: list[uuid.UUID]
    has_rate: bool
    assigned: bool  # works at this location


class Totals(BaseModel):
    scheduled_hours: float
    projected_cost: int
    unrated_shifts: int
    forecast_sales: int | None
    projected_labor_pct: float | None


class ScheduleDay(Totals):
    business_date: date
    forecast_basis: list[date]
    forecast_reason: Literal["not_connected", "not_enough_history"] | None


class ScheduleWeek(Totals):
    reference_labor_pct: float | None  # actual labor % over the last 4 complete weeks
    reference_period: dict[str, date]


class FreshnessOut(BaseModel):
    stream: Literal["orders", "timecards", "catalog", "team_members"]
    location_id: uuid.UUID
    data_as_of: datetime | None
    status: Literal["fresh", "stale", "failing", "not_connected"]


class ScheduleOut(BaseModel):
    location_id: uuid.UUID
    timezone: str
    currency: Literal["USD"] = "USD"
    schedule: ScheduleMeta
    shifts: list[ShiftOut]
    employees: list[ScheduleEmployee]
    days: list[ScheduleDay]
    week: ScheduleWeek
    conflicts: list[ConflictOut]
    freshness: list[FreshnessOut]


class CopyRequest(BaseModel):
    from_week_start: date


class CopyOut(BaseModel):
    copied: int
    unassigned: int  # copied without an employee (inactive, or overlapping elsewhere)


# ---------------------------------------------------------------- publish and notifications


class PublishRequest(BaseModel):
    expected_version: int
    acknowledge_conflicts: bool = False


class EmployeeRef(BaseModel):
    id: uuid.UUID
    display_name: str


class PublishOut(BaseModel):
    published_version: int | None
    changed: bool  # false for a no-op republish
    changed_employees: list[uuid.UUID]
    notified_users: int
    employees_without_login: list[EmployeeRef]


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    title: str
    body: str
    link: str | None
    entity_type: str | None
    entity_id: uuid.UUID | None
    created_at: datetime
    read_at: datetime | None


class NotificationList(BaseModel):
    data: list[NotificationOut]
    unread_count: int


class MarkRead(BaseModel):
    ids: Annotated[list[uuid.UUID], Field(max_length=200)] | None = None
    all: bool = False


class UnreadCount(BaseModel):
    unread_count: int


# ---------------------------------------------------------------- employee self-service (/v1/me)


class MyLocation(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str


class MyShift(BaseModel):
    """A published shift as its employee sees it: no rates or costs."""

    id: uuid.UUID
    location_id: uuid.UUID
    business_date: date
    starts_at: datetime
    ends_at: datetime
    break_minutes: int
    position_name: str
    position_color: str
    station_name: str | None
    notes: str


class TeamShift(BaseModel):
    """A teammate's published shift: name, position, and times only."""

    location_id: uuid.UUID
    business_date: date
    starts_at: datetime
    ends_at: datetime
    employee_name: str
    position_name: str
    position_color: str


class MyWeek(BaseModel):
    week_start: date
    employee: EmployeeRef
    published_at: datetime | None
    locations: list[MyLocation]
    my_shifts: list[MyShift]
    team_shifts: list[TeamShift]
