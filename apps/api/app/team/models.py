"""Team entities (docs/specs/team.md).

`staff_members` is the Employee. The integration layer writes its POS-owned fields
(display_name, job_title, hourly_rate, active) for Square employees; everything else is
team-owned and never touched by sync. The table keeps its name so pipeline SQL and
`external_refs.entity_type = 'staff_member'` stay as they are. Every table carries `org_id`
and sits under RLS.
"""

import uuid
from datetime import UTC, date, datetime, time
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
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.db import Base, CIText
from app.ids import new_id


def _now() -> datetime:
    return datetime.now(UTC)


_NOW = text("now()")
# The notification kinds deduplicated by uq_notifications_attendance (migration 0013).
ATTENDANCE_KINDS_SQL = "kind IN ('shift_late', 'shift_missed')"


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


class Invitation(Base):
    """A single-use login invitation. Only the SHA-256 of the token is stored; the link is shown
    once. Acceptance runs before any org scope exists, so it resolves the token through the
    `invitation_for_token` SECURITY DEFINER function (app.db.lookup_invitation)."""

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("role IN ('manager', 'employee')", name="role"),
        Index("ix_invitations_org_id_created_at", "org_id", "created_at"),
        Index("ix_invitations_employee_id", "employee_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    email: Mapped[str] = mapped_column(CIText())
    role: Mapped[str] = mapped_column(String)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff_members.id"), default=None
    )
    location_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid()), default=list)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    accepted_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Schedule(Base):
    """One location's week (Monday start). `version` is bumped by every shift write and drives
    optimistic concurrency on publish; `published_version` counts publishes."""

    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint("location_id", "week_start", name="uq_schedules_location_week"),
        CheckConstraint("extract(isodow from week_start) = 1", name="week_start_monday"),
        Index("ix_schedules_org_id", "org_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    week_start: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    published_version: Mapped[int | None] = mapped_column(Integer, default=None)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )


class Shift(Base):
    """The draft working copy. Employees never read this table; they read published_shifts."""

    __tablename__ = "shifts"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        CheckConstraint("break_minutes >= 0", name="break_minutes"),
        Index("ix_shifts_schedule_id", "schedule_id"),
        Index("ix_shifts_employee_id_starts_at", "employee_id", "starts_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"))
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff_members.id"), default=None
    )
    position_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("positions.id"))
    station_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stations.id"), default=None)
    business_date: Mapped[date] = mapped_column(Date)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    break_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    notes: Mapped[str] = mapped_column(String, default="", server_default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )


class PublishedShift(Base):
    """The snapshot employees see, replaced per schedule on publish. `id` is the draft shift's
    id, so republishing can diff old and new snapshots shift by shift."""

    __tablename__ = "published_shifts"
    __table_args__ = (
        Index("ix_published_shifts_schedule_id", "schedule_id"),
        Index("ix_published_shifts_employee_id_starts_at", "employee_id", "starts_at"),
        Index("ix_published_shifts_location_date", "location_id", "business_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"))
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff_members.id"), default=None
    )
    position_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("positions.id"))
    station_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stations.id"), default=None)
    business_date: Mapped[date] = mapped_column(Date)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    break_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    notes: Mapped[str] = mapped_column(String, default="", server_default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_version: Mapped[int] = mapped_column(Integer)


class Notification(Base):
    """In-app alert for one user. Clients poll; there is no external delivery."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_id_read_at", "user_id", "read_at"),
        # Clock-in alerts: at most one per recipient, shift (entity_id), and kind.
        Index(
            "uq_notifications_attendance",
            "user_id",
            "kind",
            "entity_id",
            unique=True,
            postgresql_where=text(ATTENDANCE_KINDS_SQL),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(String, default="", server_default="")
    link: Mapped[str | None] = mapped_column(String, default=None)
    entity_type: Mapped[str | None] = mapped_column(String, default=None)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AvailabilitySet(Base):
    """A weekly availability an employee submitted. The approved set with the latest
    `effective_from` on or before a date applies to that date; a new submission supersedes a
    pending one."""

    __tablename__ = "availability_sets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'superseded')", name="status"
        ),
        Index("ix_availability_sets_employee_id_status", "employee_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_members.id"))
    status: Mapped[str] = mapped_column(String, default="pending", server_default="pending")
    effective_from: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String, default="", server_default="")
    submitted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    decision_note: Mapped[str | None] = mapped_column(String, default=None)


class AvailabilityBlock(Base):
    """An unavailable window on a weekday (0 = Monday), in location-local time. Null times mean
    the whole day."""

    __tablename__ = "availability_blocks"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        CheckConstraint(
            "(start_time IS NULL AND end_time IS NULL) OR "
            "(start_time IS NOT NULL AND end_time IS NOT NULL AND start_time < end_time)",
            name="window",
        ),
        Index("ix_availability_blocks_set_id", "set_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("availability_sets.id", ondelete="CASCADE")
    )
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[time | None] = mapped_column(Time, default=None)
    end_time: Mapped[time | None] = mapped_column(Time, default=None)


class TimeOffRequest(Base):
    """Dates are inclusive. `start_time` applies to the first day and `end_time` to the last;
    null means from the start / to the end of that day (location-local)."""

    __tablename__ = "time_off_requests"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'denied', 'cancelled')", name="status"),
        CheckConstraint("ends_on >= starts_on", name="dates"),
        Index("ix_time_off_requests_employee_id_starts_on", "employee_id", "starts_on"),
        Index("ix_time_off_requests_org_id_status", "org_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_members.id"))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    start_time: Mapped[time | None] = mapped_column(Time, default=None)
    end_time: Mapped[time | None] = mapped_column(Time, default=None)
    reason: Mapped[str] = mapped_column(String, default="", server_default="")
    status: Mapped[str] = mapped_column(String, default="pending", server_default="pending")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    decision_note: Mapped[str | None] = mapped_column(String, default=None)


class ShiftCoverageRequest(Base):
    """Employee request to claim a published open shift. Approval updates only the draft."""

    __tablename__ = "shift_coverage_requests"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'denied', 'cancelled')", name="status"),
        Index("ix_shift_coverage_requests_location_status", "location_id", "status"),
        Index("ix_shift_coverage_requests_employee_status", "employee_id", "status"),
        Index(
            "uq_shift_coverage_requests_pending_shift",
            "published_shift_id",
            "employee_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"))
    published_shift_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("published_shifts.id", ondelete="CASCADE")
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_members.id"))
    status: Mapped[str] = mapped_column(String, default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    decision_note: Mapped[str | None] = mapped_column(String, default=None)


class ShiftTradeRequest(Base):
    """Employee-proposed swap of two published shifts; approval edits only the draft."""

    __tablename__ = "shift_trade_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_recipient', 'pending_manager', 'approved', 'denied', 'cancelled')",
            name="status",
        ),
        CheckConstraint("offered_shift_id <> requested_shift_id", name="distinct_shifts"),
        Index("ix_shift_trade_requests_location_status", "location_id", "status"),
        Index("ix_shift_trade_requests_requester_status", "requester_employee_id", "status"),
        Index("ix_shift_trade_requests_recipient_status", "recipient_employee_id", "status"),
        Index("ix_shift_trade_requests_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"))
    offered_shift_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("published_shifts.id", ondelete="CASCADE")
    )
    requested_shift_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("published_shifts.id", ondelete="CASCADE")
    )
    requester_employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_members.id"))
    recipient_employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_members.id"))
    status: Mapped[str] = mapped_column(
        String, default="pending_recipient", server_default="pending_recipient"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    decision_note: Mapped[str | None] = mapped_column(String, default=None)
