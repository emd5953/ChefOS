"""Inventory shared helpers and catalog use cases: items, suppliers, storage areas, pars, packs."""
# ruff: noqa: E501

import uuid
from collections.abc import Collection
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.business_date import business_date as calculate_business_date
from app.errors import not_found, validation_error
from app.identity.models import Location
from app.inventory.schemas import (
    ConversionUpdate,
    ItemCreate,
    ItemUpdate,
    PackCreate,
    PackUpdate,
    ParUpdate,
    StorageAreaCreate,
    StorageAreaUpdate,
    SupplierCreate,
    SupplierUpdate,
)

MANAGERS = frozenset({"owner", "admin", "manager"})


def business_date_for(location: Location) -> date:
    return calculate_business_date(
        datetime.now(UTC), location.timezone, location.business_day_start
    )


async def authorized_location(
    db: AsyncSession, allowed_location_ids: Collection[uuid.UUID], location_id: uuid.UUID
) -> Location:
    if location_id not in allowed_location_ids:
        raise not_found("Location not found")
    row = await db.get(Location, location_id)
    if row is None:
        raise not_found("Location not found")
    return row


def as_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


def conflict(detail: str) -> Exception:
    from app.errors import ProblemError

    return ProblemError(409, "conflict", detail)


async def list_items(
    db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> dict[str, list[dict[str, Any]]]:
    rows = await db.execute(
        text("""SELECT i.id,i.name,i.category,i.base_unit,i.dimension,i.active,i.default_supplier_id,i.currency,
        COALESCE(l.on_hand_base_quantity,0) on_hand_base_quantity,p.par_base_quantity,
        cost.last_received_cost_minor,cost.cost_as_of,
        (p.par_base_quantity IS NOT NULL AND COALESCE(l.on_hand_base_quantity,0)<p.par_base_quantity) below_par
        FROM inventory_items i LEFT JOIN inventory_levels l ON l.org_id=i.org_id AND l.item_id=i.id AND l.location_id=:loc
        LEFT JOIN inventory_pars p ON p.org_id=i.org_id AND p.item_id=i.id AND p.location_id=:loc
        LEFT JOIN LATERAL (SELECT rl.total_cost_minor/NULLIF(rl.received_base_quantity,0) last_received_cost_minor,r.received_at cost_as_of
          FROM receipt_lines rl JOIN receipts r ON r.org_id=rl.org_id AND r.id=rl.receipt_id
          WHERE rl.org_id=i.org_id AND rl.item_id=i.id ORDER BY r.received_at DESC LIMIT 1) cost ON true
        WHERE i.org_id=:org ORDER BY i.name"""),
        {"org": org_id, "loc": location_id},
    )
    return {"data": [as_dict(r) for r in rows]}


async def create_item(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, body: ItemCreate
) -> dict[str, Any]:
    if body.default_supplier_id is not None:
        supplier = await db.execute(
            text("SELECT id FROM suppliers WHERE id=:id AND org_id=:org AND active"),
            {"id": body.default_supplier_id, "org": org_id},
        )
        if supplier.first() is None:
            raise not_found("Supplier not found")
    cur = await db.execute(
        text(
            "INSERT INTO inventory_items(id,org_id,name,category,base_unit,dimension,default_supplier_id,currency) VALUES(:id,:org,:name,:category,:unit,:dimension,:supplier,:currency) RETURNING *"
        ),
        {
            "id": uuid.uuid7(),
            "org": org_id,
            "name": body.name,
            "category": body.category,
            "unit": body.base_unit,
            "dimension": body.dimension,
            "supplier": body.default_supplier_id,
            "currency": body.currency,
        },
    )
    item = as_dict(cur.one())
    for unit, factor in body.conversions.items():
        await db.execute(
            text(
                "INSERT INTO item_unit_conversions(id,org_id,item_id,unit,base_units_per_unit) VALUES(:id,:org,:item,:unit,:factor)"
            ),
            {
                "id": uuid.uuid7(),
                "org": org_id,
                "item": item["id"],
                "unit": unit,
                "factor": factor,
            },
        )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="inventory_item",
        entity_id=item["id"],
        action="created",
    )
    return item


