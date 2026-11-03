"""Inventory catalog and stock endpoints."""
# ruff: noqa: E501

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from sqlalchemy import text

from app.audit import service as audit
from app.errors import not_found, validation_error
from app.identity.models import Location
from app.integrations.providers.square.business_date import business_date as calculate_business_date
from app.inventory.food_cost import build_variance
from app.inventory.food_cost_schemas import FoodCostVarianceIn, FoodCostVarianceOut
from app.inventory.schemas import (
    MAX_QUANTITY,
    ConversionUpdate,
    CountEntry,
    CountStart,
    CountSubmit,
    ItemCreate,
    ItemUpdate,
    PackCreate,
    PackUpdate,
    ParUpdate,
    POCreate,
    POUpdate,
    ReceiveRequest,
    RecipeCreate,
    RecipeUpdate,
    StorageAreaCreate,
    StorageAreaUpdate,
    SupplierCreate,
    SupplierUpdate,
    WasteCreate,
)
from app.security.sessions import AuthDep, OrgSession, require_role

router = APIRouter(prefix="/v1/inventory", tags=["inventory"])
_MANAGERS = {"owner", "admin", "manager"}


@router.post("/food-cost-variance", response_model=FoodCostVarianceOut)
async def food_cost_variance(
    body: FoodCostVarianceIn, ctx: AuthDep, db: OrgSession
) -> FoodCostVarianceOut:
    """Count-anchored operational variance. This is not accounting COGS."""
    require_role(ctx, _MANAGERS)
    location = await _location(ctx, db, body.location_id)
    return await build_variance(db, ctx.org_id, location, body)


