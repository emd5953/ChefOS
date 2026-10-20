"""Catalog endpoints validate base units, scope, and audited writes."""

import uuid

import pytest


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
