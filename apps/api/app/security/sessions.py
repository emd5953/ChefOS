"""Sessions, CSRF, and the auth dependencies (`require_auth`, `org_session`, `require_role`,
`require_staff_role`).

Only this module and app.identity.service may import `unscoped_session`.
"""

import hashlib
import hmac
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import scoped_session, set_scope, unscoped_session
from app.errors import ProblemError, forbidden
from app.identity.models import Location, LocationAccess, Membership, User
from app.security.models import SessionRecord

COOKIE_NAME = "chefos_session"
CSRF_HEADER = "X-CSRF-Token"
ABSOLUTE_LIFETIME = timedelta(days=14)
IDLE_LIFETIME = timedelta(hours=24)
_TOUCH_INTERVAL = timedelta(seconds=60)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, SessionRecord]:
    """Issue a fresh session (new token every time, so fixation can't carry over)."""
    token = secrets.token_urlsafe(32)  # 256 bits
    now = datetime.now(UTC)
    record = SessionRecord(
        token_hash=hash_token(token),
        user_id=user_id,
        org_id=org_id,
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        last_seen_at=now,
        expires_at=now + ABSOLUTE_LIFETIME,
        user_agent=(user_agent or "")[:256] or None,
        ip=ip,
    )
    session.add(record)
    await session.flush()
    return token, record


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(ABSOLUTE_LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=get_settings().session_cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=get_settings().session_cookie_secure,
    )


async def revoke_session(session_id: uuid.UUID) -> None:
    async with unscoped_session() as db:
        await db.execute(
            update(SessionRecord)
            .where(SessionRecord.id == session_id, SessionRecord.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )


@dataclass(frozen=True)
class AuthContext:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    session_id: uuid.UUID
    csrf_token: str

    async def allowed_location_ids(self, *, include_archived: bool = True) -> list[uuid.UUID]:
        """Locations this user may see: all org locations for owner/admin, else location_access."""
        stmt = select(Location.id).where(Location.org_id == self.org_id)
        if not include_archived:
            stmt = stmt.where(Location.archived_at.is_(None))
        if self.role not in ("owner", "admin"):
            stmt = stmt.join(
                LocationAccess,
                (LocationAccess.location_id == Location.id)
                & (LocationAccess.user_id == self.user_id),
            )
        async with scoped_session(self.org_id) as db:
            return list((await db.scalars(stmt.order_by(Location.created_at))).all())


def _expired() -> ProblemError:
    return ProblemError(401, "session_expired", "Your session has expired. Please log in again.")


async def require_auth(request: Request) -> AuthContext:
    """Authenticate the session cookie and enforce CSRF on unsafe methods."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise ProblemError(401, "unauthenticated", "Authentication required")
    now = datetime.now(UTC)
    async with unscoped_session() as db:
        rec = await db.scalar(
            select(SessionRecord).where(SessionRecord.token_hash == hash_token(token))
        )
        if (
            rec is None
            or rec.revoked_at is not None
            or rec.expires_at <= now
            or rec.last_seen_at <= now - IDLE_LIFETIME
        ):
            raise _expired()
        # Membership is RLS-protected, so look it up with the session's org in scope.
        await set_scope(db, org_id=rec.org_id)
        role = await db.scalar(
            select(Membership.role)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.user_id == rec.user_id,
                Membership.org_id == rec.org_id,
                User.disabled_at.is_(None),
            )
        )
        if role is None:
            raise _expired()
        if now - rec.last_seen_at > _TOUCH_INTERVAL:
            await db.execute(
                update(SessionRecord).where(SessionRecord.id == rec.id).values(last_seen_at=now)
            )
        ctx = AuthContext(
            user_id=rec.user_id,
            org_id=rec.org_id,
            role=role,
            session_id=rec.id,
            csrf_token=rec.csrf_token,
        )
    if request.method not in SAFE_METHODS:
        supplied = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(supplied.encode(), ctx.csrf_token.encode()):
            raise ProblemError(403, "csrf_failed", "Missing or invalid CSRF token")
    return ctx


AuthDep = Annotated[AuthContext, Depends(require_auth)]


def require_role(ctx: AuthContext, allowed: set[str] | frozenset[str]) -> None:
    if ctx.role not in allowed:
        raise forbidden()


STAFF_ROLES = frozenset({"owner", "admin", "manager"})


async def require_staff_role(ctx: AuthDep) -> AuthContext:
    """Owner, admin, or manager. `employee` sessions get 403 on manager and back-office APIs."""
    require_role(ctx, STAFF_ROLES)
    return ctx


StaffDep = Annotated[AuthContext, Depends(require_staff_role)]


async def org_session(ctx: AuthDep) -> AsyncIterator[AsyncSession]:
    """Request-scoped transaction with `app.org_id` set. Commit on success, rollback on error."""
    async with scoped_session(ctx.org_id) as db:
        yield db


# function scope: the transaction commits before the response is sent.
OrgSession = Annotated[AsyncSession, Depends(org_session, scope="function")]
