"""Inventory waste entries and the waste log."""
# ruff: noqa: E501

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.errors import not_found, validation_error
from app.identity.models import Location
from app.inventory import service
from app.inventory.schemas import (
    MAX_QUANTITY,
    WasteCreate,
)


async def create_waste(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, location: Location, body: WasteCreate
) -> dict[str, Any]:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{org_id}:waste:{body.idempotency_key}"},
    )
    prior = await db.execute(
        text("""SELECT w.*,i.name item_name,i.base_unit FROM waste_entries w
        JOIN inventory_items i ON i.org_id=w.org_id AND i.id=w.item_id
        WHERE w.org_id=:org AND w.idempotency_key=:key"""),
        {"org": org_id, "key": body.idempotency_key},
    )
    if row := prior.first():
        return service.as_dict(row)
    item_result = await db.execute(
        text("""SELECT i.id,i.name,i.base_unit,i.active,c.base_units_per_unit
        FROM inventory_items i LEFT JOIN item_unit_conversions c ON c.org_id=i.org_id AND c.item_id=i.id
          AND c.unit=:unit WHERE i.org_id=:org AND i.id=:item"""),
        {"org": org_id, "item": body.item_id, "unit": body.unit},
    )
    if not (item := item_result.first()) or not item.active:
        raise not_found("Item not found")
    if body.unit and body.unit != item.base_unit and item.base_units_per_unit is None:
        raise validation_error({"unit": "No conversion is configured for this item"})
    quantity = body.quantity * (item.base_units_per_unit or Decimal(1))
    if int(quantity.normalize().as_tuple().exponent) < -6:
        raise validation_error({"quantity": "Converted quantity supports at most 6 decimal places"})
    quantity = quantity.quantize(Decimal("0.000001"))
    if quantity > MAX_QUANTITY:
        raise validation_error({"quantity": "Converted quantity exceeds the supported maximum"})
    level = await db.execute(
        text(
            "SELECT on_hand_base_quantity FROM inventory_levels WHERE org_id=:org AND location_id=:loc AND item_id=:item FOR UPDATE"
        ),
        {"org": org_id, "loc": body.location_id, "item": body.item_id},
    )
    on_hand = level.scalar_one_or_none()
    if on_hand is None or on_hand < quantity:
        raise validation_error({"quantity": "Waste quantity exceeds available stock"})
    cost_row = await db.execute(
        text("""SELECT rl.total_cost_minor/NULLIF(rl.received_base_quantity,0)
        FROM receipt_lines rl JOIN receipts r ON r.org_id=rl.org_id AND r.id=rl.receipt_id
        WHERE rl.org_id=:org AND rl.item_id=:item ORDER BY r.received_at DESC LIMIT 1"""),
        {"org": org_id, "item": body.item_id},
    )
    unit_cost = cost_row.scalar_one_or_none()
    waste_id = uuid.uuid7()
    business_day = service.business_date_for(location)
    await db.execute(
        text("""INSERT INTO waste_entries(id,org_id,location_id,item_id,quantity_base,unit_cost_minor,reason,
        business_date,idempotency_key,created_by) VALUES(:id,:org,:loc,:item,:qty,:cost,:reason,:date,:key,:user)"""),
        {
            "id": waste_id,
            "org": org_id,
            "loc": body.location_id,
            "item": body.item_id,
            "qty": quantity,
            "cost": unit_cost,
            "reason": body.reason,
            "date": business_day,
            "key": body.idempotency_key,
            "user": user_id,
        },
    )
    await db.execute(
        text("""UPDATE inventory_levels SET on_hand_base_quantity=on_hand_base_quantity-:qty,
        version=version+1,updated_at=now() WHERE org_id=:org AND location_id=:loc AND item_id=:item"""),
        {"qty": quantity, "org": org_id, "loc": body.location_id, "item": body.item_id},
    )
    await db.execute(
        text("""INSERT INTO stock_movements(id,org_id,location_id,item_id,movement_type,source_id,
        quantity_delta,business_date,item_name,base_unit) VALUES(:id,:org,:loc,:item,'waste',:source,:delta,:date,:name,:unit)"""),
        {
            "id": uuid.uuid7(),
            "org": org_id,
            "loc": body.location_id,
            "item": body.item_id,
            "source": waste_id,
            "delta": -quantity,
            "date": business_day,
            "name": item.name,
            "unit": item.base_unit,
        },
    )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="waste_entry",
        entity_id=waste_id,
        location_id=body.location_id,
        action="created",
        details={
            "item_id": str(body.item_id),
            "quantity_base": str(quantity),
            "reason": body.reason,
        },
    )
    saved = await db.execute(
        text("""SELECT w.*,i.name item_name,i.base_unit FROM waste_entries w
        JOIN inventory_items i ON i.org_id=w.org_id AND i.id=w.item_id WHERE w.id=:id AND w.org_id=:org"""),
        {"id": waste_id, "org": org_id},
    )
    return service.as_dict(saved.one())


async def list_log(
    db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID, from_date: date, to_date: date
) -> dict[str, list[dict[str, Any]]]:
    if to_date < from_date or (to_date - from_date).days > 366:
        raise validation_error({"to": "Date range must be ordered and no longer than 367 days"})
    rows = await db.execute(
        text("""SELECT w.*,i.name item_name,i.base_unit FROM waste_entries w
        JOIN inventory_items i ON i.org_id=w.org_id AND i.id=w.item_id WHERE w.org_id=:org
        AND w.location_id=:loc AND w.business_date BETWEEN :start AND :end
        ORDER BY w.business_date DESC,w.created_at DESC"""),
        {"org": org_id, "loc": location_id, "start": from_date, "end": to_date},
    )
    return {"data": [service.as_dict(row) for row in rows]}
