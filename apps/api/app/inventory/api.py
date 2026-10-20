"""Inventory catalog and stock endpoints."""
# ruff: noqa: E501

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from sqlalchemy import text

from app.audit import service as audit
from app.errors import not_found, validation_error
from app.identity.models import Location
from app.inventory.schemas import (
    ItemCreate,
    ItemUpdate,
    PackCreate,
    ParUpdate,
    StorageAreaCreate,
    SupplierCreate,
)
from app.security.sessions import AuthDep, OrgSession, require_role

router = APIRouter(prefix="/v1/inventory", tags=["inventory"])
_MANAGERS = {"owner", "admin", "manager"}


async def _location(ctx: AuthDep, db: OrgSession, location_id: uuid.UUID) -> Location:
    if location_id not in await ctx.allowed_location_ids(include_archived=False):
        raise not_found("Location not found")
    row = await db.get(Location, location_id)
    if row is None:
        raise not_found("Location not found")
    return row


def _row(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


@router.get("/items")
async def items(
    ctx: AuthDep, db: OrgSession, location_id: Annotated[uuid.UUID, Query()]
) -> dict[str, list[dict[str, Any]]]:
    await _location(ctx, db, location_id)
    rows = await db.execute(
        text("""SELECT i.id,i.name,i.category,i.base_unit,i.dimension,i.active,i.default_supplier_id,i.currency,
        COALESCE(l.on_hand_base_quantity,0) on_hand_base_quantity,p.par_base_quantity,
        (p.par_base_quantity IS NOT NULL AND COALESCE(l.on_hand_base_quantity,0)<p.par_base_quantity) below_par
        FROM inventory_items i LEFT JOIN inventory_levels l ON l.org_id=i.org_id AND l.item_id=i.id AND l.location_id=:loc
        LEFT JOIN inventory_pars p ON p.org_id=i.org_id AND p.item_id=i.id AND p.location_id=:loc
        WHERE i.org_id=:org ORDER BY i.name"""),
        {"org": ctx.org_id, "loc": location_id},
    )
    return {"data": [_row(r) for r in rows]}


@router.post("/items", status_code=status.HTTP_201_CREATED)
async def create_item(body: ItemCreate, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    if body.default_supplier_id is not None:
        supplier = await db.execute(
            text("SELECT id FROM suppliers WHERE id=:id AND org_id=:org AND active"),
            {"id": body.default_supplier_id, "org": ctx.org_id},
        )
        if supplier.first() is None:
            raise not_found("Supplier not found")
    cur = await db.execute(
        text(
            "INSERT INTO inventory_items(id,org_id,name,category,base_unit,dimension,default_supplier_id,currency) VALUES(:id,:org,:name,:category,:unit,:dimension,:supplier,:currency) RETURNING *"
        ),
        {
            "id": uuid.uuid7(),
            "org": ctx.org_id,
            "name": body.name,
            "category": body.category,
            "unit": body.base_unit,
            "dimension": body.dimension,
            "supplier": body.default_supplier_id,
            "currency": body.currency,
        },
    )
    item = _row(cur.one())
    for unit, factor in body.conversions.items():
        await db.execute(
            text(
                "INSERT INTO item_unit_conversions(id,org_id,item_id,unit,base_units_per_unit) VALUES(:id,:org,:item,:unit,:factor)"
            ),
            {
                "id": uuid.uuid7(),
                "org": ctx.org_id,
                "item": item["id"],
                "unit": unit,
                "factor": factor,
            },
        )
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="inventory_item",
        entity_id=item["id"],
        action="created",
    )
    return item


@router.patch("/items/{item_id}")
async def update_item(
    item_id: uuid.UUID, body: ItemUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    values = body.model_dump(exclude_unset=True)
    if not values:
        result = await db.execute(
            text("SELECT * FROM inventory_items WHERE id=:id AND org_id=:org"),
            {"id": item_id, "org": ctx.org_id},
        )
        if not (item := result.first()):
            raise not_found("Item not found")
        return _row(item)
    result = await db.execute(
        text(
            "UPDATE inventory_items SET name=COALESCE(:name,name),category=COALESCE(:category,category),active=COALESCE(:active,active),default_supplier_id=:supplier,updated_at=now() WHERE id=:id AND org_id=:org RETURNING *"
        ),
        {
            "id": item_id,
            "org": ctx.org_id,
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
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="inventory_item",
        entity_id=item_id,
        action="updated",
        details=values,
    )
    return _row(item)


@router.post("/suppliers", status_code=201)
async def create_supplier(body: SupplierCreate, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    result = await db.execute(
        text(
            "INSERT INTO suppliers(id,org_id,name,contact_name,email,phone,notes) VALUES(:id,:org,:name,:contact,:email,:phone,:notes) RETURNING *"
        ),
        {
            "id": uuid.uuid7(),
            "org": ctx.org_id,
            "name": body.name,
            "contact": body.contact_name,
            "email": body.email,
            "phone": body.phone,
            "notes": body.notes,
        },
    )
    row = _row(result.one())
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="supplier",
        entity_id=row["id"],
        action="created",
    )
    return row


@router.get("/suppliers")
async def suppliers(ctx: AuthDep, db: OrgSession) -> dict[str, list[dict[str, Any]]]:
    rows = await db.execute(
        text("SELECT * FROM suppliers WHERE org_id=:org ORDER BY name"), {"org": ctx.org_id}
    )
    return {"data": [_row(r) for r in rows]}


@router.post("/storage-areas", status_code=201)
async def create_storage_area(
    body: StorageAreaCreate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    await _location(ctx, db, body.location_id)
    result = await db.execute(
        text(
            "INSERT INTO storage_areas(id,org_id,location_id,name,sort_order) VALUES(:id,:org,:loc,:name,:sort) RETURNING *"
        ),
        {
            "id": uuid.uuid7(),
            "org": ctx.org_id,
            "loc": body.location_id,
            "name": body.name,
            "sort": body.sort_order,
        },
    )
    row = _row(result.one())
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="storage_area",
        entity_id=row["id"],
        location_id=body.location_id,
        action="created",
    )
    return row


@router.get("/storage-areas")
async def storage_areas(
    ctx: AuthDep, db: OrgSession, location_id: Annotated[uuid.UUID, Query()]
) -> dict[str, list[dict[str, Any]]]:
    await _location(ctx, db, location_id)
    rows = await db.execute(
        text(
            "SELECT * FROM storage_areas WHERE org_id=:org AND location_id=:loc ORDER BY sort_order,name"
        ),
        {"org": ctx.org_id, "loc": location_id},
    )
    return {"data": [_row(r) for r in rows]}


@router.put("/items/{item_id}/par")
async def set_par(
    item_id: uuid.UUID, body: ParUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    await _location(ctx, db, body.location_id)
    result = await db.execute(
        text(
            "INSERT INTO inventory_pars(org_id,location_id,item_id,par_base_quantity) SELECT :org,:loc,id,:qty FROM inventory_items WHERE id=:item AND org_id=:org AND active ON CONFLICT(org_id,location_id,item_id) DO UPDATE SET par_base_quantity=excluded.par_base_quantity,updated_at=now() RETURNING *"
        ),
        {
            "org": ctx.org_id,
            "loc": body.location_id,
            "item": item_id,
            "qty": body.par_base_quantity,
        },
    )
    if not (row := result.first()):
        raise not_found("Item not found")
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="inventory_par",
        entity_id=item_id,
        location_id=body.location_id,
        action="updated",
    )
    return _row(row)


@router.post("/items/{item_id}/packs", status_code=201)
async def create_pack(
    item_id: uuid.UUID, body: PackCreate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    if body.item_id != item_id:
        raise validation_error({"item_id": "Pack item must match the URL item"})
    if body.currency != "USD":
        raise validation_error({"currency": "Currency must match the organization currency (USD)"})
    item = await db.execute(
        text("SELECT id FROM inventory_items WHERE id=:id AND org_id=:org AND active"),
        {"id": item_id, "org": ctx.org_id},
    )
    supplier = await db.execute(
        text("SELECT id FROM suppliers WHERE id=:id AND org_id=:org AND active"),
        {"id": body.supplier_id, "org": ctx.org_id},
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
            "org": ctx.org_id,
            "item": item_id,
            "supplier": body.supplier_id,
            "name": body.name,
            "unit": body.purchase_unit,
            "quantity": body.base_quantity,
            "price": body.last_price_minor,
            "currency": body.currency,
        },
    )
    row = _row(result.one())
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="item_pack",
        entity_id=row["id"],
        action="created",
    )
    return row


@router.put("/items/{item_id}/storage-area")
async def assign_storage_area(
    item_id: uuid.UUID,
    location_id: Annotated[uuid.UUID, Query()],
    storage_area_id: Annotated[uuid.UUID, Query()],
    ctx: AuthDep,
    db: OrgSession,
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    await _location(ctx, db, location_id)
    result = await db.execute(
        text("""INSERT INTO item_storage_assignments(org_id,location_id,storage_area_id,item_id)
            SELECT :org,:loc,a.id,i.id FROM storage_areas a,inventory_items i
            WHERE a.id=:area AND a.org_id=:org AND a.location_id=:loc AND a.active
            AND i.id=:item AND i.org_id=:org AND i.active
            ON CONFLICT(org_id,location_id,item_id) DO UPDATE SET storage_area_id=excluded.storage_area_id
            RETURNING *"""),
        {"org": ctx.org_id, "loc": location_id, "area": storage_area_id, "item": item_id},
    )
    if not (row := result.first()):
        raise not_found("Item or storage area not found")
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="item_storage_assignment",
        entity_id=item_id,
        location_id=location_id,
        action="updated",
        details={"storage_area_id": str(storage_area_id)},
    )
    return _row(row)
