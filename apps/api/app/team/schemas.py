import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

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