async def update_item(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, item_id: uuid.UUID, body: ItemUpdate
) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    if not values:
        result = await db.execute(
            text("SELECT * FROM inventory_items WHERE id=:id AND org_id=:org"),
            {"id": item_id, "org": org_id},
        )
        if not (item := result.first()):
            raise not_found("Item not found")
        return as_dict(item)
    result = await db.execute(
        text(
            "UPDATE inventory_items SET name=COALESCE(:name,name),category=COALESCE(:category,category),active=COALESCE(:active,active),default_supplier_id=:supplier,updated_at=now() WHERE id=:id AND org_id=:org RETURNING *"
        ),
        {
            "id": item_id,
            "org": org_id,
            "name": values.get("name"),
            "category": values.get("category"),
            "active": values.get("active"),
            "supplier": values.get("default_supplier_id"),
        },
    )
    if not (item := result.first()):
        raise not_found("Item not found")
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="inventory_item",
        entity_id=item_id,
        action="updated",
        details=values,
    )
    return as_dict(item)


async def replace_conversions(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ConversionUpdate,
) -> dict[str, Any]:
    item = await db.execute(
        text("SELECT dimension,base_unit FROM inventory_items WHERE id=:id AND org_id=:org"),
        {"id": item_id, "org": org_id},
    )
    if not (row := item.first()):
        raise not_found("Item not found")
    allowed = {"mass": {"kg", "oz", "lb"}, "volume": {"l", "fl_oz"}, "count": set()}[row.dimension]
    if any(
        unit not in allowed or not value.is_finite() or value <= 0
        for unit, value in body.conversions.items()
    ):
        raise validation_error(
            {
                "conversions": "Units must match the item dimension and factors must be finite and positive"
            }
        )
    await db.execute(
        text("DELETE FROM item_unit_conversions WHERE org_id=:org AND item_id=:item"),
        {"org": org_id, "item": item_id},
    )
    for unit, factor in body.conversions.items():
        await db.execute(
            text(
                "INSERT INTO item_unit_conversions(id,org_id,item_id,unit,base_units_per_unit) VALUES(:id,:org,:item,:unit,:factor)"
            ),
            {
                "id": uuid.uuid7(),
                "org": org_id,
                "item": item_id,
                "unit": unit,
                "factor": factor,
            },
        )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="item_conversion",
        entity_id=item_id,
        action="updated",
        details={"conversions": {unit: str(factor) for unit, factor in body.conversions.items()}},
    )
    return {"item_id": str(item_id), "conversions": body.conversions, "base_unit": row.base_unit}


async def create_supplier(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, body: SupplierCreate
) -> dict[str, Any]:
    result = await db.execute(
        text(
            "INSERT INTO suppliers(id,org_id,name,contact_name,email,phone,notes) VALUES(:id,:org,:name,:contact,:email,:phone,:notes) RETURNING *"
        ),
        {
            "id": uuid.uuid7(),
            "org": org_id,
            "name": body.name,
            "contact": body.contact_name,
            "email": body.email,
            "phone": body.phone,
            "notes": body.notes,
        },
    )
    row = as_dict(result.one())
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="supplier",
        entity_id=row["id"],
        action="created",
    )
    return row


