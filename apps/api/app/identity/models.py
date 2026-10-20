import uuid
from datetime import UTC, datetime, time

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Time, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.db import Base, CIText
from app.ids import new_id

ROLES = ("owner", "admin", "manager", "employee")


def _now() -> datetime:
    return datetime.now(UTC)


_NOW = text("now()")


class User(Base):
    """Global (no RLS): login looks users up by email before the org is known."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(CIText(), unique=True)
    password_hash: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    timezone: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),
        CheckConstraint("role IN ('owner', 'admin', 'manager', 'employee')", name="role"),
        Index("ix_memberships_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (Index("ix_locations_org_id", "org_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String)
    address: Mapped[str] = mapped_column(String, default="", server_default="")
    timezone: Mapped[str] = mapped_column(String)
    business_day_start: Mapped[time] = mapped_column(
        Time, default=time(4, 0), server_default=text("'04:00'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class LocationAccess(Base):
    """For manager/employee roles; unused in Phase 0."""

    __tablename__ = "location_access"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"), primary_key=True)
