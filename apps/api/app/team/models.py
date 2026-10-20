"""Team entities (docs/specs/team.md).

`staff_members` is the Employee. The integration layer writes its POS-owned fields
(display_name, job_title, hourly_rate, active) for Square employees; everything else is
team-owned and never touched by sync. The table keeps its name so pipeline SQL and
`external_refs.entity_type = 'staff_member'` stay as they are. Every table carries `org_id`
and sits under RLS.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.db import Base, CIText
from app.ids import new_id


def _now() -> datetime:
    return datetime.now(UTC)


_NOW = text("now()")


class StaffMember(Base):
    """The Employee. Square team members link to it through external_refs."""

    __tablename__ = "staff_members"
    __table_args__ = (
        Index("ix_staff_members_org_id", "org_id"),
        UniqueConstraint("org_id", "user_id", name="uq_staff_members_org_user"),
        CheckConstraint("source IN ('square', 'manual')", name="source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    # POS-owned for Square employees (sync writes them); editable for manual ones.
    display_name: Mapped[str] = mapped_column(String)
    job_title: Mapped[str | None] = mapped_column(String, default=None)
    hourly_rate: Mapped[int | None] = mapped_column(BigInteger, default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    # Team-owned.
    email: Mapped[str | None] = mapped_column(CIText(), default=None)
    phone: Mapped[str | None] = mapped_column(String, default=None)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    rate_override: Mapped[int | None] = mapped_column(BigInteger, default=None)
    source: Mapped[str] = mapped_column(String, default="square", server_default="square")
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff_members.id"), default=None
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )


class Timecard(Base):
    __tablename__ = "timecards"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_timecards_conn_external"),
        Index("ix_timecards_org_location_date", "org_id", "location_id", "business_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("integration_connections.id"))
    external_id: Mapped[str] = mapped_column(String)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    # Null when the team member is not linked (counted in location labor, not per-employee views).
    staff_member_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff_members.id"), default=None
    )
    external_staff_ref: Mapped[str] = mapped_column(String)
    business_date: Mapped[date] = mapped_column(Date)
    clock_in: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clock_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    break_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    hourly_rate: Mapped[int] = mapped_column(BigInteger)
    paid_hours: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    cost: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String, default="USD", server_default="USD")
    source: Mapped[str] = mapped_column(String)
    source_version: Mapped[int] = mapped_column(BigInteger)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Position(Base):
    """A role on the schedule (Server, Line Cook). Org-wide; names are unique ignoring case."""

    __tablename__ = "positions"
    __table_args__ = (Index("uq_positions_org_name", "org_id", text("lower(name)"), unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String)
    color: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class EmployeePosition(Base):
    __tablename__ = "employee_positions"
    __table_args__ = (
        # At most one primary position per employee.
        Index(
            "uq_employee_positions_primary",
            "employee_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        Index("ix_employee_positions_position_id", "position_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("staff_members.id", ondelete="CASCADE"), primary_key=True
    )
    position_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("positions.id"), primary_key=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Station(Base):
    """An optional label on a shift (Grill, Patio), per location."""

    __tablename__ = "stations"
    __table_args__ = (Index("ix_stations_location_id", "location_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    name: Mapped[str] = mapped_column(String)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class EmployeeLocation(Base):
    __tablename__ = "employee_locations"
    __table_args__ = (Index("ix_employee_locations_location_id", "location_id"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("staff_members.id", ondelete="CASCADE"), primary_key=True
    )
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), primary_key=True)