def _location_business_date(location: Location) -> date:
    return calculate_business_date(
        datetime.now(UTC), location.timezone, location.business_day_start
    )


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
        cost.last_received_cost_minor,cost.cost_as_of,
        (p.par_base_quantity IS NOT NULL AND COALESCE(l.on_hand_base_quantity,0)<p.par_base_quantity) below_par
        FROM inventory_items i LEFT JOIN inventory_levels l ON l.org_id=i.org_id AND l.item_id=i.id AND l.location_id=:loc
        LEFT JOIN inventory_pars p ON p.org_id=i.org_id AND p.item_id=i.id AND p.location_id=:loc
        LEFT JOIN LATERAL (SELECT rl.total_cost_minor/NULLIF(rl.received_base_quantity,0) last_received_cost_minor,r.received_at cost_as_of
          FROM receipt_lines rl JOIN receipts r ON r.org_id=rl.org_id AND r.id=rl.receipt_id
          WHERE rl.org_id=i.org_id AND rl.item_id=i.id ORDER BY r.received_at DESC LIMIT 1) cost ON true
        WHERE i.org_id=:org ORDER BY i.name"""),
        {"org": ctx.org_id, "loc": location_id},
    )
    return {"data": [_row(r) for r in rows]}


@router.post("/waste", status_code=201)
async def create_waste(body: WasteCreate, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    location = await _location(ctx, db, body.location_id)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{ctx.org_id}:waste:{body.idempotency_key}"},
    )
    prior = await db.execute(
        text("""SELECT w.*,i.name item_name,i.base_unit FROM waste_entries w
        JOIN inventory_items i ON i.org_id=w.org_id AND i.id=w.item_id
        WHERE w.org_id=:org AND w.idempotency_key=:key"""),
        {"org": ctx.org_id, "key": body.idempotency_key},
    )
    if row := prior.first():
        return _row(row)
    item_result = await db.execute(
        text("""SELECT i.id,i.name,i.base_unit,i.active,c.base_units_per_unit
        FROM inventory_items i LEFT JOIN item_unit_conversions c ON c.org_id=i.org_id AND c.item_id=i.id
          AND c.unit=:unit WHERE i.org_id=:org AND i.id=:item"""),
        {"org": ctx.org_id, "item": body.item_id, "unit": body.unit},
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
        {"org": ctx.org_id, "loc": body.location_id, "item": body.item_id},
    )
    on_hand = level.scalar_one_or_none()
    if on_hand is None or on_hand < quantity:
        raise validation_error({"quantity": "Waste quantity exceeds available stock"})
    cost_row = await db.execute(
        text("""SELECT rl.total_cost_minor/NULLIF(rl.received_base_quantity,0)
        FROM receipt_lines rl JOIN receipts r ON r.org_id=rl.org_id AND r.id=rl.receipt_id
        WHERE rl.org_id=:org AND rl.item_id=:item ORDER BY r.received_at DESC LIMIT 1"""),
        {"org": ctx.org_id, "item": body.item_id},
    )
    unit_cost = cost_row.scalar_one_or_none()
    waste_id = uuid.uuid7()
    business_day = _location_business_date(location)
    await db.execute(
        text("""INSERT INTO waste_entries(id,org_id,location_id,item_id,quantity_base,unit_cost_minor,reason,
        business_date,idempotency_key,created_by) VALUES(:id,:org,:loc,:item,:qty,:cost,:reason,:date,:key,:user)"""),
        {
            "id": waste_id,
            "org": ctx.org_id,
            "loc": body.location_id,
            "item": body.item_id,
            "qty": quantity,
            "cost": unit_cost,
            "reason": body.reason,
            "date": business_day,
            "key": body.idempotency_key,
            "user": ctx.user_id,
        },
    )
    await db.execute(
        text("""UPDATE inventory_levels SET on_hand_base_quantity=on_hand_base_quantity-:qty,
        version=version+1,updated_at=now() WHERE org_id=:org AND location_id=:loc AND item_id=:item"""),
        {"qty": quantity, "org": ctx.org_id, "loc": body.location_id, "item": body.item_id},
    )
    await db.execute(
        text("""INSERT INTO stock_movements(id,org_id,location_id,item_id,movement_type,source_id,
        quantity_delta,business_date,item_name,base_unit) VALUES(:id,:org,:loc,:item,'waste',:source,:delta,:date,:name,:unit)"""),
        {
            "id": uuid.uuid7(),
            "org": ctx.org_id,
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
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
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
        {"id": waste_id, "org": ctx.org_id},
    )
    return _row(saved.one())


@router.get("/waste")
async def waste_log(
    ctx: AuthDep,
    db: OrgSession,
    location_id: Annotated[uuid.UUID, Query()],
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
) -> dict[str, list[dict[str, Any]]]:
    await _location(ctx, db, location_id)
    if to_date < from_date or (to_date - from_date).days > 366:
        raise validation_error({"to": "Date range must be ordered and no longer than 367 days"})
    rows = await db.execute(
        text("""SELECT w.*,i.name item_name,i.base_unit FROM waste_entries w
        JOIN inventory_items i ON i.org_id=w.org_id AND i.id=w.item_id WHERE w.org_id=:org
        AND w.location_id=:loc AND w.business_date BETWEEN :start AND :end
        ORDER BY w.business_date DESC,w.created_at DESC"""),
        {"org": ctx.org_id, "loc": location_id, "start": from_date, "end": to_date},
    )
    return {"data": [_row(row) for row in rows]}


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


@router.put("/items/{item_id}/conversions")
async def replace_conversions(
    item_id: uuid.UUID, body: ConversionUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    item = await db.execute(
        text("SELECT dimension,base_unit FROM inventory_items WHERE id=:id AND org_id=:org"),
        {"id": item_id, "org": ctx.org_id},
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
        {"org": ctx.org_id, "item": item_id},
    )
    for unit, factor in body.conversions.items():
        await db.execute(
            text(
                "INSERT INTO item_unit_conversions(id,org_id,item_id,unit,base_units_per_unit) VALUES(:id,:org,:item,:unit,:factor)"
            ),
            {
                "id": uuid.uuid7(),
                "org": ctx.org_id,
                "item": item_id,
                "unit": unit,
                "factor": factor,
            },
        )
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="item_conversion",
        entity_id=item_id,
        action="updated",
        details={"conversions": {unit: str(factor) for unit, factor in body.conversions.items()}},
    )
    return {"item_id": str(item_id), "conversions": body.conversions, "base_unit": row.base_unit}


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


@router.patch("/suppliers/{supplier_id}")
async def update_supplier(
    supplier_id: uuid.UUID, body: SupplierUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
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
            "org": ctx.org_id,
        },
    )
    if not (row := result.first()):
        raise not_found("Supplier not found")
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="supplier",
        entity_id=supplier_id,
        action="updated",
        details=values,
    )
    return _row(row)


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


@router.patch("/storage-areas/{area_id}")
async def update_storage_area(
    area_id: uuid.UUID, body: StorageAreaUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise validation_error({"body": "Provide at least one field to update"})
    existing = await db.execute(
        text("SELECT location_id FROM storage_areas WHERE id=:id AND org_id=:org"),
        {"id": area_id, "org": ctx.org_id},
    )
    if not (area := existing.first()):
        raise not_found("Storage area not found")
    await _location(ctx, db, area.location_id)
    result = await db.execute(
        text(
            "UPDATE storage_areas SET name=COALESCE(:name,name),sort_order=COALESCE(:sort,sort_order),active=COALESCE(:active,active),updated_at=now() WHERE id=:id AND org_id=:org RETURNING *"
        ),
        {
            "name": values.get("name"),
            "sort": values.get("sort_order"),
            "active": values.get("active"),
            "id": area_id,
            "org": ctx.org_id,
        },
    )
    if not (row := result.first()):
        raise not_found("Storage area not found")
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="storage_area",
        entity_id=area_id,
        location_id=row.location_id,
        action="updated",
        details=values,
    )
    return _row(row)


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


def _conflict(detail: str) -> Exception:
    from app.errors import ProblemError

    return ProblemError(409, "conflict", detail)


@router.get("/items/{item_id}/packs")
async def item_packs(item_id: uuid.UUID, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    rows = await db.execute(
        text("SELECT * FROM item_packs WHERE org_id=:org AND item_id=:id ORDER BY name"),
        {"org": ctx.org_id, "id": item_id},
    )
    return {"data": [_row(r) for r in rows]}


@router.get("/packs")
async def packs(ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    rows = await db.execute(
        text(
            "SELECT p.*,i.name item_name FROM item_packs p JOIN inventory_items i ON i.id=p.item_id AND i.org_id=p.org_id WHERE p.org_id=:org ORDER BY i.name,p.name"
        ),
        {"org": ctx.org_id},
    )
    return {"data": [_row(row) for row in rows]}


@router.patch("/items/{item_id}/packs/{pack_id}")
async def update_pack(
    item_id: uuid.UUID, pack_id: uuid.UUID, body: PackUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
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
            "org": ctx.org_id,
        },
    )
    if not (row := result.first()):
        raise not_found("Pack not found")
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="item_pack",
        entity_id=pack_id,
        action="updated",
        details=values,
    )
    return _row(row)


@router.get("/count-sessions")
async def count_sessions(
    location_id: Annotated[uuid.UUID, Query()], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    await _location(ctx, db, location_id)
    rows = await db.execute(
        text(
            "SELECT s.*,a.name AS storage_area_name FROM count_sessions s JOIN storage_areas a ON a.id=s.storage_area_id AND a.org_id=s.org_id WHERE s.org_id=:org AND s.location_id=:loc ORDER BY s.started_at DESC"
        ),
        {"org": ctx.org_id, "loc": location_id},
    )
    return {"data": [_row(r) for r in rows]}


@router.post("/count-sessions", status_code=201)
async def start_count(body: CountStart, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    await _location(ctx, db, body.location_id)
    area = await db.execute(
        text(
            "SELECT id FROM storage_areas WHERE id=:id AND org_id=:org AND location_id=:loc AND active"
        ),
        {"id": body.storage_area_id, "org": ctx.org_id, "loc": body.location_id},
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
            "org": ctx.org_id,
            "loc": body.location_id,
            "area": body.storage_area_id,
            "user": ctx.user_id,
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
        {"org": ctx.org_id, "loc": body.location_id, "area": body.storage_area_id},
    )
    await db.execute(
        text("""INSERT INTO count_lines(id,org_id,location_id,session_id,item_id,expected_base_quantity,expected_level_version,item_name,base_unit)
      SELECT gen_random_uuid(),:org,:loc,:session,i.id,COALESCE(l.on_hand_base_quantity,0),COALESCE(l.version,0),i.name,i.base_unit
      FROM item_storage_assignments a JOIN inventory_items i ON i.id=a.item_id AND i.org_id=a.org_id
      LEFT JOIN inventory_levels l ON l.org_id=i.org_id AND l.item_id=i.id AND l.location_id=:loc
      WHERE a.org_id=:org AND a.location_id=:loc AND a.storage_area_id=:area AND i.active"""),
        {
            "org": ctx.org_id,
            "loc": body.location_id,
            "session": session_id,
            "area": body.storage_area_id,
        },
    )
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="count_session",
        entity_id=session_id,
        location_id=body.location_id,
        action="started",
    )
    return _row(result.one())


@router.get("/count-sessions/{session_id}")
async def count_detail(session_id: uuid.UUID, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    session = await db.execute(
        text("SELECT * FROM count_sessions WHERE id=:id AND org_id=:org"),
        {"id": session_id, "org": ctx.org_id},
    )
    if not (s := session.first()):
        raise not_found("Count session not found")
    await _location(ctx, db, s.location_id)
    lines = await db.execute(
        text("SELECT * FROM count_lines WHERE org_id=:org AND session_id=:id ORDER BY item_name"),
        {"org": ctx.org_id, "id": session_id},
    )
    return {**_row(s), "lines": [_row(r) for r in lines]}


@router.put("/count-sessions/{session_id}/lines/{item_id}")
async def save_count_line(
    session_id: uuid.UUID, item_id: uuid.UUID, body: CountEntry, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    row = await db.execute(
        text(
            "SELECT c.*,s.status FROM count_lines c JOIN count_sessions s ON s.id=c.session_id AND s.org_id=c.org_id WHERE c.org_id=:org AND c.session_id=:session AND c.item_id=:item FOR UPDATE OF s"
        ),
        {"org": ctx.org_id, "session": session_id, "item": item_id},
    )
    if not (line := row.first()):
        raise not_found("Count line not found")
    if line.status != "draft":
        raise _conflict("Submitted count sessions are immutable")
    if ctx.role not in {"employee", *_MANAGERS}:
        raise _conflict("Role cannot enter counts")
    await _location(ctx, db, line.location_id)
    conversion = await db.scalar(
        text(
            "SELECT base_units_per_unit FROM item_unit_conversions WHERE org_id=:org AND item_id=:item AND unit=:unit"
        ),
        {"org": ctx.org_id, "item": item_id, "unit": body.unit},
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
            "org": ctx.org_id,
            "session": session_id,
            "item": item_id,
        },
    )
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="count_line",
        entity_id=line.id,
        location_id=line.location_id,
        action="saved",
    )
    return _row(result.one())


@router.post("/count-sessions/{session_id}/lines/{item_id}/confirm")
async def confirm_count_exception(
    session_id: uuid.UUID, item_id: uuid.UUID, body: dict[str, str], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    if not body.get("note", "").strip():
        raise validation_error({"note": "A review note is required"})
    session = await db.execute(
        text(
            "SELECT status,location_id FROM count_sessions WHERE id=:id AND org_id=:org FOR UPDATE"
        ),
        {"id": session_id, "org": ctx.org_id},
    )
    if not (count := session.first()):
        raise not_found("Count session not found")
    await _location(ctx, db, count.location_id)
    if count.status != "draft":
        raise _conflict("Submitted count sessions are immutable")
    result = await db.execute(
        text(
            "UPDATE count_lines SET exception_state='confirmed',note=:note WHERE org_id=:org AND session_id=:session AND item_id=:item AND counted_base_quantity IS NOT NULL AND exception_state='review_required' RETURNING *"
        ),
        {"note": body["note"], "org": ctx.org_id, "session": session_id, "item": item_id},
    )
    if not (line := result.first()):
        raise not_found("Review-required count line not found")
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="count_line",
        entity_id=line.id,
        location_id=line.location_id,
        action="variance_confirmed",
        details={"note": body["note"]},
    )
    return _row(line)


@router.post("/count-sessions/{session_id}/submit")
async def submit_count(
    session_id: uuid.UUID, body: CountSubmit, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    session = await db.execute(
        text("SELECT * FROM count_sessions WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": session_id, "org": ctx.org_id},
    )
    if not (s := session.first()):
        raise not_found("Count session not found")
    await _location(ctx, db, s.location_id)
    if s.status != "draft":
        raise _conflict("Count session has already been submitted")
    lines = await db.execute(
        text("SELECT * FROM count_lines WHERE org_id=:org AND session_id=:id FOR UPDATE"),
        {"org": ctx.org_id, "id": session_id},
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
        {"org": ctx.org_id, "loc": s.location_id, "items": item_ids},
    )
    versions = {x.item_id: x.version for x in levels}
    if any(versions.get(x.item_id, 0) != x.expected_level_version for x in all_lines):
        raise _conflict("Stock changed after this count started. Start a fresh count and recount.")
    business_day = _location_business_date(await _location(ctx, db, s.location_id))
    for x in all_lines:
        delta = x.counted_base_quantity - x.expected_base_quantity
        await db.execute(
            text(
                "INSERT INTO inventory_levels(org_id,location_id,item_id,on_hand_base_quantity,version,updated_at) VALUES(:org,:loc,:item,:qty,1,now()) ON CONFLICT(org_id,location_id,item_id) DO UPDATE SET on_hand_base_quantity=excluded.on_hand_base_quantity,version=inventory_levels.version+1,updated_at=now()"
            ),
            {
                "org": ctx.org_id,
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
                "org": ctx.org_id,
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
        {"user": ctx.user_id, "id": session_id, "org": ctx.org_id},
    )
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="count_session",
        entity_id=session_id,
        location_id=s.location_id,
        action="submitted",
    )
    return _row(result.one())


@router.get("/purchase-orders")
async def purchase_orders(
    location_id: Annotated[uuid.UUID, Query()], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    await _location(ctx, db, location_id)
    rows = await db.execute(
        text(
            "SELECT p.*,s.name supplier_name FROM purchase_orders p JOIN suppliers s ON s.id=p.supplier_id AND s.org_id=p.org_id WHERE p.org_id=:org AND p.location_id=:loc ORDER BY p.ordered_at DESC"
        ),
        {"org": ctx.org_id, "loc": location_id},
    )
    return {"data": [_row(r) for r in rows]}


@router.patch("/purchase-orders/{po_id}")
async def update_purchase_order(
    po_id: uuid.UUID, body: POUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    order = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": po_id, "org": ctx.org_id},
    )
    if not (po := order.first()):
        raise not_found("Purchase order not found")
    await _location(ctx, db, po.location_id)
    if po.status == "closed":
        raise _conflict("Reopen a closed purchase order before editing")
    if body.lines is not None:
        existing_lines = await db.execute(
            text(
                "SELECT received_quantity FROM purchase_order_lines WHERE org_id=:org AND purchase_order_id=:id FOR UPDATE"
            ),
            {"org": ctx.org_id, "id": po_id},
        )
        if any(line.received_quantity > 0 for line in existing_lines):
            raise _conflict("Orders with posted receipts cannot have their lines replaced")
        await db.execute(
            text("DELETE FROM purchase_order_lines WHERE org_id=:org AND purchase_order_id=:id"),
            {"org": ctx.org_id, "id": po_id},
        )
        for line_request in body.lines:
            pack = await db.execute(
                text(
                    "SELECT p.*,i.name item_name,i.base_unit FROM item_packs p JOIN inventory_items i ON i.id=p.item_id AND i.org_id=p.org_id WHERE p.id=:id AND p.org_id=:org AND p.active AND i.active AND p.supplier_id=:supplier"
                ),
                {"id": line_request.pack_id, "org": ctx.org_id, "supplier": po.supplier_id},
            )
            if not (pack_row := pack.first()):
                raise not_found("Active pack for this supplier not found")
            await db.execute(
                text(
                    "INSERT INTO purchase_order_lines(id,org_id,location_id,purchase_order_id,item_id,pack_id,ordered_packs,ordered_pack_name,ordered_base_quantity_per_pack,ordered_unit_price_minor,ordered_quantity,item_name,base_unit,pack_id_snapshot) VALUES(:id,:org,:loc,:po,:item,:pack,:packs,:pack_name,:base,:price,:qty,:item_name,:unit,:pack)"
                ),
                {
                    "id": uuid.uuid7(),
                    "org": ctx.org_id,
                    "loc": po.location_id,
                    "po": po_id,
                    "item": pack_row.item_id,
                    "pack": pack_row.id,
                    "packs": line_request.ordered_packs,
                    "pack_name": pack_row.name,
                    "base": pack_row.base_quantity,
                    "price": line_request.unit_price_minor,
                    "qty": line_request.ordered_packs * pack_row.base_quantity,
                    "item_name": pack_row.item_name,
                    "unit": pack_row.base_unit,
                },
            )
    result = await db.execute(
        text(
            "UPDATE purchase_orders SET expected_at=COALESCE(:expected,expected_at),note=COALESCE(:note,note),updated_at=now() WHERE id=:id AND org_id=:org RETURNING *"
        ),
        {"expected": body.expected_at, "note": body.note, "id": po_id, "org": ctx.org_id},
    )
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="purchase_order",
        entity_id=po_id,
        location_id=po.location_id,
        action="updated",
    )
    return _row(result.one())


@router.post("/purchase-orders", status_code=201)
async def create_purchase_order(body: POCreate, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    await _location(ctx, db, body.location_id)
    po_id = uuid.uuid7()
    supplier = await db.scalar(
        text("SELECT id FROM suppliers WHERE id=:id AND org_id=:org AND active"),
        {"id": body.supplier_id, "org": ctx.org_id},
    )
    if supplier is None:
        raise not_found("Supplier not found")
    await db.execute(
        text(
            "INSERT INTO purchase_orders(id,org_id,location_id,supplier_id,created_by,currency,expected_at,note) VALUES(:id,:org,:loc,:supplier,:user,:currency,:expected,:note)"
        ),
        {
            "id": po_id,
            "org": ctx.org_id,
            "loc": body.location_id,
            "supplier": body.supplier_id,
            "user": ctx.user_id,
            "currency": body.currency,
            "expected": body.expected_at,
            "note": body.note,
        },
    )
    for line_request in body.lines:
        raw = line_request.model_dump()
        pack_id = uuid.UUID(str(raw["pack_id"]))
        packs = await db.execute(
            text(
                "SELECT p.*,i.name item_name,i.base_unit FROM item_packs p JOIN inventory_items i ON i.id=p.item_id AND i.org_id=p.org_id WHERE p.id=:id AND p.org_id=:org AND p.active AND i.active AND p.supplier_id=:supplier"
            ),
            {"id": pack_id, "org": ctx.org_id, "supplier": body.supplier_id},
        )
        if not (pack := packs.first()):
            raise not_found("Pack not found for this supplier")
        packs_count = Decimal(str(raw["ordered_packs"]))
        unit_price = int(str(raw["unit_price_minor"]))
        await db.execute(
            text(
                "INSERT INTO purchase_order_lines(id,org_id,location_id,purchase_order_id,item_id,pack_id,ordered_packs,ordered_pack_name,ordered_base_quantity_per_pack,ordered_unit_price_minor,ordered_quantity,item_name,base_unit,pack_id_snapshot) VALUES(:id,:org,:loc,:po,:item,:pack,:packs,:name,:base,:price,:qty,:item_name,:unit,:pack)"
            ),
            {
                "id": uuid.uuid7(),
                "org": ctx.org_id,
                "loc": body.location_id,
                "po": po_id,
                "item": pack.item_id,
                "pack": pack.id,
                "packs": packs_count,
                "name": pack.name,
                "base": pack.base_quantity,
                "price": unit_price,
                "qty": packs_count * pack.base_quantity,
                "item_name": pack.item_name,
                "unit": pack.base_unit,
            },
        )
    result = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org"),
        {"id": po_id, "org": ctx.org_id},
    )
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="purchase_order",
        entity_id=po_id,
        location_id=body.location_id,
        action="created",
    )
    return {**_row(result.one()), "lines": []}


@router.get("/purchase-orders/{po_id}")
async def purchase_order_detail(po_id: uuid.UUID, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    result = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org"),
        {"id": po_id, "org": ctx.org_id},
    )
    if not (po := result.first()):
        raise not_found("Purchase order not found")
    await _location(ctx, db, po.location_id)
    lines = await db.execute(
        text(
            "SELECT * FROM purchase_order_lines WHERE org_id=:org AND purchase_order_id=:id ORDER BY item_name"
        ),
        {"org": ctx.org_id, "id": po_id},
    )
    return {**_row(po), "lines": [_row(r) for r in lines]}


@router.post("/purchase-orders/{po_id}/close")
async def close_purchase_order(
    po_id: uuid.UUID, body: dict[str, str], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    if not body.get("reason", "").strip():
        raise validation_error({"reason": "A close reason is required"})
    po = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": po_id, "org": ctx.org_id},
    )
    if not (order := po.first()):
        raise not_found("Purchase order not found")
    if order.status == "closed":
        raise _conflict("Purchase order is already closed")
    await _location(ctx, db, order.location_id)
    await db.execute(
        text(
            "UPDATE purchase_order_lines SET closed_quantity=ordered_quantity-received_quantity WHERE org_id=:org AND purchase_order_id=:id"
        ),
        {"org": ctx.org_id, "id": po_id},
    )
    result = await db.execute(
        text(
            "UPDATE purchase_orders SET status='closed',updated_at=now() WHERE id=:id AND org_id=:org RETURNING *"
        ),
        {"id": po_id, "org": ctx.org_id},
    )
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="purchase_order",
        entity_id=po_id,
        location_id=order.location_id,
        action="closed",
        details={"reason": body["reason"]},
    )
    return _row(result.one())


@router.post("/purchase-orders/{po_id}/reopen")
async def reopen_purchase_order(
    po_id: uuid.UUID, body: dict[str, str], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    if not body.get("reason", "").strip():
        raise validation_error({"reason": "A reopen reason is required"})
    existing = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": po_id, "org": ctx.org_id},
    )
    if not (closed_order := existing.first()):
        raise not_found("Purchase order not found")
    await _location(ctx, db, closed_order.location_id)
    if closed_order.status != "closed":
        raise _conflict("Only a closed purchase order can be reopened")
    await db.execute(
        text(
            "UPDATE purchase_order_lines SET closed_quantity=0 WHERE org_id=:org AND purchase_order_id=:id"
        ),
        {"org": ctx.org_id, "id": po_id},
    )
    result = await db.execute(
        text(
            "UPDATE purchase_orders SET status=CASE WHEN EXISTS(SELECT 1 FROM purchase_order_lines l WHERE l.org_id=:org AND l.purchase_order_id=:id AND l.received_quantity>0) THEN 'partially_received' ELSE 'open' END,updated_at=now() WHERE id=:id AND org_id=:org AND status='closed' RETURNING *"
        ),
        {"id": po_id, "org": ctx.org_id},
    )
    if not (order := result.first()):
        raise not_found("Closed purchase order not found")
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="purchase_order",
        entity_id=po_id,
        location_id=order.location_id,
        action="reopened",
        details={"reason": body["reason"]},
    )
    return _row(order)


@router.post("/purchase-orders/{po_id}/receive", status_code=201)
async def receive_purchase_order(
    po_id: uuid.UUID, body: ReceiveRequest, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    po = await db.execute(
        text("SELECT * FROM purchase_orders WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": po_id, "org": ctx.org_id},
    )
    if not (order := po.first()):
        raise not_found("Purchase order not found")
    await _location(ctx, db, order.location_id)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{ctx.org_id}:{body.idempotency_key}"},
    )
    previous = await db.execute(
        text("SELECT * FROM receipts WHERE org_id=:org AND idempotency_key=:key"),
        {"org": ctx.org_id, "key": body.idempotency_key},
    )
    if prior := previous.first():
        if prior.purchase_order_id != po_id:
            raise _conflict("This idempotency key was already used for another purchase order")
        original_lines = await db.execute(
            text("SELECT * FROM receipt_lines WHERE org_id=:org AND receipt_id=:id ORDER BY id"),
            {"org": ctx.org_id, "id": prior.id},
        )
        saved_lines = list(original_lines)
        saved = {
            row.purchase_order_line_id: (row.received_base_quantity, row.total_cost_minor)
            for row in saved_lines
        }
        requested = {
            line.line_id: (line.received_base_quantity, line.total_cost_minor)
            for line in body.lines
        }
        # The posted quantities/costs and approval reasons are durable, including receipts
        # created before this check existed. Compare semantics, independent of line order.
        details = await db.scalar(
            text(
                "SELECT details FROM audit_events WHERE org_id=:org AND entity_id=:id "
                "AND entity_type='receipt' AND action='posted'"
            ),
            {"org": ctx.org_id, "id": prior.id},
        )
        saved_overages = {
            entry["line_id"]: entry["reason"] for entry in (details or {}).get("overages", [])
        }
        requested_overages = {
            str(line.line_id): str(line.reason) for line in body.lines if line.accept_overage
        }
        if saved != requested or saved_overages != requested_overages:
            raise _conflict("This idempotency key was already used with a different request")
        return {**_row(prior), "lines": [_row(row) for row in saved_lines]}
    if order.status == "closed":
        raise _conflict("Reopen this purchase order before receiving")
    business_day = _location_business_date(await _location(ctx, db, order.location_id))
    receipt_id = uuid.uuid7()
    await db.execute(
        text(
            "INSERT INTO receipts(id,org_id,location_id,purchase_order_id,received_by,business_date,idempotency_key) VALUES(:id,:org,:loc,:po,:user,:date,:key)"
        ),
        {
            "id": receipt_id,
            "org": ctx.org_id,
            "loc": order.location_id,
            "po": po_id,
            "user": ctx.user_id,
            "date": business_day,
            "key": body.idempotency_key,
        },
    )
    for line_request in body.lines:
        raw = line_request.model_dump()
        line_id = uuid.UUID(str(raw["line_id"]))
        q = Decimal(str(raw["received_base_quantity"]))
        cost = int(str(raw["total_cost_minor"]))
        rows = await db.execute(
            text(
                "SELECT * FROM purchase_order_lines WHERE id=:id AND org_id=:org AND purchase_order_id=:po FOR UPDATE"
            ),
            {"id": line_id, "org": ctx.org_id, "po": po_id},
        )
        if not (line := rows.first()):
            raise not_found("Purchase order line not found")
        over = bool(raw.get("accept_overage", False))
        if over and not str(raw.get("reason", "")).strip():
            raise validation_error({"reason": "A reason is required to accept an overage"})
        if q <= 0 or (
            q > line.ordered_quantity - line.received_quantity - line.closed_quantity
            and not (over and ctx.role in _MANAGERS)
        ):
            raise _conflict(
                "Received quantity exceeds the remaining order. Manager approval is required for overage."
            )
        await db.execute(
            text(
                "UPDATE purchase_order_lines SET received_quantity=received_quantity+:q WHERE id=:id AND org_id=:org"
            ),
            {"q": q, "id": line_id, "org": ctx.org_id},
        )
        await db.execute(
            text(
                "INSERT INTO receipt_lines(id,org_id,location_id,receipt_id,purchase_order_line_id,item_id,received_base_quantity,total_cost_minor,item_name,base_unit) VALUES(:id,:org,:loc,:receipt,:line,:item,:q,:cost,:name,:unit)"
            ),
            {
                "id": uuid.uuid7(),
                "org": ctx.org_id,
                "loc": order.location_id,
                "receipt": receipt_id,
                "line": line_id,
                "item": line.item_id,
                "q": q,
                "cost": cost,
                "name": line.item_name,
                "unit": line.base_unit,
            },
        )
        await db.execute(
            text(
                "INSERT INTO inventory_levels(org_id,location_id,item_id,on_hand_base_quantity,version,updated_at) VALUES(:org,:loc,:item,:q,1,now()) ON CONFLICT(org_id,location_id,item_id) DO UPDATE SET on_hand_base_quantity=inventory_levels.on_hand_base_quantity+excluded.on_hand_base_quantity,version=inventory_levels.version+1,updated_at=now()"
            ),
            {"org": ctx.org_id, "loc": order.location_id, "item": line.item_id, "q": q},
        )
        await db.execute(
            text(
                "INSERT INTO stock_movements(id,org_id,location_id,item_id,movement_type,source_id,quantity_delta,business_date,item_name,base_unit) VALUES(:id,:org,:loc,:item,'receipt',:source,:q,:date,:name,:unit)"
            ),
            {
                "id": uuid.uuid7(),
                "org": ctx.org_id,
                "loc": order.location_id,
                "item": line.item_id,
                "source": receipt_id,
                "q": q,
                "date": business_day,
                "name": line.item_name,
                "unit": line.base_unit,
            },
        )
    await db.execute(
        text(
            "UPDATE purchase_orders SET status=CASE WHEN EXISTS(SELECT 1 FROM purchase_order_lines l WHERE l.org_id=:org AND l.purchase_order_id=:id AND l.ordered_quantity>l.received_quantity+l.closed_quantity) THEN 'partially_received' ELSE 'closed' END,updated_at=now() WHERE id=:id AND org_id=:org"
        ),
        {"id": po_id, "org": ctx.org_id},
    )
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="receipt",
        entity_id=receipt_id,
        location_id=order.location_id,
        action="posted",
        details={
            "overages": [
                {"line_id": str(line.line_id), "reason": str(line.reason)}
                for line in body.lines
                if line.accept_overage
            ]
        },
    )
    receipt = await db.execute(
        text("SELECT * FROM receipts WHERE id=:id AND org_id=:org"),
        {"id": receipt_id, "org": ctx.org_id},
    )
    lines = await db.execute(
        text("SELECT * FROM receipt_lines WHERE receipt_id=:id AND org_id=:org"),
        {"id": receipt_id, "org": ctx.org_id},
    )
    return {**_row(receipt.one()), "lines": [_row(r) for r in lines]}


@router.get("/recipes")
async def recipes(ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    result = await db.execute(
        text("SELECT * FROM recipes WHERE org_id=:org ORDER BY name"), {"org": ctx.org_id}
    )
    return {"data": [_row(r) for r in result]}


@router.post("/recipes", status_code=201)
async def create_recipe(body: RecipeCreate, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    if len({ingredient.item_id for ingredient in body.ingredients}) != len(body.ingredients):
        raise validation_error(
            {"ingredients": "An inventory item may only appear once in a recipe"}
        )
    recipe_id = uuid.uuid7()
    await db.execute(
        text(
            "INSERT INTO recipes(id,org_id,name,yield_quantity,yield_unit) VALUES(:id,:org,:name,:qty,:unit)"
        ),
        {
            "id": recipe_id,
            "org": ctx.org_id,
            "name": body.name,
            "qty": body.yield_quantity,
            "unit": body.yield_unit,
        },
    )
    for ingredient in body.ingredients:
        raw = ingredient.model_dump()
        item_id = uuid.UUID(str(raw["item_id"]))
        qty = Decimal(str(raw["quantity_base"]))
        item = await db.execute(
            text(
                "SELECT name,base_unit FROM inventory_items WHERE id=:id AND org_id=:org AND active"
            ),
            {"id": item_id, "org": ctx.org_id},
        )
        if not (i := item.first()):
            raise not_found("Active item not found")
        await db.execute(
            text(
                "INSERT INTO recipe_ingredients(id,org_id,recipe_id,item_id,quantity_base,item_name,base_unit) VALUES(:id,:org,:recipe,:item,:qty,:name,:unit)"
            ),
            {
                "id": uuid.uuid7(),
                "org": ctx.org_id,
                "recipe": recipe_id,
                "item": item_id,
                "qty": qty,
                "name": i.name,
                "unit": i.base_unit,
            },
        )
    await _snapshot_recipe(db, ctx.org_id, recipe_id, ctx.user_id)
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="recipe",
        entity_id=recipe_id,
        action="created",
    )
    return {
        "id": str(recipe_id),
        "name": body.name,
        "yield_quantity": body.yield_quantity,
        "yield_unit": body.yield_unit,
    }


@router.patch("/recipes/{recipe_id}")
async def update_recipe(
    recipe_id: uuid.UUID, body: RecipeUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    current = await db.execute(
        text("SELECT * FROM recipes WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": recipe_id, "org": ctx.org_id},
    )
    if current.first() is None:
        raise not_found("Recipe not found")
    if body.ingredients is not None and len(
        {ingredient.item_id for ingredient in body.ingredients}
    ) != len(body.ingredients):
        raise validation_error(
            {"ingredients": "An inventory item may only appear once in a recipe"}
        )
    result = await db.execute(
        text(
            "UPDATE recipes SET name=COALESCE(:name,name),yield_quantity=COALESCE(:yield,yield_quantity),yield_unit=COALESCE(:unit,yield_unit),active=COALESCE(:active,active),updated_at=now() WHERE id=:id AND org_id=:org RETURNING *"
        ),
        {
            "name": body.name,
            "yield": body.yield_quantity,
            "unit": body.yield_unit,
            "active": body.active,
            "id": recipe_id,
            "org": ctx.org_id,
        },
    )
    if body.ingredients is not None:
        await db.execute(
            text("DELETE FROM recipe_ingredients WHERE org_id=:org AND recipe_id=:id"),
            {"org": ctx.org_id, "id": recipe_id},
        )
        for ingredient in body.ingredients:
            raw = ingredient.model_dump()
            item_id = uuid.UUID(str(raw["item_id"]))
            item = await db.execute(
                text(
                    "SELECT name,base_unit FROM inventory_items WHERE id=:id AND org_id=:org AND active"
                ),
                {"id": item_id, "org": ctx.org_id},
            )
            if not (i := item.first()):
                raise not_found("Active item not found")
            await db.execute(
                text(
                    "INSERT INTO recipe_ingredients(id,org_id,recipe_id,item_id,quantity_base,item_name,base_unit) VALUES(:id,:org,:recipe,:item,:qty,:name,:unit)"
                ),
                {
                    "id": uuid.uuid7(),
                    "org": ctx.org_id,
                    "recipe": recipe_id,
                    "item": item_id,
                    "qty": Decimal(str(raw["quantity_base"])),
                    "name": i.name,
                    "unit": i.base_unit,
                },
            )
    await _snapshot_recipe(db, ctx.org_id, recipe_id, ctx.user_id)
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="recipe",
        entity_id=recipe_id,
        action="updated",
    )
    return _row(result.one())


async def _snapshot_recipe(
    db: OrgSession, org_id: uuid.UUID, recipe_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """Append an immutable snapshot after every recipe write."""
    recipe = await db.execute(
        text("SELECT * FROM recipes WHERE id=:id AND org_id=:org"),
        {"id": recipe_id, "org": org_id},
    )
    row = recipe.first()
    if row is None:
        raise not_found("Recipe not found")
    version = int(
        await db.scalar(
            text(
                "SELECT COALESCE(MAX(version),0)+1 FROM recipe_versions WHERE org_id=:org AND recipe_id=:id"
            ),
            {"org": org_id, "id": recipe_id},
        )
    )
    version_id = uuid.uuid7()
    await db.execute(
        text("""INSERT INTO recipe_versions(id,org_id,recipe_id,version,name,yield_quantity,yield_unit,active,created_by,effective_at)
        VALUES(:id,:org,:recipe,:version,:name,:yield,:unit,:active,:actor,clock_timestamp())"""),
        {
            "id": version_id,
            "org": org_id,
            "recipe": recipe_id,
            "version": version,
            "name": row.name,
            "yield": row.yield_quantity,
            "unit": row.yield_unit,
            "active": row.active,
            "actor": actor_id,
        },
    )
    await db.execute(
        text("""INSERT INTO recipe_version_ingredients(org_id,recipe_version_id,item_id,quantity_base,item_name,base_unit)
        SELECT org_id,:version_id,item_id,quantity_base,item_name,base_unit FROM recipe_ingredients
        WHERE org_id=:org AND recipe_id=:recipe"""),
        {"version_id": version_id, "org": org_id, "recipe": recipe_id},
    )


@router.get("/recipes/menu-links")
async def menu_recipe_costs(
    location_id: Annotated[uuid.UUID, Query()], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    await _location(ctx, db, location_id)
    rows = await db.execute(
        text("""SELECT m.id menu_item_id,m.name menu_item_name,m.recipe_id,r.name recipe_name,
      CASE WHEN r.id IS NULL THEN NULL WHEN bool_or(cost.last_cost IS NULL) THEN NULL ELSE round(sum(ri.quantity_base*cost.last_cost)/r.yield_quantity)::bigint END plate_cost_minor,
      (r.id IS NOT NULL AND bool_or(cost.last_cost IS NULL)) cost_incomplete
      FROM menu_items m LEFT JOIN recipes r ON r.id=m.recipe_id AND r.org_id=m.org_id
      LEFT JOIN recipe_ingredients ri ON ri.recipe_id=r.id AND ri.org_id=r.org_id
      LEFT JOIN LATERAL (SELECT rl.total_cost_minor/NULLIF(rl.received_base_quantity,0) last_cost FROM receipt_lines rl JOIN receipts rc ON rc.org_id=rl.org_id AND rc.id=rl.receipt_id WHERE rl.org_id=m.org_id AND rl.item_id=ri.item_id ORDER BY rc.received_at DESC LIMIT 1) cost ON true
      WHERE m.org_id=:org GROUP BY m.id,m.name,m.recipe_id,r.id,r.name,r.yield_quantity ORDER BY m.name"""),
        {"org": ctx.org_id, "loc": location_id},
    )
    return {"data": [_row(r) for r in rows]}


@router.get("/recipes/{recipe_id}")
async def recipe_detail(recipe_id: uuid.UUID, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    recipe = await db.execute(
        text("SELECT * FROM recipes WHERE id=:id AND org_id=:org"),
        {"id": recipe_id, "org": ctx.org_id},
    )
    if not (row := recipe.first()):
        raise not_found("Recipe not found")
    ingredients = await db.execute(
        text(
            "SELECT item_id,quantity_base,item_name,base_unit FROM recipe_ingredients WHERE org_id=:org AND recipe_id=:id ORDER BY item_name"
        ),
        {"org": ctx.org_id, "id": recipe_id},
    )
    return {**_row(row), "ingredients": [_row(ingredient) for ingredient in ingredients]}


@router.get("/menu-items")
async def inventory_menu_items(ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    rows = await db.execute(
        text(
            "SELECT id,name,category,active,recipe_id FROM menu_items WHERE org_id=:org ORDER BY name"
        ),
        {"org": ctx.org_id},
    )
    return {"data": [_row(row) for row in rows]}


@router.put("/menu-items/{menu_item_id}/recipe")
async def link_menu_recipe(
    menu_item_id: uuid.UUID, body: dict[str, str | None], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, _MANAGERS)
    recipe_id = uuid.UUID(body["recipe_id"]) if body.get("recipe_id") else None
    if (
        recipe_id is not None
        and await db.scalar(
            text("SELECT id FROM recipes WHERE id=:id AND org_id=:org AND active"),
            {"id": recipe_id, "org": ctx.org_id},
        )
        is None
    ):
        raise not_found("Recipe not found")
    result = await db.execute(
        text("UPDATE menu_items SET recipe_id=:recipe WHERE id=:id AND org_id=:org RETURNING id"),
        {"id": menu_item_id, "org": ctx.org_id, "recipe": recipe_id},
    )
    if result.first() is None:
        raise not_found("Menu item not found")
    await db.execute(
        text("""INSERT INTO menu_recipe_assignments(id,org_id,menu_item_id,recipe_id,created_by,effective_at)
        VALUES(:id,:org,:menu,:recipe,:actor,clock_timestamp())"""),
        {
            "id": uuid.uuid7(),
            "org": ctx.org_id,
            "menu": menu_item_id,
            "recipe": recipe_id,
            "actor": ctx.user_id,
        },
    )
    await audit.record(
        db,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
        entity_type="menu_item",
        entity_id=menu_item_id,
        action="recipe_link_updated",
        details={"recipe_id": str(recipe_id) if recipe_id else None},
    )
    return {"menu_item_id": str(menu_item_id), "recipe_id": str(recipe_id) if recipe_id else None}
