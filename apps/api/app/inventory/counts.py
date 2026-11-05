"""Inventory count sessions, lines, exception confirmation, and submission."""
# ruff: noqa: E501

import uuid
from collections.abc import Collection
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.errors import not_found, validation_error
from app.inventory import service
from app.inventory.schemas import (
    CountEntry,
    CountStart,
)


async def list_sessions(
    db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> dict[str, Any]:
    rows = await db.execute(
        text(
            "SELECT s.*,a.name AS storage_area_name FROM count_sessions s JOIN storage_areas a ON a.id=s.storage_area_id AND a.org_id=s.org_id WHERE s.org_id=:org AND s.location_id=:loc ORDER BY s.started_at DESC"
        ),
        {"org": org_id, "loc": location_id},
    )
    return {"data": [service.as_dict(r) for r in rows]}


async def start_count(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, body: CountStart
) -> dict[str, Any]:
    area = await db.execute(
        text(
            "SELECT id FROM storage_areas WHERE id=:id AND org_id=:org AND location_id=:loc AND active"
        ),
        {"id": body.storage_area_id, "org": org_id, "loc": body.location_id},
    )
    if area.first() is None:
        raise not_found("Storage area not found")
    session_id = uuid.uuid7()
    result = await db.execute(
        text(
            "INSERT INTO count_sessions(id,org_id,location_id,storage_area_id,started_by) VALUES(:id,:org,:loc,:area,:user) RETURNING *"
        ),
        {
            "id": session_id,
            "org": org_id,
            "loc": body.location_id,
            "area": body.storage_area_id,
            "user": user_id,
        },
    )
    await db.execute(
        text(
            "INSERT INTO inventory_levels(org_id,location_id,item_id,on_hand_base_quantity,version) "
            "SELECT :org,:loc,a.item_id,0,0 FROM item_storage_assignments a "
            "JOIN inventory_items i ON i.org_id=a.org_id AND i.id=a.item_id AND i.active "
            "WHERE a.org_id=:org AND a.location_id=:loc AND a.storage_area_id=:area "
            "ON CONFLICT(org_id,location_id,item_id) DO NOTHING"
        ),
        {"org": org_id, "loc": body.location_id, "area": body.storage_area_id},
    )
    await db.execute(
        text("""INSERT INTO count_lines(id,org_id,location_id,session_id,item_id,expected_base_quantity,expected_level_version,item_name,base_unit)
      SELECT gen_random_uuid(),:org,:loc,:session,i.id,COALESCE(l.on_hand_base_quantity,0),COALESCE(l.version,0),i.name,i.base_unit
      FROM item_storage_assignments a JOIN inventory_items i ON i.id=a.item_id AND i.org_id=a.org_id
      LEFT JOIN inventory_levels l ON l.org_id=i.org_id AND l.item_id=i.id AND l.location_id=:loc
      WHERE a.org_id=:org AND a.location_id=:loc AND a.storage_area_id=:area AND i.active"""),
        {
            "org": org_id,
            "loc": body.location_id,
            "session": session_id,
            "area": body.storage_area_id,
        },
    )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="count_session",
        entity_id=session_id,
        location_id=body.location_id,
        action="started",
    )
    return service.as_dict(result.one())


async def session_detail(
    db: AsyncSession,
    org_id: uuid.UUID,
    allowed_location_ids: Collection[uuid.UUID],
    session_id: uuid.UUID,
) -> dict[str, Any]:
    session = await db.execute(
        text("SELECT * FROM count_sessions WHERE id=:id AND org_id=:org"),
        {"id": session_id, "org": org_id},
    )
    if not (s := session.first()):
        raise not_found("Count session not found")
    await service.authorized_location(db, allowed_location_ids, s.location_id)
    lines = await db.execute(
        text("SELECT * FROM count_lines WHERE org_id=:org AND session_id=:id ORDER BY item_name"),
        {"org": org_id, "id": session_id},
    )
    return {**service.as_dict(s), "lines": [service.as_dict(r) for r in lines]}


async def save_line(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    allowed_location_ids: Collection[uuid.UUID],
    session_id: uuid.UUID,
    item_id: uuid.UUID,
    body: CountEntry,
) -> dict[str, Any]:
    row = await db.execute(
        text(
            "SELECT c.*,s.status FROM count_lines c JOIN count_sessions s ON s.id=c.session_id AND s.org_id=c.org_id WHERE c.org_id=:org AND c.session_id=:session AND c.item_id=:item FOR UPDATE OF s"
        ),
        {"org": org_id, "session": session_id, "item": item_id},
    )
    if not (line := row.first()):
        raise not_found("Count line not found")
    if line.status != "draft":
        raise service.conflict("Submitted count sessions are immutable")
    if role not in {"employee", *service.MANAGERS}:
        raise service.conflict("Role cannot enter counts")
    await service.authorized_location(db, allowed_location_ids, line.location_id)
    conversion = await db.scalar(
        text(
            "SELECT base_units_per_unit FROM item_unit_conversions WHERE org_id=:org AND item_id=:item AND unit=:unit"
        ),
        {"org": org_id, "item": item_id, "unit": body.unit},
    )
    if body.unit != line.base_unit and conversion is None:
        raise validation_error(
            {"unit": f"Use configured units for this item; base unit is {line.base_unit}"}
        )
    factor = conversion if body.unit != line.base_unit else Decimal(1)
    quantity = body.quantity * factor
    expected = line.expected_base_quantity
    variance = abs(quantity - expected)
    exception = (
        "review_required" if variance > max(expected * Decimal("0.2"), Decimal(1)) else "clear"
    )
    result = await db.execute(
        text(
            "UPDATE count_lines SET counted_base_quantity=:qty,entered_quantity=:entered,entered_unit=:unit,exception_state=:exception,note=:note WHERE org_id=:org AND session_id=:session AND item_id=:item RETURNING *"
        ),
        {
            "qty": quantity,
            "entered": body.quantity,
            "unit": body.unit,
            "exception": exception,
            "note": body.note,
            "org": org_id,
            "session": session_id,
            "item": item_id,
        },
    )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="count_line",
        entity_id=line.id,
        location_id=line.location_id,
        action="saved",
    )
    return service.as_dict(result.one())


