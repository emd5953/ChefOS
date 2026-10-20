import re
import uuid
from datetime import datetime, time
from functools import lru_cache
from typing import Annotated
from zoneinfo import available_timezones

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.security.passwords import validate_password

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@lru_cache
def _timezones() -> frozenset[str]:
    return frozenset(available_timezones())


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL_RE.match(value):
        raise ValueError("Enter a valid email address")
    return value


def _timezone(value: str) -> str:
    if value not in _timezones():
        raise ValueError("Must be an IANA timezone, e.g. America/New_York")
    return value


def _hhmm(value: str | time) -> time:
    if isinstance(value, time):
        return value
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?", value.strip())
    if not match:
        raise ValueError("Must be a time like 04:00")
    return time(int(match.group(1)), int(match.group(2)))


Email = Annotated[str, AfterValidator(_email)]
Timezone = Annotated[str, AfterValidator(_timezone)]
Name = Annotated[str, Field(min_length=1, max_length=120)]


class SignupRequest(BaseModel):
    email: Email
    password: Annotated[str, Field(max_length=256)]
    display_name: Name
    org_name: Name
    location_name: Name
    timezone: Timezone

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        message = validate_password(value)
        if message:
            raise ValueError(message)
        return value

    @field_validator("display_name", "org_name", "location_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank")
        return value


class LoginRequest(BaseModel):
    email: Annotated[str, Field(max_length=254)]
    password: Annotated[str, Field(max_length=256)]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    display_name: str


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    timezone: str


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    address: str
    timezone: str
    business_day_start: time
    archived_at: datetime | None

    @field_serializer("business_day_start")
    def _ser_time(self, value: time) -> str:
        return value.strftime("%H:%M")


class SessionOut(BaseModel):
    user: UserOut
    org: OrgOut
    role: str
    csrf_token: str


class SignupOut(SessionOut):
    location: LocationOut


class LocationList(BaseModel):
    data: list[LocationOut]


class OrgUpdate(BaseModel):
    name: Name | None = None
    timezone: Timezone | None = None


class LocationCreate(BaseModel):
    name: Name
    address: Annotated[str, Field(max_length=300)] = ""
    timezone: Timezone
    business_day_start: Annotated[time, "HH:MM"] = time(4, 0)

    _parse_start = field_validator("business_day_start", mode="before")(_hhmm)


class LocationUpdate(BaseModel):
    name: Name | None = None
    address: Annotated[str, Field(max_length=300)] | None = None
    timezone: Timezone | None = None
    business_day_start: time | None = None

    @field_validator("business_day_start", mode="before")
    @classmethod
    def _parse_start(cls, value: str | time | None) -> time | None:
        return None if value is None else _hhmm(value)
