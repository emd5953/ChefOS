"""Recipes, immutable snapshots, and menu-item recipe links and costs."""
# ruff: noqa: E501

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.errors import not_found, validation_error
from app.inventory import service
from app.inventory.schemas import (
    RecipeCreate,
    RecipeUpdate,
)


async def _snapshot_recipe(
    db: AsyncSession, org_id: uuid.UUID, recipe_id: uuid.UUID, actor_id: uuid.UUID
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


async def list_recipes(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    result = await db.execute(
        text("SELECT * FROM recipes WHERE org_id=:org ORDER BY name"), {"org": org_id}
    )
    return {"data": [service.as_dict(r) for r in result]}


async def create_recipe(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, body: RecipeCreate
) -> dict[str, Any]:
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
            "org": org_id,
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
            {"id": item_id, "org": org_id},
        )
        if not (i := item.first()):
            raise not_found("Active item not found")
        await db.execute(
            text(
                "INSERT INTO recipe_ingredients(id,org_id,recipe_id,item_id,quantity_base,item_name,base_unit) VALUES(:id,:org,:recipe,:item,:qty,:name,:unit)"
            ),
            {
                "id": uuid.uuid7(),
                "org": org_id,
                "recipe": recipe_id,
                "item": item_id,
                "qty": qty,
                "name": i.name,
                "unit": i.base_unit,
            },
        )
    await _snapshot_recipe(db, org_id, recipe_id, user_id)
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
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


async def update_recipe(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    recipe_id: uuid.UUID,
    body: RecipeUpdate,
) -> dict[str, Any]:
    current = await db.execute(
        text("SELECT * FROM recipes WHERE id=:id AND org_id=:org FOR UPDATE"),
        {"id": recipe_id, "org": org_id},
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
            "org": org_id,
        },
    )
    if body.ingredients is not None:
        await db.execute(
            text("DELETE FROM recipe_ingredients WHERE org_id=:org AND recipe_id=:id"),
            {"org": org_id, "id": recipe_id},
        )
        for ingredient in body.ingredients:
            raw = ingredient.model_dump()
            item_id = uuid.UUID(str(raw["item_id"]))
            item = await db.execute(
                text(
                    "SELECT name,base_unit FROM inventory_items WHERE id=:id AND org_id=:org AND active"
                ),
                {"id": item_id, "org": org_id},
            )
            if not (i := item.first()):
                raise not_found("Active item not found")
            await db.execute(
                text(
                    "INSERT INTO recipe_ingredients(id,org_id,recipe_id,item_id,quantity_base,item_name,base_unit) VALUES(:id,:org,:recipe,:item,:qty,:name,:unit)"
                ),
                {
                    "id": uuid.uuid7(),
                    "org": org_id,
                    "recipe": recipe_id,
                    "item": item_id,
                    "qty": Decimal(str(raw["quantity_base"])),
                    "name": i.name,
                    "unit": i.base_unit,
                },
            )
    await _snapshot_recipe(db, org_id, recipe_id, user_id)
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="recipe",
        entity_id=recipe_id,
        action="updated",
    )
    return service.as_dict(result.one())


async def menu_recipe_costs(
    db: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> dict[str, Any]:
    rows = await db.execute(
        text("""SELECT m.id menu_item_id,m.name menu_item_name,m.recipe_id,r.name recipe_name,
      CASE WHEN r.id IS NULL THEN NULL WHEN bool_or(cost.last_cost IS NULL) THEN NULL ELSE round(sum(ri.quantity_base*cost.last_cost)/r.yield_quantity)::bigint END plate_cost_minor,
      (r.id IS NOT NULL AND bool_or(cost.last_cost IS NULL)) cost_incomplete
      FROM menu_items m LEFT JOIN recipes r ON r.id=m.recipe_id AND r.org_id=m.org_id
      LEFT JOIN recipe_ingredients ri ON ri.recipe_id=r.id AND ri.org_id=r.org_id
      LEFT JOIN LATERAL (SELECT rl.total_cost_minor/NULLIF(rl.received_base_quantity,0) last_cost FROM receipt_lines rl JOIN receipts rc ON rc.org_id=rl.org_id AND rc.id=rl.receipt_id WHERE rl.org_id=m.org_id AND rl.item_id=ri.item_id ORDER BY rc.received_at DESC LIMIT 1) cost ON true
      WHERE m.org_id=:org GROUP BY m.id,m.name,m.recipe_id,r.id,r.name,r.yield_quantity ORDER BY m.name"""),
        {"org": org_id, "loc": location_id},
    )
    return {"data": [service.as_dict(r) for r in rows]}


async def recipe_detail(
    db: AsyncSession, org_id: uuid.UUID, recipe_id: uuid.UUID
) -> dict[str, Any]:
    recipe = await db.execute(
        text("SELECT * FROM recipes WHERE id=:id AND org_id=:org"),
        {"id": recipe_id, "org": org_id},
    )
    if not (row := recipe.first()):
        raise not_found("Recipe not found")
    ingredients = await db.execute(
        text(
            "SELECT item_id,quantity_base,item_name,base_unit FROM recipe_ingredients WHERE org_id=:org AND recipe_id=:id ORDER BY item_name"
        ),
        {"org": org_id, "id": recipe_id},
    )
    return {
        **service.as_dict(row),
        "ingredients": [service.as_dict(ingredient) for ingredient in ingredients],
    }


async def list_menu_items(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    rows = await db.execute(
        text(
            "SELECT id,name,category,active,recipe_id FROM menu_items WHERE org_id=:org ORDER BY name"
        ),
        {"org": org_id},
    )
    return {"data": [service.as_dict(row) for row in rows]}


async def link_menu_recipe(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    menu_item_id: uuid.UUID,
    body: dict[str, str | None],
) -> dict[str, Any]:
    recipe_id = uuid.UUID(body["recipe_id"]) if body.get("recipe_id") else None
    if (
        recipe_id is not None
        and await db.scalar(
            text("SELECT id FROM recipes WHERE id=:id AND org_id=:org AND active"),
            {"id": recipe_id, "org": org_id},
        )
        is None
    ):
        raise not_found("Recipe not found")
    result = await db.execute(
        text("UPDATE menu_items SET recipe_id=:recipe WHERE id=:id AND org_id=:org RETURNING id"),
        {"id": menu_item_id, "org": org_id, "recipe": recipe_id},
    )
    if result.first() is None:
        raise not_found("Menu item not found")
    await db.execute(
        text("""INSERT INTO menu_recipe_assignments(id,org_id,menu_item_id,recipe_id,created_by,effective_at)
        VALUES(:id,:org,:menu,:recipe,:actor,clock_timestamp())"""),
        {
            "id": uuid.uuid7(),
            "org": org_id,
            "menu": menu_item_id,
            "recipe": recipe_id,
            "actor": user_id,
        },
    )
    await audit.record(
        db,
        org_id=org_id,
        actor_user_id=user_id,
        entity_type="menu_item",
        entity_id=menu_item_id,
        action="recipe_link_updated",
        details={"recipe_id": str(recipe_id) if recipe_id else None},
    )
    return {"menu_item_id": str(menu_item_id), "recipe_id": str(recipe_id) if recipe_id else None}
