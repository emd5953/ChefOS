"""Integration layer tables: connections, streams, runs, raw landing, identity mapping."""

import uuid
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.db import Base
from app.ids import new_id

STREAMS = ("locations", "catalog", "orders", "team_members", "timecards")
CONNECTION_STATUSES = ("pending", "active", "needs_reauth", "error", "disconnected")


def _now() -> datetime:
    return datetime.now(UTC)


_NOW = text("now()")


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'active', 'needs_reauth', 'error', 'disconnected')",
            name="status",
        ),
        # One live connection per provider per org (a disconnected one may be replaced).
        Index(
            "uq_integration_connections_live",
            "org_id",
            "provider",
            unique=True,
            postgresql_where=text("status <> 'disconnected'"),
        ),
        Index("ix_integration_connections_external_account", "provider", "external_account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    provider: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String, default="fake", server_default="fake")
    status: Mapped[str] = mapped_column(String, default="pending", server_default="pending")
    external_account_id: Mapped[str | None] = mapped_column(String, default=None)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default=text("'{}'")
    )
    credential_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    credential_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    api_version: Mapped[str | None] = mapped_column(String, default=None)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=_NOW
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class IntegrationLocationLink(Base):
    __tablename__ = "integration_location_links"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_location_id", name="uq_location_links_ext"),
        Index("ix_integration_location_links_location_id", "location_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    external_location_id: Mapped[str] = mapped_column(String)
    external_name: Mapped[str] = mapped_column(String)
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id"), default=None)
    timezone: Mapped[str] = mapped_column(String)
    business_day_start: Mapped[time] = mapped_column(
        Time, default=time(4, 0), server_default=text("'04:00'")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class IntegrationStream(Base):
    __tablename__ = "integration_streams"
    __table_args__ = (
        PrimaryKeyConstraint("connection_id", "stream", name="pk_integration_streams"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    stream: Mapped[str] = mapped_column(String)
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error_class: Mapped[str | None] = mapped_column(String, default=None)
    last_error_detail: Mapped[str | None] = mapped_column(Text, default=None)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    backfill_target_from: Mapped[date | None] = mapped_column(Date, default=None)
    backfill_completed_through: Mapped[date | None] = mapped_column(Date, default=None)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_connection_started", "connection_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    stream: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    window_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    window_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    changed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_class: Mapped[str | None] = mapped_column(String, default=None)
    error_detail: Mapped[str | None] = mapped_column(Text, default=None)


class RawRecord(Base):
    __tablename__ = "raw_records"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "stream", "external_id", "payload_sha256", name="uq_raw_records_hash"
        ),
        Index(
            "ix_raw_records_pending",
            "connection_id",
            "stream",
            postgresql_where=text("normalize_status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    stream: Mapped[str] = mapped_column(String)
    external_id: Mapped[str] = mapped_column(String)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    source_version: Mapped[str | None] = mapped_column(String, default=None)
    payload_sha256: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_uri: Mapped[str | None] = mapped_column(String, default=None)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    normalize_status: Mapped[str] = mapped_column(
        String, default="pending", server_default="pending"
    )
    normalizer_version: Mapped[str | None] = mapped_column(String, default=None)
    normalize_error: Mapped[str | None] = mapped_column(Text, default=None)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (PrimaryKeyConstraint("provider", "event_id", name="pk_webhook_events"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    provider: Mapped[str] = mapped_column(String)
    event_id: Mapped[str] = mapped_column(String)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ExternalRef(Base):
    __tablename__ = "external_refs"
    __table_args__ = (
        PrimaryKeyConstraint(
            "connection_id", "entity_type", "external_id", name="pk_external_refs"
        ),
        Index("ix_external_refs_entity", "entity_type", "entity_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    entity_type: Mapped[str] = mapped_column(String)
    external_id: Mapped[str] = mapped_column(String)
    entity_id: Mapped[uuid.UUID] = mapped_column()


class MappingIssue(Base):
    __tablename__ = "mapping_issues"
    __table_args__ = (
        UniqueConstraint("connection_id", "kind", "external_id", name="uq_mapping_issues_key"),
        CheckConstraint(
            "kind IN ('unmapped_location', 'unmapped_item', 'unmapped_team_member', "
            "'unknown_channel')",
            name="kind",
        ),
        Index("ix_mapping_issues_org_id", "org_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String)
    external_id: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    occurrences: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
