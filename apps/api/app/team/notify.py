"""In-app notifications. No external delivery: clients poll `GET /v1/notifications`.

Kinds: schedule_published, schedule_changed, time_off_decided, availability_decided (to
employees); time_off_requested, availability_submitted, employee_deactivated (to managers).
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import LocationAccess, Membership
from app.ids import new_id
from app.team.models import Notification
from app.team.schemas import NotificationOut

MAX_LIST = 50


async def notify(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_ids: Iterable[uuid.UUID],
    *,
    kind: str,
    title: str,
    body: str = "",
    link: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> int:
    """One row per distinct user, in the caller's transaction. Returns how many were written."""
    users = sorted(set(user_ids))
    if not users:
        return 0
    await db.execute(
        pg_insert(Notification).values(
            [
                {
                    "id": new_id(),
                    "org_id": org_id,
                    "user_id": u,
                    "kind": kind,
                    "title": title[:200],
                    "body": body[:1000],
                    "link": link,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                }
                for u in users
            ]
        )
    )
    return len(users)


async def managers_for(
    db: AsyncSession, org_id: uuid.UUID, location_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Owners and admins, plus managers with access to any of these locations."""
    admins = set(
        await db.scalars(
            select(Membership.user_id).where(
                Membership.org_id == org_id, Membership.role.in_(("owner", "admin"))
            )
        )
    )
    locs = list(set(location_ids))
    managers: set[uuid.UUID] = set()
    if locs:
        managers = set(
            await db.scalars(
                select(Membership.user_id)
                .join(
                    LocationAccess,
                    (LocationAccess.user_id == Membership.user_id)
                    & (LocationAccess.org_id == Membership.org_id),
                )
                .where(
                    Membership.org_id == org_id,
                    Membership.role == "manager",
                    LocationAccess.location_id.in_(locs),
                )
            )
        )
    return admins | managers


async def list_for(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, *, unread_only: bool
) -> tuple[list[NotificationOut], int]:
    stmt = select(Notification).where(
        Notification.org_id == org_id, Notification.user_id == user_id
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = await db.scalars(
        stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(MAX_LIST)
    )
    return [NotificationOut.model_validate(r) for r in rows], await unread_count(
        db, org_id, user_id
    )


async def unread_count(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> int:
    n = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.org_id == org_id,
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
    )
    return int(n or 0)


async def mark_read(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    ids: list[uuid.UUID] | None,
    all_: bool,
) -> int:
    stmt = update(Notification).where(
        Notification.org_id == org_id,
        Notification.user_id == user_id,
        Notification.read_at.is_(None),
    )
    if not all_:
        stmt = stmt.where(Notification.id.in_(ids or []))
    await db.execute(stmt.values(read_at=datetime.now(UTC)))
    return await unread_count(db, org_id, user_id)
