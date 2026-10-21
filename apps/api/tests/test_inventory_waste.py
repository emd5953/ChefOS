"""Waste posting covers conversion, audit, stock movement, replay, and tenant scope."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text


async def test_waste_conversion_audit_movement_and_idempotency(acct: dict, owner_engine) -> None:
    client, headers = acct["client"], acct["headers"]
    location_id = acct["location"]["id"]
    item = await client.post(
        "/v1/inventory/items",
        json={
            "name": "Waste flour",
            "dimension": "mass",
            "base_unit": "g",
            "conversions": {"kg": 1000},
        },
        headers=headers,
    )
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO inventory_levels(org_id,location_id,item_id,on_hand_base_quantity,version) VALUES(:org,:loc,:item,5000,1)"
            ),
            {"org": acct["org"]["id"], "loc": location_id, "item": item_id},
        )
    payload = {
        "location_id": location_id,
        "item_id": item_id,
        "quantity": 1.25,
        "unit": "kg",
        "reason": "Spoiled",
        "idempotency_key": str(uuid.uuid4()),
    }
    posted = await client.post("/v1/inventory/waste", json=payload, headers=headers)
    assert posted.status_code == 201, posted.text
    assert Decimal(posted.json()["quantity_base"]) == Decimal("1250")
    retry = await client.post("/v1/inventory/waste", json=payload, headers=headers)
    assert retry.status_code == 201 and retry.json()["id"] == posted.json()["id"]
    stock = await client.get("/v1/inventory/items", params={"location_id": location_id})
    assert Decimal(
        next(row for row in stock.json()["data"] if row["id"] == item_id)["on_hand_base_quantity"]
    ) == Decimal("3750")
    async with owner_engine.begin() as conn:
        movement = await conn.scalar(
            text(
                "SELECT count(*) FROM stock_movements WHERE source_id=:id AND movement_type='waste'"
            ),
            {"id": posted.json()["id"]},
        )
        audit_count = await conn.scalar(
            text(
                "SELECT count(*) FROM audit_events WHERE entity_id=:id AND entity_type='waste_entry'"
            ),
            {"id": posted.json()["id"]},
        )
    assert movement == audit_count == 1
    log = await client.get(
        "/v1/inventory/waste",
        params={
            "location_id": location_id,
            "from": posted.json()["business_date"],
            "to": posted.json()["business_date"],
        },
    )
    assert log.status_code == 200 and [row["id"] for row in log.json()["data"]] == [
        posted.json()["id"]
    ]


async def test_waste_rejects_insufficient_stock_and_cross_tenant_location(
    acct: dict, signup
) -> None:
    client, headers = acct["client"], acct["headers"]
    item = await client.post(
        "/v1/inventory/items",
        json={"name": "Empty stock", "dimension": "count", "base_unit": "ea"},
        headers=headers,
    )
    payload = {
        "location_id": acct["location"]["id"],
        "item_id": item.json()["id"],
        "quantity": 1,
        "reason": "Dropped",
        "idempotency_key": str(uuid.uuid4()),
    }
    insufficient = await client.post("/v1/inventory/waste", json=payload, headers=headers)
    assert insufficient.status_code == 422
    other = await signup()
    try:
        payload["location_id"] = other["location"]["id"]
        denied = await client.post("/v1/inventory/waste", json=payload, headers=headers)
        assert denied.status_code == 404
        hidden_log = await other["client"].get(
            "/v1/inventory/waste",
            params={
                "location_id": acct["location"]["id"],
                "from": "2026-01-01",
                "to": "2026-12-31",
            },
        )
        assert hidden_log.status_code == 404
    finally:
        await other["client"].aclose()


async def test_waste_validation_and_cross_tenant_item(acct: dict, signup) -> None:
    client, headers = acct["client"], acct["headers"]
    item = await client.post(
        "/v1/inventory/items",
        json={"name": "Waste oil", "dimension": "volume", "base_unit": "ml"},
        headers=headers,
    )
    payload = {
        "location_id": acct["location"]["id"],
        "item_id": item.json()["id"],
        "quantity": 1,
        "reason": "Spill",
        "idempotency_key": str(uuid.uuid4()),
    }
    assert (
        await client.post("/v1/inventory/waste", json={**payload, "quantity": 0}, headers=headers)
    ).status_code == 422
    assert (
        await client.post(
            "/v1/inventory/waste",
            json={k: v for k, v in payload.items() if k != "reason"},
            headers=headers,
        )
    ).status_code == 422
    assert (
        await client.post(
            "/v1/inventory/waste", json={**payload, "item_id": str(uuid.uuid4())}, headers=headers
        )
    ).status_code == 404
    assert (
        await client.post("/v1/inventory/waste", json={**payload, "unit": "kg"}, headers=headers)
    ).status_code == 422
    other = await signup()
    try:
        foreign_item = await other["client"].post(
            "/v1/inventory/items",
            json={"name": "Foreign oil", "dimension": "volume", "base_unit": "ml"},
            headers=other["headers"],
        )
        denied = await client.post(
            "/v1/inventory/waste",
            json={**payload, "item_id": foreign_item.json()["id"]},
            headers=headers,
        )
        assert denied.status_code == 404
    finally:
        await other["client"].aclose()


async def test_waste_base_unit_and_date_filtered_log(acct: dict, owner_engine) -> None:
    client, headers = acct["client"], acct["headers"]
    item = await client.post(
        "/v1/inventory/items",
        json={"name": "Waste cups", "dimension": "count", "base_unit": "ea"},
        headers=headers,
    )
    item_id, location_id = item.json()["id"], acct["location"]["id"]
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO inventory_levels(org_id,location_id,item_id,on_hand_base_quantity,version) VALUES(:org,:loc,:item,5,1)"
            ),
            {"org": acct["org"]["id"], "loc": location_id, "item": item_id},
        )
    created = await client.post(
        "/v1/inventory/waste",
        json={
            "location_id": location_id,
            "item_id": item_id,
            "quantity": 2,
            "reason": "Broken",
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=headers,
    )
    assert created.status_code == 201 and Decimal(created.json()["quantity_base"]) == 2
    day = date.fromisoformat(created.json()["business_date"])
    before = await client.get(
        "/v1/inventory/waste",
        params={
            "location_id": location_id,
            "from": day - timedelta(days=1),
            "to": day - timedelta(days=1),
        },
    )
    after = await client.get(
        "/v1/inventory/waste",
        params={
            "location_id": location_id,
            "from": day + timedelta(days=1),
            "to": day + timedelta(days=1),
        },
    )
    assert before.status_code == after.status_code == 200
    assert all(
        row["id"] != created.json()["id"] for row in before.json()["data"] + after.json()["data"]
    )
