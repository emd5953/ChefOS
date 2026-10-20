"""End-to-end inventory count and receiving transactions through the public API."""

import uuid
from decimal import Decimal


async def test_count_concurrency_and_receipt_idempotency(acct: dict) -> None:
    client = acct["client"]
    headers = acct["headers"]
    location_id = acct["location"]["id"]

    supplier = await client.post(
        "/v1/inventory/suppliers",
        json={"name": f"Supplier {uuid.uuid4().hex[:8]}"},
        headers=headers,
    )
    assert supplier.status_code == 201, supplier.text
    item = await client.post(
        "/v1/inventory/items",
        json={
            "name": f"Workflow item {uuid.uuid4().hex[:8]}",
            "base_unit": "ea",
            "dimension": "count",
            "default_supplier_id": supplier.json()["id"],
        },
        headers=headers,
    )
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]
    area = await client.post(
        "/v1/inventory/storage-areas",
        json={"location_id": location_id, "name": "Count test area"},
        headers=headers,
    )
    assert area.status_code == 201, area.text
    area_id = area.json()["id"]
    assignment = await client.put(
        f"/v1/inventory/items/{item_id}/storage-area",
        params={"location_id": location_id, "storage_area_id": area_id},
        headers=headers,
    )
    assert assignment.status_code == 200, assignment.text

    sessions = []
    for _ in range(2):
        response = await client.post(
            "/v1/inventory/count-sessions",
            json={"location_id": location_id, "storage_area_id": area_id},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        session = await client.get(
            f"/v1/inventory/count-sessions/{response.json()['id']}", headers=headers
        )
        sessions.append(session.json())
    for session, quantity in zip(sessions, (10, 11), strict=True):
        saved = await client.put(
            f"/v1/inventory/count-sessions/{session['id']}/lines/{item_id}",
            json={"quantity": quantity, "unit": "ea"},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        if saved.json()["exception_state"] == "review_required":
            confirmed = await client.post(
                f"/v1/inventory/count-sessions/{session['id']}/lines/{item_id}/confirm",
                json={"note": "Verified the shelf count"},
                headers=headers,
            )
            assert confirmed.status_code == 200, confirmed.text
    submitted = await client.post(
        f"/v1/inventory/count-sessions/{sessions[0]['id']}/submit", json={}, headers=headers
    )
    assert submitted.status_code == 200, submitted.text
    stale = await client.post(
        f"/v1/inventory/count-sessions/{sessions[1]['id']}/submit", json={}, headers=headers
    )
    assert stale.status_code == 409, stale.text

    pack = await client.post(
        f"/v1/inventory/items/{item_id}/packs",
        json={
            "item_id": item_id,
            "supplier_id": supplier.json()["id"],
            "name": "Case of 12",
            "purchase_unit": "case",
            "base_quantity": 12,
            "last_price_minor": 2400,
        },
        headers=headers,
    )
    assert pack.status_code == 201, pack.text
    order = await client.post(
        "/v1/inventory/purchase-orders",
        json={
            "location_id": location_id,
            "supplier_id": supplier.json()["id"],
            "lines": [{"pack_id": pack.json()["id"], "ordered_packs": 2, "unit_price_minor": 2400}],
        },
        headers=headers,
    )
    assert order.status_code == 201, order.text
    detail = await client.get(
        f"/v1/inventory/purchase-orders/{order.json()['id']}", headers=headers
    )
    line_id = detail.json()["lines"][0]["id"]
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "lines": [{"line_id": line_id, "received_base_quantity": 12, "total_cost_minor": 2400}],
    }
    receipt = await client.post(
        f"/v1/inventory/purchase-orders/{order.json()['id']}/receive", json=payload, headers=headers
    )
    assert receipt.status_code == 201, receipt.text
    retry = await client.post(
        f"/v1/inventory/purchase-orders/{order.json()['id']}/receive", json=payload, headers=headers
    )
    assert retry.status_code == 201, retry.text
    assert retry.json()["id"] == receipt.json()["id"]
    closed = await client.post(
        f"/v1/inventory/purchase-orders/{order.json()['id']}/close",
        json={"reason": "Supplier delivery is complete"},
        headers=headers,
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    reopened = await client.post(
        f"/v1/inventory/purchase-orders/{order.json()['id']}/reopen",
        json={"reason": "A second delivery is expected"},
        headers=headers,
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "partially_received"
    stock = await client.get(
        "/v1/inventory/items", params={"location_id": location_id}, headers=headers
    )
    stock_item = next(entry for entry in stock.json()["data"] if entry["id"] == item_id)
    assert Decimal(str(stock_item["on_hand_base_quantity"])) == Decimal("22")
