"""Identity mapping: external_refs and mapping_issues."""

import uuid
from collections import Counter
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ids import new_id
from app.integrations.models import ExternalRef, MappingIssue
from app.integrations.providers.square.types import MappingIssueSignal


async def resolve_refs(
    db: AsyncSession,
    connection_id: uuid.UUID,
    entity_type: str,
    external_ids: Iterable[str],
) -> dict[str, uuid.UUID]:
    """external id -> platform entity id, for those that are mapped."""
    ids = sorted(set(external_ids))
    if not ids:
        return {}
    rows = await db.execute(
        select(ExternalRef.external_id, ExternalRef.entity_id).where(
            ExternalRef.connection_id == connection_id,
            ExternalRef.entity_type == entity_type,
            ExternalRef.external_id.in_(ids),
        )
    )
    return {r.external_id: r.entity_id for r in rows}


async def add_refs(
    db: AsyncSession,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    entity_type: str,
    mapping: dict[str, uuid.UUID],
) -> None:
    if not mapping:
        return
    stmt = pg_insert(ExternalRef).values(
        [
            {
                "org_id": org_id,
                "connection_id": connection_id,
                "entity_type": entity_type,
                "external_id": ext,
                "entity_id": eid,
            }
            for ext, eid in mapping.items()
        ]
    )
    await db.execute(stmt.on_conflict_do_nothing())


class IssueCollector:
    """Aggregates mapping signals during a batch; `flush` upserts one row per distinct issue."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, str]] = Counter()
        self._labels: dict[tuple[str, str], str] = {}

    def add(self, signal: MappingIssueSignal) -> None:
        key = (signal.kind, signal.external_id)
        self._counts[key] += 1
        self._labels.setdefault(key, signal.label)

    def add_many(self, signals: Iterable[MappingIssueSignal]) -> None:
        for s in signals:
            self.add(s)

    async def flush(
        self, db: AsyncSession, org_id: uuid.UUID, connection_id: uuid.UUID, now: datetime
    ) -> None:
        if not self._counts:
            return
        rows = [
            {
                "id": new_id(),
                "org_id": org_id,
                "connection_id": connection_id,
                "kind": kind,
                "external_id": ext,
                "label": self._labels[(kind, ext)][:300],
                "first_seen_at": now,
                "last_seen_at": now,
                "occurrences": n,
            }
            for (kind, ext), n in sorted(self._counts.items())
        ]
        stmt = pg_insert(MappingIssue).values(rows)
        await db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_mapping_issues_key",
                set_={
                    "last_seen_at": stmt.excluded.last_seen_at,
                    "occurrences": MappingIssue.occurrences + stmt.excluded.occurrences,
                },
            )
        )
        self._counts.clear()


async def open_issue_count(db: AsyncSession, connection_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            text(
                "SELECT count(*) FROM mapping_issues "
                "WHERE connection_id = :c AND resolved_at IS NULL"
            ),
            {"c": connection_id},
        )
        or 0
    )
