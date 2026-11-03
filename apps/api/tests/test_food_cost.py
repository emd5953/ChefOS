"""Pure formula and coverage tests for food cost variance."""

import uuid
from datetime import UTC, datetime, time, timedelta
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
