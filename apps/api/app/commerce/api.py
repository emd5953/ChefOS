"""Commerce read API (docs/api.md, Commerce section)."""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.audit import service as audit
from app.commerce import service
from app.commerce.schemas import DailyOut, ItemsOut, SummaryOut, TodayOut
from app.errors import not_found
from app.identity.models import Location
from app.security.sessions import OrgSession, StaffDep, require_role

router = APIRouter(prefix="/v1/commerce", tags=["commerce"])

LocationId = Annotated[uuid.UUID, Query()]
From = Annotated[date, Query(alias="from")]
To = Annotated[date, Query(alias="to")]


async def _location(ctx: StaffDep, db: OrgSession, location_id: uuid.UUID) -> Location:
    return await service.get_location(db, await ctx.allowed_location_ids(), location_id)


@router.get("/daily", response_model=DailyOut)
async def daily(
    ctx: StaffDep, db: OrgSession, location_id: LocationId, from_: From, to: To
) -> DailyOut:
    loc = await _location(ctx, db, location_id)
    return await service.daily(db, ctx.org_id, loc, from_, to)


@router.get("/summary", response_model=SummaryOut)
async def summary(
    ctx: StaffDep,
    db: OrgSession,
    location_id: LocationId,
    from_: From,
    to: To,
    compare: Literal["previous_period", "previous_year"] = "previous_period",
) -> SummaryOut:
    loc = await _location(ctx, db, location_id)
    return await service.summary(db, ctx.org_id, loc, from_, to, compare)


@router.get("/items", response_model=ItemsOut)
async def items(
    ctx: StaffDep,
    db: OrgSession,
    location_id: LocationId,
    from_: From,
    to: To,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> ItemsOut:
    loc = await _location(ctx, db, location_id)
    return await service.items(db, ctx.org_id, loc, from_, to, limit)


@router.get("/today", response_model=TodayOut)
async def today(ctx: StaffDep, db: OrgSession, location_id: LocationId) -> TodayOut:
    loc = await _location(ctx, db, location_id)
    return await service.today(db, ctx.org_id, loc)


@router.put("/menu-items/{menu_item_id}/recipe")
async def set_menu_item_recipe(
    menu_item_id: uuid.UUID, body: dict[str, str | None], ctx: StaffDep, db: OrgSession
) -> dict[str, str | None]:
    """Link a canonical menu item to an org-owned inventory recipe."""
    require_role(ctx, {"owner", "admin", "manager"})
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