async def list_suppliers(db: AsyncSession, org_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
    rows = await db.execute(
        text("SELECT * FROM suppliers WHERE org_id=:org ORDER BY name"), {"org": org_id}
    )
    return {"data": [as_dict(r) for r in rows]}


async def update_supplier(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    supplier_id: uuid.UUID,
    body: SupplierUpdate,
) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise validation_error({"body": "Provide at least one field to update"})
    result = await db.execute(
        text(
            "UPDATE suppliers SET name=COALESCE(:name,name),contact_name=COALESCE(:contact_name,contact_name),"
            "email=COALESCE(:email,email),phone=COALESCE(:phone,phone),notes=COALESCE(:notes,notes),"
            "active=COALESCE(:active,active),updated_at=now() WHERE id=:id AND org_id=:org RETURNING *"
        ),
        {
            "name": values.get("name"),
            "contact_name": values.get("contact_name"),
            "email": values.get("email"),
            "phone": values.get("phone"),
            "notes": values.get("notes"),
            "active": values.get("active"),
            "id": supplier_id,
            "org": org_id,
        },
    )
    if not (row := result.first()):
        raise not_found("Supplier not found")
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="supplier",
        entity_id=supplier_id,
        action="updated",
        details=values,
    )
    return as_dict(row)


async def create_storage_area(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, body: StorageAreaCreate
) -> dict[str, Any]:
    result = await db.execute(
        text(
            "INSERT INTO storage_areas(id,org_id,location_id,name,sort_order) VALUES(:id,:org,:loc,:name,:sort) RETURNING *"
        ),
        {
            "id": uuid.uuid7(),
            "org": org_id,
            "loc": body.location_id,
            "name": body.name,
            "sort": body.sort_order,
        },
    )
    row = as_dict(result.one())
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="storage_area",
        entity_id=row["id"],
        location_id=body.location_id,
        action="created",
    )
    return row


async def list_storage_areas(
    db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> dict[str, list[dict[str, Any]]]:
    rows = await db.execute(
        text(
            "SELECT * FROM storage_areas WHERE org_id=:org AND location_id=:loc ORDER BY sort_order,name"
        ),
        {"org": org_id, "loc": location_id},
    )
    return {"data": [as_dict(r) for r in rows]}


async def update_storage_area(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    allowed_location_ids: Collection[uuid.UUID],
    area_id: uuid.UUID,
    body: StorageAreaUpdate,
) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise validation_error({"body": "Provide at least one field to update"})
    existing = await db.execute(
        text("SELECT location_id FROM storage_areas WHERE id=:id AND org_id=:org"),
        {"id": area_id, "org": org_id},
    )
    if not (area := existing.first()):
        raise not_found("Storage area not found")
    await authorized_location(db, allowed_location_ids, area.location_id)
    result = await db.execute(
        text(
            "UPDATE storage_areas SET name=COALESCE(:name,name),sort_order=COALESCE(:sort,sort_order),active=COALESCE(:active,active),updated_at=now() WHERE id=:id AND org_id=:org RETURNING *"
        ),
        {
            "name": values.get("name"),
            "sort": values.get("sort_order"),
            "active": values.get("active"),
            "id": area_id,
            "org": org_id,
        },
    )
    if not (row := result.first()):
        raise not_found("Storage area not found")
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="storage_area",
        entity_id=area_id,
        location_id=row.location_id,
        action="updated",
        details=values,
    )
    return as_dict(row)


async def set_par(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, item_id: uuid.UUID, body: ParUpdate
) -> dict[str, Any]:
    result = await db.execute(
        text(
            "INSERT INTO inventory_pars(org_id,location_id,item_id,par_base_quantity) SELECT :org,:loc,id,:qty FROM inventory_items WHERE id=:item AND org_id=:org AND active ON CONFLICT(org_id,location_id,item_id) DO UPDATE SET par_base_quantity=excluded.par_base_quantity,updated_at=now() RETURNING *"
        ),
        {
            "org": org_id,
            "loc": body.location_id,
            "item": item_id,
            "qty": body.par_base_quantity,
        },
    )
    if not (row := result.first()):
        raise not_found("Item not found")
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="inventory_par",
        entity_id=item_id,
        location_id=body.location_id,
        action="updated",
    )
    return as_dict(row)


