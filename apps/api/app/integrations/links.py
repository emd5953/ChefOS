"""Location links: Square location <-> platform location, loaded for the pipeline."""

import uuid
from dataclasses import dataclass
from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import Location
from app.integrations.models import IntegrationLocationLink


@dataclass(frozen=True)
class Link:
    external_id: str
    external_name: str
    location_id: uuid.UUID | None
    timezone: str
    business_day_start: time


async def load_links(db: AsyncSession, connection_id: uuid.UUID) -> dict[str, Link]:
    rows = await db.scalars(
        select(IntegrationLocationLink).where(
            IntegrationLocationLink.connection_id == connection_id,
            IntegrationLocationLink.active.is_(True),
        )
    )
    return {
        r.external_location_id: Link(
            r.external_location_id,
            r.external_name,
            r.location_id,
            r.timezone,
            r.business_day_start,
        )
        for r in rows
    }


async def apply_link(
    db: AsyncSession, link: IntegrationLocationLink, location: Location | None
) -> None:
    """Point a link at a platform location (or unlink). Timezone and day start follow it."""
    link.location_id = location.id if location else None
    if location is not None:
        link.timezone = location.timezone
        link.business_day_start = location.business_day_start
    await db.flush()


async def auto_link(db: AsyncSession, org_id: uuid.UUID, connection_id: uuid.UUID) -> list[str]:
    """Link by exact name match, and a lone Square location to a lone platform location."""
    links = list(
        await db.scalars(
            select(IntegrationLocationLink).where(
                IntegrationLocationLink.connection_id == connection_id,
                IntegrationLocationLink.active.is_(True),
            )
        )
    )
    locations = list(
        await db.scalars(
            select(Location).where(Location.org_id == org_id, Location.archived_at.is_(None))
        )
    )
    taken = {link.location_id for link in links if link.location_id is not None}
    free = [loc for loc in locations if loc.id not in taken]
    changed: list[str] = []
    unlinked = [link for link in links if link.location_id is None]
    if len(links) == 1 and len(locations) == 1 and unlinked and free:
        pairs = [(unlinked[0], free[0])]
    else:
        by_name = {loc.name.strip().lower(): loc for loc in free}
        pairs = [
            (link, by_name[link.external_name.strip().lower()])
            for link in unlinked
            if link.external_name.strip().lower() in by_name
        ]
    for link, loc in pairs:
        await apply_link(db, link, loc)
        changed.append(link.external_location_id)
    return changed
