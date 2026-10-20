import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.db import Base
from app.ids import new_id


def _now() -> datetime:
    return datetime.now(UTC)


class SessionRecord(Base):
    """Login sessions. No RLS: only the auth code reads this, before org scope exists."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    csrf_token: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    user_agent: Mapped[str | None] = mapped_column(String, default=None)
    ip: Mapped[str | None] = mapped_column(String, default=None)


class LoginThrottle(Base):
    """Failed-login counters per key ("email:..." or "ip:..."), in fixed windows. No RLS: login
    runs before any org scope exists."""

    __tablename__ = "login_throttle"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    failures: Mapped[int] = mapped_column(default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
