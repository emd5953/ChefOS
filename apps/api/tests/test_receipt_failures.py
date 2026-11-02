"""Receipt retries must describe the committed operation, including concurrent deliveries."""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def order(acct: dict) -> tuple[str, dict]:
    client, headers = acct["client"], acct["headers"]
    supplier = await client.post(
        "/v1/inventory/suppliers", json={"name": "Supplier"}, headers=headers
    )
    item = await client.post(
        "/v1/inventory/items",
        json={"name": "Rice", "base_unit": "g", "dimension": "mass"},
        headers=headers,
    )
    pack = await client.post(
        f"/v1/inventory/items/{item.json()['id']}/packs",
        json={
            "item_id": item.json()["id"],
            "supplier_id": supplier.json()["id"],
            "name": "Bag",
            "purchase_unit": "bag",
            "base_quantity": 1000,
        },
        headers=headers,
    )
    po = await client.post(
        "/v1/inventory/purchase-orders",
        json={
            "location_id": acct["location"]["id"],
            "supplier_id": supplier.json()["id"],
            "lines": [{"pack_id": pack.json()["id"], "ordered_packs": 1, "unit_price_minor": 500}],
        },
        headers=headers,
    )
    assert po.status_code == 201, po.text
    url = f"/v1/inventory/purchase-orders/{po.json()['id']}"
    detail = (await client.get(url)).json()
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "lines": [
            {
                "line_id": detail["lines"][0]["id"],
                "received_base_quantity": 1000,
                "total_cost_minor": 500,
            }
        ],
    }
    return url, payload


async def test_concurrent_full_receipt_retries_return_one_posting(
    acct: dict, owner_engine: AsyncEngine
) -> None:
    url, payload = await order(acct)
    client, headers = acct["client"], acct["headers"]
    responses = await asyncio.gather(
        *[client.post(f"{url}/receive", json=payload, headers=headers) for _ in range(3)]
    )
    assert [r.status_code for r in responses] == [201, 201, 201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert (await client.get(url)).json()["status"] == "closed"
    # A lost response can be retried after the order closed, but a new receipt cannot.
    retry = await client.post(f"{url}/receive", json=payload, headers=headers)
    assert retry.json()["id"] == responses[0].json()["id"]
    payload["idempotency_key"] = str(uuid.uuid4())
    assert (await client.post(f"{url}/receive", json=payload, headers=headers)).status_code == 409
    async with owner_engine.connect() as db:
        count = await db.scalar(
            text("SELECT count(*) FROM stock_movements WHERE source_id=:id"),
            {"id": uuid.UUID(retry.json()["id"])},
        )
    assert count == 1


@pytest.mark.parametrize("change", ["quantity", "cost", "approval"])
async def test_receipt_key_cannot_claim_a_changed_request_succeeded(
    acct: dict, change: str
) -> None:
    url, payload = await order(acct)
    client, headers = acct["client"], acct["headers"]
    posted = await client.post(f"{url}/receive", json=payload, headers=headers)
    assert posted.status_code == 201
    line = payload["lines"][0]
    if change == "quantity":
        line["received_base_quantity"] = 999
    elif change == "cost":
        line["total_cost_minor"] = 501
    else:
        line.update(accept_overage=True, reason="A different approval")
    response = await client.post(f"{url}/receive", json=payload, headers=headers)
    assert response.status_code == 409 and "different request" in response.text


async def test_later_line_failure_rolls_back_receipt_and_all_stock(acct: dict) -> None:
    url, payload = await order(acct)
    client, headers = acct["client"], acct["headers"]
    payload["lines"].append(
        {"line_id": str(uuid.uuid4()), "received_base_quantity": 1, "total_cost_minor": 1}
    )
    failed = await client.post(f"{url}/receive", json=payload, headers=headers)
    assert failed.status_code == 404
    unchanged = (await client.get(url)).json()
    assert unchanged["status"] == "open" and float(unchanged["lines"][0]["received_quantity"]) == 0
    payload["lines"].pop()
    retry = await client.post(f"{url}/receive", json=payload, headers=headers)
    assert retry.status_code == 201
    stock = (
        await client.get("/v1/inventory/items", params={"location_id": acct["location"]["id"]})
    ).json()
    assert float(stock["data"][0]["on_hand_base_quantity"]) == 1000
