"""Pure formula and coverage tests for food cost variance."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.inventory.food_cost import _theoretical_usage


def test_sales_line_quantity_is_counted_once_for_multi_ingredient_recipe() -> None:
    common = {
        "id": uuid.uuid4(),
        "quantity": Decimal("2"),
        "closed_at": datetime(2026, 10, 20, 22, tzinfo=UTC),
        "menu_item_variant_id": uuid.uuid4(),
        "menu_item_id": uuid.uuid4(),
        "recipe_id": uuid.uuid4(),
        "recipe_version_id": uuid.uuid4(),
        "yield_quantity": Decimal("1"),
        "recipe_active": True,
        "ingredient_active": True,
    }
    flour, salt = uuid.uuid4(), uuid.uuid4()
    rows = [
        SimpleNamespace(**common, ingredient_id=flour, ingredient_quantity=Decimal("100")),
        SimpleNamespace(**common, ingredient_id=salt, ingredient_quantity=Decimal("20")),
    ]
    used, excluded, count, gross, mapped = _theoretical_usage(
        rows, datetime(2026, 10, 1, tzinfo=UTC), False
    )
    assert count == 1
    assert gross == Decimal("2")
    assert mapped == Decimal("2")
    assert used == {flour: Decimal("200"), salt: Decimal("40")}
    assert excluded == {}


def test_unmapped_sales_line_is_excluded_once_instead_of_zero_usage() -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        quantity=Decimal("3"),
        closed_at=datetime(2026, 10, 20, 22, tzinfo=UTC),
        menu_item_variant_id=None,
        menu_item_id=None,
        recipe_id=None,
        recipe_version_id=None,
        yield_quantity=None,
        recipe_active=None,
        ingredient_id=None,
        ingredient_quantity=None,
        ingredient_active=None,
    )
    used, excluded, count, gross, mapped = _theoretical_usage(
        [row], datetime(2026, 10, 1, tzinfo=UTC), False
    )
    assert count == 1
    assert gross == Decimal("3")
    assert mapped == Decimal("0")
    assert not used
    assert excluded == {"unmapped_sales_line": 1}


async def test_food_cost_endpoint_uses_submitted_count_anchors(
    acct: dict, owner_engine: AsyncEngine
) -> None:
    client, headers = acct["client"], acct["headers"]
    location_id = uuid.UUID(acct["location"]["id"])
    org_id = uuid.UUID(acct["org"]["id"])
    user_id = uuid.UUID(acct["user"]["id"])
    item = await client.post(
        "/v1/inventory/items",
        json={"name": "Variance Potatoes", "base_unit": "g", "dimension": "mass"},
        headers=headers,
    )
    assert item.status_code == 201, item.text
    item_id = uuid.UUID(item.json()["id"])
    area = await client.post(
        "/v1/inventory/storage-areas",
        json={"location_id": str(location_id), "name": "Variance walk-in"},
        headers=headers,
    )
    assert area.status_code == 201, area.text
    area_id = uuid.UUID(area.json()["id"])
    assigned = await client.put(
        f"/v1/inventory/items/{item_id}/storage-area",
        params={"location_id": str(location_id), "storage_area_id": str(area_id)},
        headers=headers,
    )
    assert assigned.status_code == 200, assigned.text
    async with owner_engine.connect() as conn:
        baseline = await conn.scalar(
            text("SELECT effective_at FROM food_cost_baselines WHERE org_id=:org"),
            {"org": org_id},
        )
    assert baseline is not None

    zone = ZoneInfo("America/New_York")
    local_today = datetime.now(UTC).astimezone(zone).date()
    open_at = datetime.combine(local_today - timedelta(days=2), time(23), zone).astimezone(UTC)
    close_at = datetime.combine(local_today - timedelta(days=1), time(23), zone).astimezone(UTC)
    count_ids = [uuid.uuid4(), uuid.uuid4()]
    quantities = [Decimal("1000"), Decimal("900")]
    async with owner_engine.begin() as conn:
        for count_id, submitted_at, quantity in zip(
            count_ids, (open_at, close_at), quantities, strict=True
        ):
            await conn.execute(
                text("""INSERT INTO count_sessions(id,org_id,location_id,storage_area_id,status,
                    started_by,submitted_by,started_at,submitted_at) VALUES(:id,:org,:loc,:area,
                    'submitted',:user,:user,:submitted,:submitted)"""),
                {
                    "id": count_id,
                    "org": org_id,
                    "loc": location_id,
                    "area": area_id,
                    "user": user_id,
                    "submitted": submitted_at,
                },
            )
            await conn.execute(
                text("""INSERT INTO count_lines(id,org_id,location_id,session_id,item_id,
                    expected_base_quantity,expected_level_version,counted_base_quantity,
                    item_name,base_unit) VALUES(:line,:org,:loc,:session,:item,0,0,:qty,
                    'Variance Potatoes','g')"""),
                {
                    "line": uuid.uuid4(),
                    "org": org_id,
                    "loc": location_id,
                    "session": count_id,
                    "item": item_id,
                    "qty": quantity,
                },
            )

    response = await client.post(
        "/v1/inventory/food-cost-variance",
        json={
            "location_id": str(location_id),
            "opening_count_session_ids": [str(count_ids[0])],
            "closing_count_session_ids": [str(count_ids[1])],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "partial"
    assert report["period_from"] == (local_today - timedelta(days=1)).isoformat()
    assert report["period_to"] == (local_today - timedelta(days=1)).isoformat()
    assert report["items"][0]["actual_depletion_quantity"] == "100.000000"
    assert report["items"][0]["actual_cost_minor"] is None
    assert report["actual_cost_minor"] is None


async def test_complete_period_golden_variance(acct: dict, owner_engine: AsyncEngine) -> None:
    """Opening 1000g + 100g received - closing 850g = 250g actual; 2 plates use 200g."""
    client, headers = acct["client"], acct["headers"]
    location_id = uuid.UUID(acct["location"]["id"])
    org_id = uuid.UUID(acct["org"]["id"])
    user_id = uuid.UUID(acct["user"]["id"])

    supplier = await client.post(
        "/v1/inventory/suppliers", json={"name": "Golden Supplier"}, headers=headers
    )
    assert supplier.status_code == 201, supplier.text
    item = await client.post(
        "/v1/inventory/items",
        json={
            "name": "Golden Potatoes",
            "base_unit": "g",
            "dimension": "mass",
            "default_supplier_id": supplier.json()["id"],
        },
        headers=headers,
    )
    assert item.status_code == 201, item.text
    item_id = uuid.UUID(item.json()["id"])
    area = await client.post(
        "/v1/inventory/storage-areas",
        json={"location_id": str(location_id), "name": "Golden walk-in"},
        headers=headers,
    )
    assert area.status_code == 201, area.text
    area_id = uuid.UUID(area.json()["id"])
    assigned = await client.put(
        f"/v1/inventory/items/{item_id}/storage-area",
        params={"location_id": str(location_id), "storage_area_id": str(area_id)},
        headers=headers,
    )
    assert assigned.status_code == 200, assigned.text

    pack = await client.post(
        f"/v1/inventory/items/{item_id}/packs",
        json={
            "item_id": str(item_id),
            "supplier_id": supplier.json()["id"],
            "name": "100g pouch",
            "purchase_unit": "pouch",
            "base_quantity": 100,
            "last_price_minor": 200,
        },
        headers=headers,
    )
    assert pack.status_code == 201, pack.text
    order = await client.post(
        "/v1/inventory/purchase-orders",
        json={
            "location_id": str(location_id),
            "supplier_id": supplier.json()["id"],
            "lines": [{"pack_id": pack.json()["id"], "ordered_packs": 1, "unit_price_minor": 200}],
        },
        headers=headers,
    )
    assert order.status_code == 201, order.text
    details = await client.get(
        f"/v1/inventory/purchase-orders/{order.json()['id']}", headers=headers
    )
    receipt = await client.post(
        f"/v1/inventory/purchase-orders/{order.json()['id']}/receive",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "lines": [
                {
                    "line_id": details.json()["lines"][0]["id"],
                    "received_base_quantity": 100,
                    "total_cost_minor": 200,
                }
            ],
        },
        headers=headers,
    )
    assert receipt.status_code == 201, receipt.text

    recipe = await client.post(
        "/v1/inventory/recipes",
        json={
            "name": "Golden potato portion",
            "yield_quantity": 1,
            "yield_unit": "portion",
            "ingredients": [{"item_id": str(item_id), "quantity_base": 100}],
        },
        headers=headers,
    )
    assert recipe.status_code == 201, recipe.text
    menu_id, variant_id = uuid.uuid4(), uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO menu_items(id,org_id,name) VALUES(:id,:org,'Golden plate')"),
            {"id": menu_id, "org": org_id},
        )
    linked = await client.put(
        f"/v1/commerce/menu-items/{menu_id}/recipe",
        json={"recipe_id": recipe.json()["id"]},
        headers=headers,
    )
    assert linked.status_code == 200, linked.text

    zone = ZoneInfo(acct["location"]["timezone"])
    close_at = datetime.now(UTC) + timedelta(minutes=1)
    # Use the same location business-date calculation as imports, including pre-4am service.
    from app.integrations.providers.square.business_date import business_date

    day_start = time.fromisoformat(acct["location"]["business_day_start"])
    measured_to = business_date(close_at, acct["location"]["timezone"], day_start)
    receipt_date = date.fromisoformat(receipt.json()["business_date"])
    open_date = min(measured_to, receipt_date) - timedelta(days=1)
    opening_at = datetime.combine(open_date, time(23), zone).astimezone(UTC)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("""INSERT INTO menu_item_variants(id,org_id,menu_item_id,name)
            VALUES(:id,:org,:menu,'Regular')"""),
            {"id": variant_id, "org": org_id, "menu": menu_id},
        )
        connection_id = uuid.uuid4()
        await conn.execute(
            text("""INSERT INTO integration_connections(id,org_id,provider,status,created_by)
            VALUES(:id,:org,'fake','active',:user)"""),
            {"id": connection_id, "org": org_id, "user": user_id},
        )
        await conn.execute(
            text("""INSERT INTO integration_location_links(id,org_id,connection_id,
                external_location_id,external_name,location_id,timezone)
                VALUES(:id,:org,:connection,'golden-location','Golden',:location,:timezone)"""),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "connection": connection_id,
                "location": location_id,
                "timezone": acct["location"]["timezone"],
            },
        )
        await conn.execute(
            text("""INSERT INTO integration_streams(org_id,connection_id,stream,data_as_of,last_success_at)
            VALUES(:org,:connection,'orders',:as_of,:success)"""),
            {
                "org": org_id,
                "connection": connection_id,
                "as_of": close_at,
                "success": datetime.now(UTC),
            },
        )
        order_id = uuid.uuid4()
        await conn.execute(
            text("""INSERT INTO sales_orders(id,org_id,connection_id,external_id,location_id,
                business_date,closed_at,state,channel,gross_sales,discounts,returns,net_sales,tax,
                tips,service_charges,currency,source,source_version,source_updated_at)
                VALUES(:id,:org,:connection,'golden-order',:location,:business_date,:closed,
                'completed','counter',400,0,0,400,0,0,0,'USD','test',1,:closed)"""),
            {
                "id": order_id,
                "org": org_id,
                "connection": connection_id,
                "location": location_id,
                "business_date": measured_to,
                "closed": close_at,
            },
        )
        await conn.execute(
            text("""INSERT INTO sales_order_lines(id,org_id,order_id,location_id,business_date,
                line_uid,menu_item_variant_id,external_item_ref,name,quantity,gross,discounts,net)
                VALUES(:id,:org,:order,:location,:business_date,'golden-line',:variant,
                'golden-plate','Golden plate',2,400,0,400)"""),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "order": order_id,
                "location": location_id,
                "business_date": measured_to,
                "variant": variant_id,
            },
        )
        opening_id, closing_id = uuid.uuid4(), uuid.uuid4()
        for session_id, submitted_at, quantity in (
            (opening_id, opening_at, Decimal("1000")),
            (closing_id, close_at + timedelta(seconds=2), Decimal("850")),
        ):
            await conn.execute(
                text("""INSERT INTO count_sessions(id,org_id,location_id,storage_area_id,status,
                    started_by,submitted_by,started_at,submitted_at) VALUES(:id,:org,:loc,:area,
                    'submitted',:user,:user,:submitted,:submitted)"""),
                {
                    "id": session_id,
                    "org": org_id,
                    "loc": location_id,
                    "area": area_id,
                    "user": user_id,
                    "submitted": submitted_at,
                },
            )
            await conn.execute(
                text("""INSERT INTO count_lines(id,org_id,location_id,session_id,item_id,
                    expected_base_quantity,expected_level_version,counted_base_quantity,
                    item_name,base_unit) VALUES(:line,:org,:loc,:session,:item,0,0,:qty,
                    'Golden Potatoes','g')"""),
                {
                    "line": uuid.uuid4(),
                    "org": org_id,
                    "loc": location_id,
                    "session": session_id,
                    "item": item_id,
                    "qty": quantity,
                },
            )
        await conn.execute(
            text("UPDATE food_cost_baselines SET effective_at=:baseline WHERE org_id=:org"),
            {
                "org": org_id,
                "baseline": datetime.combine(open_date - timedelta(days=1), time(0), zone),
            },
        )

    response = await client.post(
        "/v1/inventory/food-cost-variance",
        json={
            "location_id": str(location_id),
            "opening_count_session_ids": [str(opening_id)],
            "closing_count_session_ids": [str(closing_id)],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "complete", report
    assert report["actual_cost_minor"] == 500
    assert report["theoretical_cost_minor"] == 400
    assert report["variance_minor"] == 100
    assert report["variance_pct"] == "25.00"
    assert Decimal(report["items"][0]["actual_depletion_quantity"]) == Decimal("250")
    assert Decimal(report["items"][0]["theoretical_usage_quantity"]) == Decimal("200")
