"""Catalog endpoints validate base units, scope, and audited writes."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_catalog_item_supplier_pack_and_par(acct: dict) -> None:
    client = acct["client"]
    headers = acct["headers"]
    supplier = await client.post(
        "/v1/inventory/suppliers", json={"name": "North Foods"}, headers=headers
    )
    assert supplier.status_code == 201, supplier.text
    supplier_id = supplier.json()["id"]

    item = await client.post(
        "/v1/inventory/items",
        json={
            "name": "Tomatoes",
            "category": "Produce",
            "base_unit": "g",
            "dimension": "mass",
            "default_supplier_id": supplier_id,
            "conversions": {"kg": 1000, "lb": 453.59237},
        },
        headers=headers,
    )
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]
    assert item.json()["base_unit"] == "g"

    pack = await client.post(
        f"/v1/inventory/items/{item_id}/packs",
        json={
            "item_id": item_id,
            "supplier_id": supplier_id,
            "name": "10 lb case",
            "purchase_unit": "case",
            "base_quantity": 4535.9237,
            "last_price_minor": 2400,
        },
        headers=headers,
    )
    assert pack.status_code == 201, pack.text

    location_id = acct["location"]["id"]
    area = await client.post(
        "/v1/inventory/storage-areas",
        json={"location_id": location_id, "name": "Walk-in"},
        headers=headers,
    )
    assert area.status_code == 201, area.text
    assignment = await client.put(
        f"/v1/inventory/items/{item_id}/storage-area",
        params={"location_id": location_id, "storage_area_id": area.json()["id"]},
        headers=headers,
    )
    assert assignment.status_code == 200, assignment.text
    par = await client.put(
        f"/v1/inventory/items/{item_id}/par",
        json={"location_id": location_id, "par_base_quantity": 5000},
        headers=headers,
    )
    assert par.status_code == 200, par.text
    stock = await client.get("/v1/inventory/items", params={"location_id": location_id})
    assert stock.status_code == 200
    assert stock.json()["data"][0]["below_par"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Water", "base_unit": "g", "dimension": "volume"},
        {"name": "Water", "base_unit": "ml", "dimension": "volume", "conversions": {"lb": 2}},
        {"name": "Flour", "base_unit": "g", "dimension": "mass", "conversions": {"kg": 0}},
    ],
)
async def test_item_rejects_incompatible_units(acct: dict, payload: dict) -> None:
    response = await acct["client"].post(
        "/v1/inventory/items", json=payload, headers=acct["headers"]
    )
    assert response.status_code == 422


async def test_catalog_rejects_supplier_from_another_org(acct: dict) -> None:
    response = await acct["client"].post(
        "/v1/inventory/items",
        json={
            "name": "Salt",
            "base_unit": "g",
            "dimension": "mass",
            "default_supplier_id": str(uuid.uuid4()),
        },
        headers=acct["headers"],
    )
    assert response.status_code == 404


async def test_recipe_and_menu_mapping_history_is_append_only(
    acct: dict, owner_engine: AsyncEngine
) -> None:
    client, headers = acct["client"], acct["headers"]
    item = await client.post(
        "/v1/inventory/items",
        json={"name": "History Flour", "base_unit": "g", "dimension": "mass"},
        headers=headers,
    )
    assert item.status_code == 201, item.text
    recipe = await client.post(
        "/v1/inventory/recipes",
        json={
            "name": "Bread",
            "yield_quantity": 1,
            "yield_unit": "portion",
            "ingredients": [{"item_id": item.json()["id"], "quantity_base": 300}],
        },
        headers=headers,
    )
    assert recipe.status_code == 201, recipe.text
    recipe_id = recipe.json()["id"]
    updated = await client.patch(
        f"/v1/inventory/recipes/{recipe_id}",
        json={
            "yield_quantity": 2,
            "ingredients": [{"item_id": item.json()["id"], "quantity_base": 500}],
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text

    menu_id = uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO menu_items(id,org_id,name) VALUES(:id,:org,'History Menu Item')"),
            {"id": menu_id, "org": uuid.UUID(acct["org"]["id"])},
        )
    linked = await client.put(
        f"/v1/commerce/menu-items/{menu_id}/recipe",
        json={"recipe_id": recipe_id},
        headers=headers,
    )
    assert linked.status_code == 200, linked.text
    unlinked = await client.put(
        f"/v1/commerce/menu-items/{menu_id}/recipe", json={"recipe_id": None}, headers=headers
    )
    assert unlinked.status_code == 200, unlinked.text

    async with owner_engine.connect() as conn:
        versions = (
            await conn.execute(
                text(
                    "SELECT version,yield_quantity FROM recipe_versions WHERE recipe_id=:id ORDER BY version"
                ),
                {"id": recipe_id},
            )
        ).all()
        ingredients = await conn.scalar(
            text(
                "SELECT quantity_base FROM recipe_version_ingredients WHERE recipe_version_id=(SELECT id FROM recipe_versions WHERE recipe_id=:id ORDER BY version DESC LIMIT 1)"
            ),
            {"id": recipe_id},
        )
        mappings = (
            await conn.execute(
                text(
                    "SELECT recipe_id FROM menu_recipe_assignments WHERE menu_item_id=:id ORDER BY effective_at,id"
                ),
                {"id": menu_id},
            )
        ).all()
    assert [(v.version, float(v.yield_quantity)) for v in versions] == [(1, 1.0), (2, 2.0)]
    assert float(ingredients) == 500
    assert [r.recipe_id for r in mappings] == [uuid.UUID(recipe_id), None]
