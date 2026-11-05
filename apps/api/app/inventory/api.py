"""Inventory catalog and stock endpoints."""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from app.identity.models import Location
from app.inventory import counts, purchasing, service, waste
from app.inventory import recipes as recipes_service
from app.inventory.food_cost import build_variance
from app.inventory.food_cost_schemas import FoodCostVarianceIn, FoodCostVarianceOut
from app.inventory.schemas import (
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


@router.post("/food-cost-variance", response_model=FoodCostVarianceOut)
async def food_cost_variance(
    body: FoodCostVarianceIn, ctx: AuthDep, db: OrgSession
) -> FoodCostVarianceOut:
    """Count-anchored operational variance. This is not accounting COGS."""
    require_role(ctx, service.MANAGERS)
    location = await _location(ctx, db, body.location_id)
    return await build_variance(db, ctx.org_id, location, body)


async def _location(ctx: AuthDep, db: OrgSession, location_id: uuid.UUID) -> Location:
    return await service.authorized_location(
        db, await ctx.allowed_location_ids(include_archived=False), location_id
    )


@router.get("/items")
async def items(
    ctx: AuthDep, db: OrgSession, location_id: Annotated[uuid.UUID, Query()]
) -> dict[str, list[dict[str, Any]]]:
    await _location(ctx, db, location_id)
    return await service.list_items(db, ctx.org_id, location_id)


@router.post("/waste", status_code=201)
async def create_waste(body: WasteCreate, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    location = await _location(ctx, db, body.location_id)
    return await waste.create_waste(db, ctx.org_id, ctx.user_id, location, body)


@router.get("/waste")
async def waste_log(
    ctx: AuthDep,
    db: OrgSession,
    location_id: Annotated[uuid.UUID, Query()],
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
) -> dict[str, list[dict[str, Any]]]:
    await _location(ctx, db, location_id)
    return await waste.list_log(db, ctx.org_id, location_id, from_date, to_date)


@router.post("/items", status_code=status.HTTP_201_CREATED)
async def create_item(body: ItemCreate, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    return await service.create_item(db, ctx.org_id, ctx.user_id, body)


@router.patch("/items/{item_id}")
async def update_item(
    item_id: uuid.UUID, body: ItemUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    return await service.update_item(db, ctx.org_id, ctx.user_id, item_id, body)


@router.put("/items/{item_id}/conversions")
async def replace_conversions(
    item_id: uuid.UUID, body: ConversionUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    return await service.replace_conversions(db, ctx.org_id, ctx.user_id, item_id, body)


@router.post("/suppliers", status_code=201)
async def create_supplier(body: SupplierCreate, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    return await service.create_supplier(db, ctx.org_id, ctx.user_id, body)


@router.get("/suppliers")
async def suppliers(ctx: AuthDep, db: OrgSession) -> dict[str, list[dict[str, Any]]]:
    return await service.list_suppliers(db, ctx.org_id)


@router.patch("/suppliers/{supplier_id}")
async def update_supplier(
    supplier_id: uuid.UUID, body: SupplierUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    return await service.update_supplier(db, ctx.org_id, ctx.user_id, supplier_id, body)


@router.post("/storage-areas", status_code=201)
async def create_storage_area(
    body: StorageAreaCreate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    await _location(ctx, db, body.location_id)
    return await service.create_storage_area(db, ctx.org_id, ctx.user_id, body)


@router.get("/storage-areas")
async def storage_areas(
    ctx: AuthDep, db: OrgSession, location_id: Annotated[uuid.UUID, Query()]
) -> dict[str, list[dict[str, Any]]]:
    await _location(ctx, db, location_id)
    return await service.list_storage_areas(db, ctx.org_id, location_id)


@router.patch("/storage-areas/{area_id}")
async def update_storage_area(
    area_id: uuid.UUID, body: StorageAreaUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    allowed = await ctx.allowed_location_ids(include_archived=False)
    return await service.update_storage_area(db, ctx.org_id, ctx.user_id, allowed, area_id, body)


@router.put("/items/{item_id}/par")
async def set_par(
    item_id: uuid.UUID, body: ParUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    await _location(ctx, db, body.location_id)
    return await service.set_par(db, ctx.org_id, ctx.user_id, item_id, body)


@router.post("/items/{item_id}/packs", status_code=201)
async def create_pack(
    item_id: uuid.UUID, body: PackCreate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    return await service.create_pack(db, ctx.org_id, ctx.user_id, item_id, body)


@router.put("/items/{item_id}/storage-area")
async def assign_storage_area(
    item_id: uuid.UUID,
    location_id: Annotated[uuid.UUID, Query()],
    storage_area_id: Annotated[uuid.UUID, Query()],
    ctx: AuthDep,
    db: OrgSession,
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    await _location(ctx, db, location_id)
    return await service.assign_storage_area(
        db, ctx.org_id, ctx.user_id, item_id, location_id, storage_area_id
    )


@router.get("/items/{item_id}/packs")
async def item_packs(item_id: uuid.UUID, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    return await service.list_item_packs(db, ctx.org_id, item_id)


@router.get("/packs")
async def packs(ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    return await service.list_packs(db, ctx.org_id)


@router.patch("/items/{item_id}/packs/{pack_id}")
async def update_pack(
    item_id: uuid.UUID, pack_id: uuid.UUID, body: PackUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    return await service.update_pack(db, ctx.org_id, ctx.user_id, item_id, pack_id, body)


@router.get("/count-sessions")
async def count_sessions(
    location_id: Annotated[uuid.UUID, Query()], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    await _location(ctx, db, location_id)
    return await counts.list_sessions(db, ctx.org_id, location_id)


@router.post("/count-sessions", status_code=201)
async def start_count(body: CountStart, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    await _location(ctx, db, body.location_id)
    return await counts.start_count(db, ctx.org_id, ctx.user_id, body)


@router.get("/count-sessions/{session_id}")
async def count_detail(session_id: uuid.UUID, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    allowed = await ctx.allowed_location_ids(include_archived=False)
    return await counts.session_detail(db, ctx.org_id, allowed, session_id)


@router.put("/count-sessions/{session_id}/lines/{item_id}")
async def save_count_line(
    session_id: uuid.UUID, item_id: uuid.UUID, body: CountEntry, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    allowed = await ctx.allowed_location_ids(include_archived=False)
    return await counts.save_line(
        db, ctx.org_id, ctx.user_id, ctx.role, allowed, session_id, item_id, body
    )


@router.post("/count-sessions/{session_id}/lines/{item_id}/confirm")
async def confirm_count_exception(
    session_id: uuid.UUID, item_id: uuid.UUID, body: dict[str, str], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    allowed = await ctx.allowed_location_ids(include_archived=False)
    return await counts.confirm_exception(
        db, ctx.org_id, ctx.user_id, allowed, session_id, item_id, body
    )


@router.post("/count-sessions/{session_id}/submit")
async def submit_count(
    session_id: uuid.UUID, body: CountSubmit, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    allowed = await ctx.allowed_location_ids(include_archived=False)
    return await counts.submit_count(db, ctx.org_id, ctx.user_id, allowed, session_id)


@router.get("/purchase-orders")
async def purchase_orders(
    location_id: Annotated[uuid.UUID, Query()], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    await _location(ctx, db, location_id)
    return await purchasing.list_orders(db, ctx.org_id, location_id)


@router.patch("/purchase-orders/{po_id}")
async def update_purchase_order(
    po_id: uuid.UUID, body: POUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    allowed = await ctx.allowed_location_ids(include_archived=False)
    return await purchasing.update_order(db, ctx.org_id, ctx.user_id, allowed, po_id, body)


@router.post("/purchase-orders", status_code=201)
async def create_purchase_order(body: POCreate, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    await _location(ctx, db, body.location_id)
    return await purchasing.create_order(db, ctx.org_id, ctx.user_id, body)


@router.get("/purchase-orders/{po_id}")
async def purchase_order_detail(po_id: uuid.UUID, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    allowed = await ctx.allowed_location_ids(include_archived=False)
    return await purchasing.order_detail(db, ctx.org_id, allowed, po_id)


@router.post("/purchase-orders/{po_id}/close")
async def close_purchase_order(
    po_id: uuid.UUID, body: dict[str, str], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    allowed = await ctx.allowed_location_ids(include_archived=False)
    return await purchasing.close_order(db, ctx.org_id, ctx.user_id, allowed, po_id, body)


@router.post("/purchase-orders/{po_id}/reopen")
async def reopen_purchase_order(
    po_id: uuid.UUID, body: dict[str, str], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    allowed = await ctx.allowed_location_ids(include_archived=False)
    return await purchasing.reopen_order(db, ctx.org_id, ctx.user_id, allowed, po_id, body)


@router.post("/purchase-orders/{po_id}/receive", status_code=201)
async def receive_purchase_order(
    po_id: uuid.UUID, body: ReceiveRequest, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    allowed = await ctx.allowed_location_ids(include_archived=False)
    return await purchasing.receive_order(
        db, ctx.org_id, ctx.user_id, ctx.role, allowed, po_id, body
    )


@router.get("/recipes")
async def recipes(ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    return await recipes_service.list_recipes(db, ctx.org_id)


@router.post("/recipes", status_code=201)
async def create_recipe(body: RecipeCreate, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    return await recipes_service.create_recipe(db, ctx.org_id, ctx.user_id, body)


@router.patch("/recipes/{recipe_id}")
async def update_recipe(
    recipe_id: uuid.UUID, body: RecipeUpdate, ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    return await recipes_service.update_recipe(db, ctx.org_id, ctx.user_id, recipe_id, body)


@router.get("/recipes/menu-links")
async def menu_recipe_costs(
    location_id: Annotated[uuid.UUID, Query()], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    await _location(ctx, db, location_id)
    return await recipes_service.menu_recipe_costs(db, ctx.org_id, location_id)


@router.get("/recipes/{recipe_id}")
async def recipe_detail(recipe_id: uuid.UUID, ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    return await recipes_service.recipe_detail(db, ctx.org_id, recipe_id)


@router.get("/menu-items")
async def inventory_menu_items(ctx: AuthDep, db: OrgSession) -> dict[str, Any]:
    return await recipes_service.list_menu_items(db, ctx.org_id)


@router.put("/menu-items/{menu_item_id}/recipe")
async def link_menu_recipe(
    menu_item_id: uuid.UUID, body: dict[str, str | None], ctx: AuthDep, db: OrgSession
) -> dict[str, Any]:
    require_role(ctx, service.MANAGERS)
    return await recipes_service.link_menu_recipe(db, ctx.org_id, ctx.user_id, menu_item_id, body)