async def create_pack(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, item_id: uuid.UUID, body: PackCreate
) -> dict[str, Any]:
    if body.item_id != item_id:
        raise validation_error({"item_id": "Pack item must match the URL item"})
    if body.currency != "USD":
        raise validation_error({"currency": "Currency must match the organization currency (USD)"})
    item = await db.execute(
        text("SELECT id FROM inventory_items WHERE id=:id AND org_id=:org AND active"),
        {"id": item_id, "org": org_id},
    )
    supplier = await db.execute(
        text("SELECT id FROM suppliers WHERE id=:id AND org_id=:org AND active"),
        {"id": body.supplier_id, "org": org_id},
    )
    if item.first() is None:
        raise not_found("Item not found")
    if supplier.first() is None:
        raise not_found("Supplier not found")
    result = await db.execute(
        text("""INSERT INTO item_packs(id,org_id,item_id,supplier_id,name,purchase_unit,
            base_quantity,last_price_minor,currency) VALUES(:id,:org,:item,:supplier,:name,
            :unit,:quantity,:price,:currency) RETURNING *"""),
        {
            "id": uuid.uuid7(),
            "org": org_id,
            "item": item_id,
            "supplier": body.supplier_id,
            "name": body.name,
            "unit": body.purchase_unit,
            "quantity": body.base_quantity,
            "price": body.last_price_minor,
            "currency": body.currency,
        },
    )
    row = as_dict(result.one())
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="item_pack",
        entity_id=row["id"],
        action="created",
    )
    return row


async def assign_storage_area(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    location_id: uuid.UUID,
    storage_area_id: uuid.UUID,
) -> dict[str, Any]:
    result = await db.execute(
        text("""INSERT INTO item_storage_assignments(org_id,location_id,storage_area_id,item_id)
            SELECT :org,:loc,a.id,i.id FROM storage_areas a,inventory_items i
            WHERE a.id=:area AND a.org_id=:org AND a.location_id=:loc AND a.active
            AND i.id=:item AND i.org_id=:org AND i.active
            ON CONFLICT(org_id,location_id,item_id) DO UPDATE SET storage_area_id=excluded.storage_area_id
            RETURNING *"""),
        {"org": org_id, "loc": location_id, "area": storage_area_id, "item": item_id},
    )
    if not (row := result.first()):
        raise not_found("Item or storage area not found")
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="item_storage_assignment",
        entity_id=item_id,
        location_id=location_id,
        action="updated",
        details={"storage_area_id": str(storage_area_id)},
    )
    return as_dict(row)


async def list_item_packs(
    db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID
) -> dict[str, Any]:
    rows = await db.execute(
        text("SELECT * FROM item_packs WHERE org_id=:org AND item_id=:id ORDER BY name"),
        {"org": org_id, "id": item_id},
    )
    return {"data": [as_dict(r) for r in rows]}


async def list_packs(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    rows = await db.execute(
        text(
            "SELECT p.*,i.name item_name FROM item_packs p JOIN inventory_items i ON i.id=p.item_id AND i.org_id=p.org_id WHERE p.org_id=:org ORDER BY i.name,p.name"
        ),
        {"org": org_id},
    )
    return {"data": [as_dict(row) for row in rows]}


async def update_pack(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    pack_id: uuid.UUID,
    body: PackUpdate,
) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise validation_error({"body": "Provide at least one field to update"})
    result = await db.execute(
        text(
            "UPDATE item_packs SET name=COALESCE(:name,name),purchase_unit=COALESCE(:unit,purchase_unit),base_quantity=COALESCE(:qty,base_quantity),last_price_minor=COALESCE(:price,last_price_minor),active=COALESCE(:active,active),updated_at=now() WHERE id=:id AND item_id=:item AND org_id=:org RETURNING *"
        ),
        {
            "name": values.get("name"),
            "unit": values.get("purchase_unit"),
            "qty": values.get("base_quantity"),
            "price": values.get("last_price_minor"),
            "active": values.get("active"),
            "id": pack_id,
            "item": item_id,
            "org": org_id,
        },
    )
    if not (row := result.first()):
        raise not_found("Pack not found")
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="item_pack",
        entity_id=pack_id,
        action="updated",
        details=values,
    )
    return as_dict(row)
