"""Identity use cases: signup, login, org and location management.

This module (with app.security.sessions) is allowed to use `unscoped_session`: signup and
login run before an org scope exists.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.db import set_scope, unscoped_session
from app.errors import ProblemError, not_found, validation_error
from app.identity.models import ApiKey, Location, Membership, Organization, User
from app.identity.schemas import (
    LocationCreate,
    LocationUpdate,
    OrgUpdate,
    SignupRequest,
)
from app.ids import new_id
from app.security import passwords, throttle
from app.security.models import SessionRecord
from app.security.sessions import create_session

_BAD_LOGIN = ProblemError(401, "unauthenticated", "Invalid email or password")


API_KEY_PREFIX = "chef_"


def hash_api_key(secret: str) -> str:
    """Keys carry 256 random bits, so an unsalted SHA-256 is a safe at-rest form and an indexed
    equality lookup on it leaks nothing useful through timing."""
    return hashlib.sha256(secret.encode()).hexdigest()


async def create_api_key(
    db: AsyncSession,
    org_id: uuid.UUID,
    actor: uuid.UUID,
    scopes: list[str],
    location_id: uuid.UUID | None,
    expires_at: datetime | None,
) -> tuple[ApiKey, str]:
    """Issue a key. The plaintext secret is returned once and never stored or logged."""
    if expires_at is not None and expires_at <= datetime.now(UTC):
        raise validation_error({"expires_at": "Must be in the future"})
    if location_id is not None:
        loc = await db.get(Location, location_id)
        if loc is None or loc.archived_at is not None:
            raise not_found("Location not found")
    secret = API_KEY_PREFIX + secrets.token_urlsafe(32)  # 256 bits
    row = ApiKey(
        org_id=org_id,
        key_hash=hash_api_key(secret),
        prefix=secret[:12],
        scopes=sorted(set(scopes)),
        location_id=location_id,
        created_by=actor,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=actor,
        entity_type="api_key",
        entity_id=row.id,
        action="api_key.create",
        location_id=location_id,
        details={
            "prefix": row.prefix,
            "scopes": row.scopes,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    return row, secret


async def revoke_api_key(
    db: AsyncSession, org_id: uuid.UUID, actor: uuid.UUID, key_id: uuid.UUID
) -> ApiKey:
    row = await db.scalar(select(ApiKey).where(ApiKey.org_id == org_id, ApiKey.id == key_id))
    if row is None:
        raise not_found()
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await audit.record(
            db,
            org_id=org_id,
            actor_user_id=actor,
            entity_type="api_key",
            entity_id=row.id,
            action="api_key.revoke",
            location_id=row.location_id,
        )
    return row


@dataclass
class Registration:
    user: User
    org: Organization
    location: Location


@dataclass
class LoggedIn:
    token: str
    record: SessionRecord
    user: User
    org: Organization
    role: str


def _email_taken() -> ProblemError:
    return ProblemError(
        409,
        "conflict",
        "Could not create the account",
        errors={"email": "Already registered"},
    )


async def _register(db: AsyncSession, data: SignupRequest, password_hash: str) -> Registration:
    """Create user, org, owner membership, first location, and audit events in one transaction."""
    if data.password.lower() == data.email.lower():
        raise validation_error({"password": "Must not be the same as your email"})
    exists = await db.scalar(select(User.id).where(User.email == data.email))
    if exists is not None:
        raise _email_taken()

    user = User(email=data.email, password_hash=password_hash, display_name=data.display_name)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        raise _email_taken() from None

    org_id = new_id()
    await set_scope(db, org_id=org_id)
    org = Organization(id=org_id, name=data.org_name, timezone=data.timezone)
    db.add(org)
    await db.flush()
    db.add(Membership(org_id=org_id, user_id=user.id, role="owner"))
    location = Location(org_id=org_id, name=data.location_name, timezone=data.timezone)
    db.add(location)
    await db.flush()
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="user.signup",
    )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user.id,
        entity_type="organization",
        entity_id=org_id,
        action="org.create",
    )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user.id,
        entity_type="location",
        entity_id=location.id,
        action="location.create",
        location_id=location.id,
    )
    return Registration(user, org, location)


async def register_account(data: SignupRequest) -> Registration:
    """Create an account without a session (used by the seed command)."""
    password_hash = await passwords.hash_password(data.password)
    async with unscoped_session() as db:
        return await _register(db, data, password_hash)


async def signup(
    data: SignupRequest, *, user_agent: str | None, ip: str | None
) -> tuple[Registration, str, SessionRecord]:
    password_hash = await passwords.hash_password(data.password)
    async with unscoped_session() as db:
        reg = await _register(db, data, password_hash)
        token, record = await create_session(
            db, user_id=reg.user.id, org_id=reg.org.id, user_agent=user_agent, ip=ip
        )
        return reg, token, record


async def login(email: str, password: str, *, user_agent: str | None, ip: str | None) -> LoggedIn:
    email = email.strip().lower()
    limits = throttle.keys(email, ip)
    now = datetime.now(UTC)
    async with unscoped_session() as db:
        await throttle.check(db, limits, now)
        user = await db.scalar(select(User).where(User.email == email))
    # Always run Argon2 (against a dummy hash for unknown emails) so timing doesn't leak accounts.
    ok = await passwords.verify_password(
        user.password_hash if user else passwords.DUMMY_HASH, password
    )
    if user is None or not ok or user.disabled_at is not None:
        # Own transaction: it must commit even though the request fails.
        async with unscoped_session() as db:
            await throttle.record_failure(db, limits, now)
        raise _BAD_LOGIN

    async with unscoped_session() as db:
        await throttle.clear_email(db, email)
        # Memberships are RLS-protected; app.user_id allows the user to read their own rows.
        await set_scope(db, user_id=user.id)
        membership = await db.scalar(
            select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at)
        )
        if membership is None:
            raise _BAD_LOGIN
        await set_scope(db, org_id=membership.org_id)
        org = await db.get_one(Organization, membership.org_id)
        token, record = await create_session(
            db, user_id=user.id, org_id=org.id, user_agent=user_agent, ip=ip
        )
        await audit.record(
            db,
            org_id=org.id,
            actor_user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            action="auth.login",
        )
        return LoggedIn(token, record, user, org, membership.role)


async def check_throttle(limits: dict[str, int], now: datetime) -> None:
    """Throttle check for unauthenticated flows other than login (invitation acceptance)."""
    async with unscoped_session() as db:
        await throttle.check(db, limits, now)


async def record_throttle_failure(limits: dict[str, int], now: datetime) -> None:
    async with unscoped_session() as db:  # own transaction: commits though the request fails
        await throttle.record_failure(db, limits, now)


async def logout_audit(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="user",
        entity_id=user_id,
        action="auth.logout",
    )


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    return await db.get_one(User, user_id)


async def get_org(db: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None:
        raise not_found("Organization not found")
    return org


async def update_org(
    db: AsyncSession, org_id: uuid.UUID, actor: uuid.UUID, data: OrgUpdate
) -> Organization:
    org = await get_org(db, org_id)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in changes.items():
        setattr(org, key, value)
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=actor,
        entity_type="organization",
        entity_id=org_id,
        action="org.update",
        details={"fields": sorted(changes)},
    )
    return org


async def list_locations(
    db: AsyncSession, org_id: uuid.UUID, *, include_archived: bool
) -> list[Location]:
    stmt = select(Location).where(Location.org_id == org_id).order_by(Location.created_at)
    if not include_archived:
        stmt = stmt.where(Location.archived_at.is_(None))
    return list((await db.scalars(stmt)).all())


async def _get_location(db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID) -> Location:
    # RLS already hides other orgs' rows; the explicit filter is the service-layer scope.
    location = await db.scalar(
        select(Location).where(Location.id == location_id, Location.org_id == org_id)
    )
    if location is None:
        raise not_found("Location not found")
    return location


async def create_location(
    db: AsyncSession, org_id: uuid.UUID, actor: uuid.UUID, data: LocationCreate
) -> Location:
    location = Location(
        org_id=org_id,
        name=data.name,
        address=data.address,
        timezone=data.timezone,
        business_day_start=data.business_day_start,
    )
    db.add(location)
    await db.flush()
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=actor,
        entity_type="location",
        entity_id=location.id,
        action="location.create",
        location_id=location.id,
    )
    return location


async def update_location(
    db: AsyncSession,
    org_id: uuid.UUID,
    actor: uuid.UUID,
    location_id: uuid.UUID,
    data: LocationUpdate,
) -> Location:
    location = await _get_location(db, org_id, location_id)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in changes.items():
        setattr(location, key, value)
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=actor,
        entity_type="location",
        entity_id=location.id,
        action="location.update",
        location_id=location.id,
        details={"fields": sorted(changes)},
    )
    return location


async def archive_location(
    db: AsyncSession, org_id: uuid.UUID, actor: uuid.UUID, location_id: uuid.UUID
) -> Location:
    location = await _get_location(db, org_id, location_id)
    if location.archived_at is None:
        location.archived_at = datetime.now(UTC)
        await audit.record(
            db,
            org_id=org_id,
            actor_user_id=actor,
            entity_type="location",
            entity_id=location.id,
            action="location.archive",
            location_id=location.id,
        )
    return location
