"""Login invitations: create, list, revoke, preview, and accept.

Tokens are 256-bit, stored as SHA-256, valid 7 days, single use. Accepting runs without a
session: the token resolves to its org through `lookup_invitation` (a SECURITY DEFINER
function), and everything after that happens in one transaction scoped to that org: the
user, membership, location access, employee link, audit event, and session.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.config import get_settings
from app.db import lookup_invitation, scoped_session
from app.errors import ProblemError, forbidden, validation_error
from app.identity import service as identity
from app.identity.models import Location, LocationAccess, Membership, Organization, User
from app.security import passwords, throttle
from app.security.models import SessionRecord
from app.security.sessions import create_session, hash_token
from app.team.models import Invitation, StaffMember
from app.team.schemas import (
    InvitationCreate,
    InvitationOut,
    InvitationPreview,
    InvitationStatus,
)
from app.team.service import Scope, check_locations, get_employee

LIFETIME = timedelta(days=7)


def _invalid() -> ProblemError:
    return ProblemError(
        410,
        "invitation_invalid",
        "This invitation link has expired, was revoked, or was already used.",
        title="Invitation no longer valid",
    )


def status_of(inv: Invitation, now: datetime) -> InvitationStatus:
    if inv.accepted_at is not None:
        return "accepted"
    if inv.revoked_at is not None:
        return "revoked"
    return "expired" if inv.expires_at <= now else "pending"


async def _out(db: AsyncSession, invs: list[Invitation]) -> list[InvitationOut]:
    ids = {i.employee_id for i in invs if i.employee_id}
    names = (
        {
            r.id: r.display_name
            for r in await db.execute(
                select(StaffMember.id, StaffMember.display_name).where(StaffMember.id.in_(ids))
            )
        }
        if ids
        else {}
    )
    now = datetime.now(UTC)
    return [
        InvitationOut(
            id=i.id,
            email=i.email,
            role="manager" if i.role == "manager" else "employee",
            employee_id=i.employee_id,
            employee_name=names.get(i.employee_id) if i.employee_id else None,
            location_ids=list(i.location_ids),
            status=status_of(i, now),
            created_at=i.created_at,
            expires_at=i.expires_at,
            accepted_at=i.accepted_at,
        )
        for i in invs
    ]


def accept_url(token: str) -> str:
    return f"{get_settings().web_origin.rstrip('/')}/invite/{token}"


async def create_invitation(
    db: AsyncSession, scope: Scope, data: InvitationCreate
) -> tuple[InvitationOut, str]:
    """Returns the invitation and the plaintext token (never stored)."""
    if data.role == "manager" and not scope.is_admin:
        raise forbidden("Managers can invite employees only")
    email = data.email
    if data.employee_id is not None:
        emp = await get_employee(db, scope, data.employee_id)
        if not emp.active or emp.merged_into_id is not None:
            raise validation_error({"employee_id": "This employee is inactive"})
        if emp.user_id is not None:
            raise ProblemError(409, "conflict", "This employee already has a login")
        email = email or emp.email
    if not email:
        raise validation_error({"email": "Add an email address first"})
    locations = await check_locations(db, scope, data.location_ids)

    now = datetime.now(UTC)
    # A new link replaces any pending one for the same person.
    match = (
        Invitation.employee_id == data.employee_id
        if data.employee_id is not None
        else Invitation.email == email
    )
    replaced = list(
        await db.scalars(
            select(Invitation.id).where(
                Invitation.org_id == scope.org_id,
                match,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
        )
    )
    if replaced:
        await db.execute(
            update(Invitation).where(Invitation.id.in_(replaced)).values(revoked_at=now)
        )
        for rid in replaced:
            await audit.record(
                db,
                org_id=scope.org_id,
                actor_user_id=scope.user_id,
                entity_type="invitation",
                entity_id=rid,
                action="invitation.revoked",
                details={"reason": "replaced"},
            )

    token = secrets.token_urlsafe(32)  # 256 bits
    inv = Invitation(
        org_id=scope.org_id,
        email=email,
        role=data.role,
        employee_id=data.employee_id,
        location_ids=sorted(locations),
        token_hash=hash_token(token),
        created_by=scope.user_id,
        created_at=now,
        expires_at=now + LIFETIME,
    )
    db.add(inv)
    await db.flush()
    await audit.record(
        db,
        org_id=scope.org_id,
        actor_user_id=scope.user_id,
        entity_type="invitation",
        entity_id=inv.id,
        action="invitation.created",
        details={
            "email": email,
            "role": data.role,
            "employee_id": str(data.employee_id) if data.employee_id else None,
            "location_ids": [str(i) for i in inv.location_ids],
        },
    )
    return (await _out(db, [inv]))[0], token


def _visible(scope: Scope, inv: Invitation) -> bool:
    return scope.is_admin or bool(set(inv.location_ids) & scope.location_ids)


async def list_invitations(
    db: AsyncSession, scope: Scope, status: InvitationStatus | None
) -> list[InvitationOut]:
    rows = await db.scalars(
        select(Invitation)
        .where(Invitation.org_id == scope.org_id)
        .order_by(Invitation.created_at.desc())
        .limit(500)
    )
    out = await _out(db, [i for i in rows if _visible(scope, i)])
    return [i for i in out if status is None or i.status == status]


async def revoke_invitation(
    db: AsyncSession, scope: Scope, invitation_id: uuid.UUID
) -> InvitationOut:
    inv = await db.scalar(
        select(Invitation).where(Invitation.id == invitation_id, Invitation.org_id == scope.org_id)
    )
    if inv is None or not _visible(scope, inv):
        raise ProblemError(404, "not_found", "Invitation not found")
    if inv.role == "manager" and not scope.is_admin:
        raise forbidden()
    if inv.accepted_at is not None:
        raise ProblemError(409, "conflict", "This invitation was already accepted")
    if inv.revoked_at is None:
        inv.revoked_at = datetime.now(UTC)
        await audit.record(
            db,
            org_id=scope.org_id,
            actor_user_id=scope.user_id,
            entity_type="invitation",
            entity_id=inv.id,
            action="invitation.revoked",
        )
    await db.flush()
    return (await _out(db, [inv]))[0]


# ---------------------------------------------------------------- unauthenticated


async def _resolve(token: str) -> tuple[uuid.UUID, uuid.UUID]:
    found = await lookup_invitation(hash_token(token))
    if found is None:
        raise _invalid()
    return found


async def preview(token: str) -> InvitationPreview:
    invitation_id, org_id = await _resolve(token)
    async with scoped_session(org_id) as db:
        inv = await db.get_one(Invitation, invitation_id)
        org = await db.get_one(Organization, org_id)
        name = (
            await db.scalar(
                select(StaffMember.display_name).where(StaffMember.id == inv.employee_id)
            )
            if inv.employee_id
            else None
        )
        return InvitationPreview(
            org_name=org.name,
            email=inv.email,
            role="manager" if inv.role == "manager" else "employee",
            employee_name=name,
            expires_at=inv.expires_at,
        )


@dataclass
class Accepted:
    token: str
    record: SessionRecord
    user: User
    org: Organization
    role: str


def _email_has_account() -> ProblemError:
    return ProblemError(
        409,
        "email_has_account",
        "This email already has a ChefOS account. Ask your manager to invite another email.",
        title="Email already has an account",
    )


async def accept(
    token: str, display_name: str, password: str, *, user_agent: str | None, ip: str | None
) -> Accepted:
    """Create the login and link it, atomically. Throttled per client IP like login."""
    now = datetime.now(UTC)
    limits = {f"ip:{ip}": throttle.IP_LIMIT} if ip else {}
    if limits:
        await identity.check_throttle(limits, now)
    try:
        invitation_id, org_id = await _resolve(token)
    except ProblemError:
        if limits:
            await identity.record_throttle_failure(limits, now)
        raise
    async with scoped_session(org_id) as db:
        email = await db.scalar(select(Invitation.email).where(Invitation.id == invitation_id))
    message = passwords.validate_password(password, email=email)
    if message:
        raise validation_error({"password": message})
    password_hash = await passwords.hash_password(password)

    async with scoped_session(org_id) as db:
        inv = await db.scalar(
            select(Invitation).where(Invitation.id == invitation_id).with_for_update()
        )
        if inv is None or status_of(inv, datetime.now(UTC)) != "pending":
            raise _invalid()  # lost a race with another accept or a revoke
        if await db.scalar(select(User.id).where(User.email == inv.email)) is not None:
            raise _email_has_account()
        user = User(email=inv.email, password_hash=password_hash, display_name=display_name)
        db.add(user)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            raise _email_has_account() from None
        db.add(Membership(org_id=org_id, user_id=user.id, role=inv.role))
        live = set(
            await db.scalars(
                select(Location.id).where(
                    Location.id.in_(inv.location_ids), Location.archived_at.is_(None)
                )
            )
        )
        for loc in sorted(live):
            db.add(LocationAccess(org_id=org_id, user_id=user.id, location_id=loc))
        if inv.employee_id is not None:
            emp = await db.scalar(
                select(StaffMember).where(StaffMember.id == inv.employee_id).with_for_update()
            )
            # Follow a merge to the surviving record.
            while emp is not None and emp.merged_into_id is not None:
                emp = await db.get(StaffMember, emp.merged_into_id)
            if emp is None or emp.user_id is not None:
                raise ProblemError(409, "conflict", "This employee already has a login")
            emp.user_id = user.id
            emp.updated_at = datetime.now(UTC)
        inv.accepted_at = datetime.now(UTC)
        inv.accepted_user_id = user.id
        await db.flush()
        await audit.record(
            db,
            org_id=org_id,
            actor_user_id=user.id,
            entity_type="invitation",
            entity_id=inv.id,
            action="invitation.accepted",
            details={
                "role": inv.role,
                "employee_id": str(inv.employee_id) if inv.employee_id else None,
            },
        )
        org = await db.get_one(Organization, org_id)
        session_token, record = await create_session(
            db, user_id=user.id, org_id=org_id, user_agent=user_agent, ip=ip
        )
        return Accepted(session_token, record, user, org, inv.role)
