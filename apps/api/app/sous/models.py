"""Private, server-owned Sous conversations. Both org and user scope are required by RLS."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.ids import new_id


class Conversation(Base):
    __tablename__ = "sous_conversations"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "id", name="uq_sous_conversation_owner"),
        Index("ix_sous_conversations_expiry", "expires_at", "id"),
        ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    location_id: Mapped[uuid.UUID]
    version: Mapped[int] = mapped_column(default=0, server_default="0")
    active_attempt_id: Mapped[uuid.UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now() + interval '24 hours'")
    )


class Turn(Base):
    __tablename__ = "sous_turns"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "client_turn_id", name="uq_sous_turn_request"),
        Index("ix_sous_turns_history", "org_id", "user_id", "conversation_id", "parent_version"),
        ForeignKeyConstraint(
            ["org_id", "user_id", "conversation_id"],
            ["sous_conversations.org_id", "sous_conversations.user_id", "sous_conversations.id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    conversation_id: Mapped[uuid.UUID]
    client_turn_id: Mapped[uuid.UUID]
    question: Mapped[str] = mapped_column(String)
    parent_version: Mapped[int]
    attempt_id: Mapped[uuid.UUID]
    state: Mapped[str] = mapped_column(String)
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ScheduleProposal(Base):
    """A 24 hour, manager reviewed proposal; payload is validated again on approval."""

    __tablename__ = "sous_schedule_proposals"
    __table_args__ = (
        Index("ix_sous_schedule_proposals_inbox", "org_id", "location_id", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    expected_version: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String, default="pending", server_default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