async def confirm_exception(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    allowed_location_ids: Collection[uuid.UUID],
    session_id: uuid.UUID,
    item_id: uuid.UUID,
    body: dict[str, str],
) -> dict[str, Any]:
    if not body.get("note", "").strip():
        raise validation_error({"note": "A review note is required"})
    session = await db.execute(
        text(
            "SELECT status,location_id FROM count_sessions WHERE id=:id AND org_id=:org FOR UPDATE"
        ),
        {"id": session_id, "org": org_id},
    )
    if not (count := session.first()):
        raise not_found("Count session not found")
    await service.authorized_location(db, allowed_location_ids, count.location_id)
    if count.status != "draft":
        raise service.conflict("Submitted count sessions are immutable")
    result = await db.execute(
        text(
            "UPDATE count_lines SET exception_state='confirmed',note=:note WHERE org_id=:org AND session_id=:session AND item_id=:item AND counted_base_quantity IS NOT NULL AND exception_state='review_required' RETURNING *"
        ),
        {"note": body["note"], "org": org_id, "session": session_id, "item": item_id},
    )
    if not (line := result.first()):
        raise not_found("Review-required count line not found")
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="count_line",
        entity_id=line.id,
        location_id=line.location_id,
        action="variance_confirmed",
        details={"note": body["note"]},
    )
    return service.as_dict(line)


async def submit_count(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    allowed_location_ids: Collection[uuid.UUID],
    session_id: uuid.UUID,
) -> dict[str, Any]:
    session = await db.execute(
        text("SELECT * FROM count_sessions WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": session_id, "org": org_id},
    )
    if not (s := session.first()):
        raise not_found("Count session not found")
    await service.authorized_location(db, allowed_location_ids, s.location_id)
    if s.status != "draft":
        raise service.conflict("Count session has already been submitted")
    lines = await db.execute(
        text("SELECT * FROM count_lines WHERE org_id=:org AND session_id=:id FOR UPDATE"),
        {"org": org_id, "id": session_id},
    )
    all_lines = list(lines)
    if not all_lines or any(x.counted_base_quantity is None for x in all_lines):
        raise validation_error({"lines": "Every count line must be saved before submission"})
    if any(x.exception_state == "review_required" for x in all_lines):
        raise validation_error({"exceptions": "Confirm or correct every count exception"})
    item_ids = [x.item_id for x in all_lines]
    levels = await db.execute(
        text(
            "SELECT item_id,version FROM inventory_levels WHERE org_id=:org AND location_id=:loc AND item_id=ANY(:items) FOR UPDATE"
        ),
        {"org": org_id, "loc": s.location_id, "items": item_ids},
    )
    versions = {x.item_id: x.version for x in levels}
    if any(versions.get(x.item_id, 0) != x.expected_level_version for x in all_lines):
        raise service.conflict(
            "Stock changed after this count started. Start a fresh count and recount."
        )
    business_day = service.business_date_for(
        await service.authorized_location(db, allowed_location_ids, s.location_id)
    )
    for x in all_lines:
        delta = x.counted_base_quantity - x.expected_base_quantity
        await db.execute(
            text(
                "INSERT INTO inventory_levels(org_id,location_id,item_id,on_hand_base_quantity,version,updated_at) VALUES(:org,:loc,:item,:qty,1,now()) ON CONFLICT(org_id,location_id,item_id) DO UPDATE SET on_hand_base_quantity=excluded.on_hand_base_quantity,version=inventory_levels.version+1,updated_at=now()"
            ),
            {
                "org": org_id,
                "loc": s.location_id,
                "item": x.item_id,
                "qty": x.counted_base_quantity,
            },
        )
        await db.execute(
            text(
                "INSERT INTO stock_movements(id,org_id,location_id,item_id,movement_type,source_id,quantity_delta,business_date,item_name,base_unit) VALUES(:id,:org,:loc,:item,'count',:source,:delta,:date,:name,:unit)"
            ),
            {
                "id": uuid.uuid7(),
                "org": org_id,
                "loc": s.location_id,
                "item": x.item_id,
                "source": session_id,
                "delta": delta,
                "date": business_day,
                "name": x.item_name,
                "unit": x.base_unit,
            },
        )
    result = await db.execute(
        text(
            "UPDATE count_sessions SET status='submitted',submitted_by=:user,submitted_at=now() WHERE id=:id AND org_id=:org RETURNING *"
        ),
        {"user": user_id, "id": session_id, "org": org_id},
    )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="count_session",
        entity_id=session_id,
        location_id=s.location_id,
        action="submitted",
    )
    return service.as_dict(result.one())
