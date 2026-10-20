import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEvent
from app.context import request_id_var


async def record(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    entity_type: str,
    entity_id: uuid.UUID | None,
    action: str,
    location_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit event in the caller's transaction (commits or rolls back with it)."""
    request_id = request_id_var.get()
    session.add(
        AuditEvent(
            org_id=org_id,
            actor_user_id=actor_user_id,
            location_id=location_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            details=details or {},
            request_id=None if request_id == "-" else request_id,
        )
    )
    await session.flush()
