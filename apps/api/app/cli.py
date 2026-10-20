"""Management CLI: `python -m app.cli <command>`.

Add a command by decorating a function with `@command("name")`.
"""
# ruff: noqa: E501

import argparse
import asyncio
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from app.config import get_settings

Handler = Callable[[argparse.Namespace], Coroutine[Any, Any, int]]
COMMANDS: dict[str, tuple[str, Handler]] = {}


def command(name: str, help_text: str = "") -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        COMMANDS[name] = (help_text or (fn.__doc__ or "").strip(), fn)
        return fn

    return register


DEMO_EMAIL = "owner@chefos.local"
DEMO_PASSWORD = "ChefOS-demo-2026!"  # noqa: S105  (local demo account)


@command("seed-demo", "Create the demo owner, org, and location (idempotent)")
async def seed_demo(_: argparse.Namespace) -> int:
    from app.errors import ProblemError
    from app.identity.schemas import SignupRequest
    from app.identity.service import register_account

    data = SignupRequest(
        email=DEMO_EMAIL,
        password=DEMO_PASSWORD,
        display_name="Morgan Lee",
        org_name="Juniper House",
        location_name="Juniper House · Brooklyn",
        timezone="America/New_York",
    )
    try:
        reg = await register_account(data)
    except ProblemError as exc:
        if exc.code == "conflict":
            print(f"Demo account already exists: {DEMO_EMAIL}")
            return 0
        raise
    # Defaults match the spec: address "Brooklyn, NY", business day starts 04:00.
    from app.db import scoped_session
    from app.identity.models import Location

    async with scoped_session(reg.org.id) as db:
        location = await db.get_one(Location, reg.location.id)
        location.address = "Brooklyn, NY"
    print(f"Seeded demo account: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    return 0


@command("seed-inventory-demo", "Seed realistic inventory data for the demo restaurant")
async def seed_inventory_demo(_: argparse.Namespace) -> int:
    """Add representative stock, supplier purchasing, recipes, and POS cost links."""
    import uuid
    from decimal import Decimal

    from sqlalchemy import text

    from app.db import scoped_session
    from app.identity import service as identity

    await seed_demo(argparse.Namespace())
    login = await identity.login(
        DEMO_EMAIL, DEMO_PASSWORD, user_agent="cli-inventory-demo", ip=None
    )
    org_id, user_id = login.org.id, login.user.id
    async with scoped_session(org_id) as db:
        location_id = await db.scalar(
            text("SELECT id FROM locations WHERE org_id=:org ORDER BY created_at LIMIT 1"),
            {"org": org_id},
        )
        if location_id is None:
            print("Demo location not found; run seed-demo first.")
            return 1
        if await db.scalar(
            text("SELECT id FROM inventory_items WHERE org_id=:org AND name='Demo Roma Tomatoes'"),
            {"org": org_id},
        ):
            print("Demo inventory already seeded.")
            return 0
        supplier_rows: dict[str, uuid.UUID] = {}
        for name in ("Green Market Produce", "Hudson Valley Dairy", "Brooklyn Provisions"):
            sid = uuid.uuid7()
            await db.execute(
                text(
                    "INSERT INTO suppliers(id,org_id,name,contact_name,email,phone,notes) VALUES(:id,:org,:name,:contact,:email,:phone,:notes)"
                ),
                {
                    "id": sid,
                    "org": org_id,
                    "name": name,
                    "contact": "Demo account",
                    "email": "orders@example.test",
                    "phone": "555-0142",
                    "notes": "Demo supplier record",
                },
            )
            supplier_rows[name] = sid
        area_ids: dict[str, uuid.UUID] = {}
        for name, sort_order in (("Walk-in cooler", 1), ("Dry storage", 2), ("Freezer", 3)):
            area_id = uuid.uuid7()
            await db.execute(
                text(
                    "INSERT INTO storage_areas(id,org_id,location_id,name,sort_order) VALUES(:id,:org,:loc,:name,:sort)"
                ),
                {
                    "id": area_id,
                    "org": org_id,
                    "loc": location_id,
                    "name": name,
                    "sort": sort_order,
                },
            )
            area_ids[name] = area_id
        products = [
            (
                "Demo Roma Tomatoes",
                "Produce",
                "mass",
                "g",
                "kg",
                1000,
                "10 lb case",
                4535.924,
                2850,
                "Green Market Produce",
                "Walk-in cooler",
                9000,
            ),
            (
                "Demo Yellow Onions",
                "Produce",
                "mass",
                "g",
                "kg",
                1000,
                "25 lb sack",
                11339.81,
                2100,
                "Green Market Produce",
                "Dry storage",
                6800,
            ),
            (
                "Demo Baby Spinach",
                "Produce",
                "mass",
                "g",
                "kg",
                1000,
                "2 lb case",
                907.185,
                1900,
                "Green Market Produce",
                "Walk-in cooler",
                1800,
            ),
            (
                "Demo Whole Milk",
                "Dairy",
                "volume",
                "ml",
                "l",
                1000,
                "1 gallon jug",
                3785.412,
                520,
                "Hudson Valley Dairy",
                "Walk-in cooler",
                7570,
            ),
            (
                "Demo Heavy Cream",
                "Dairy",
                "volume",
                "ml",
                "l",
                1000,
                "quart",
                946.353,
                690,
                "Hudson Valley Dairy",
                "Walk-in cooler",
                1893,
            ),
            (
                "Demo Mozzarella",
                "Dairy",
                "mass",
                "g",
                "kg",
                1000,
                "5 lb block",
                2267.962,
                3250,
                "Hudson Valley Dairy",
                "Walk-in cooler",
                4536,
            ),
            (
                "Demo Bread Flour",
                "Dry goods",
                "mass",
                "g",
                "kg",
                1000,
                "50 lb bag",
                22679.62,
                2850,
                "Brooklyn Provisions",
                "Dry storage",
                13608,
            ),
            (
                "Demo Olive Oil",
                "Dry goods",
                "volume",
                "ml",
                "l",
                1000,
                "3 liter tin",
                3000,
                2490,
                "Brooklyn Provisions",
                "Dry storage",
                4500,
            ),
            (
                "Demo Chicken Thighs",
                "Protein",
                "mass",
                "g",
                "kg",
                1000,
                "10 lb case",
                4535.924,
                4680,
                "Brooklyn Provisions",
                "Freezer",
                9072,
            ),
            (
                "Demo Ground Beef",
                "Protein",
                "mass",
                "g",
                "kg",
                1000,
                "5 lb pack",
                2267.962,
                3290,
                "Brooklyn Provisions",
                "Freezer",
                4536,
            ),
            (
                "Demo Eggs",
                "Dairy",
                "count",
                "ea",
                "ea",
                1,
                "case of 15 dozen",
                180,
                3650,
                "Hudson Valley Dairy",
                "Walk-in cooler",
                120,
            ),
            (
                "Demo Butter",
                "Dairy",
                "mass",
                "g",
                "kg",
                1000,
                "1 lb pack",
                453.592,
                650,
                "Hudson Valley Dairy",
                "Walk-in cooler",
                1814,
            ),
            (
                "Demo Parmesan",
                "Dairy",
                "mass",
                "g",
                "kg",
                1000,
                "5 lb wedge",
                2267.962,
                3850,
                "Hudson Valley Dairy",
                "Walk-in cooler",
                907,
            ),
            (
                "Demo Garlic",
                "Produce",
                "mass",
                "g",
                "kg",
                1000,
                "5 lb case",
                2267.962,
                1490,
                "Green Market Produce",
                "Dry storage",
                680,
            ),
            (
                "Demo Basil",
                "Produce",
                "mass",
                "g",
                "kg",
                1000,
                "1 lb case",
                453.592,
                1250,
                "Green Market Produce",
                "Walk-in cooler",
                227,
            ),
        ]
        item_ids: dict[str, uuid.UUID] = {}
        pack_ids: dict[str, uuid.UUID] = {}
        for (
            name,
            category,
            dimension,
            base,
            conv,
            factor,
            pack_name,
            pack_qty,
            price,
            supplier_name,
            area_name,
            par,
        ) in products:
            iid, pid = uuid.uuid7(), uuid.uuid7()
            item_ids[name] = iid
            pack_ids[name] = pid
            supplier_id = supplier_rows[supplier_name]
            await db.execute(
                text(
                    "INSERT INTO inventory_items(id,org_id,name,category,base_unit,dimension,default_supplier_id,currency) VALUES(:id,:org,:name,:category,:unit,:dimension,:supplier,'USD')"
                ),
                {
                    "id": iid,
                    "org": org_id,
                    "name": name,
                    "category": category,
                    "unit": base,
                    "dimension": dimension,
                    "supplier": supplier_id,
                },
            )
            if conv != base:
                await db.execute(
                    text(
                        "INSERT INTO item_unit_conversions(id,org_id,item_id,unit,base_units_per_unit) VALUES(:id,:org,:item,:unit,:factor)"
                    ),
                    {
                        "id": uuid.uuid7(),
                        "org": org_id,
                        "item": iid,
                        "unit": conv,
                        "factor": factor,
                    },
                )
            await db.execute(
                text(
                    "INSERT INTO item_packs(id,org_id,item_id,supplier_id,name,purchase_unit,base_quantity,last_price_minor,currency) VALUES(:id,:org,:item,:supplier,:name,'case',:qty,:price,'USD')"
                ),
                {
                    "id": pid,
                    "org": org_id,
                    "item": iid,
                    "supplier": supplier_id,
                    "name": pack_name,
                    "qty": pack_qty,
                    "price": price,
                },
            )
            await db.execute(
                text(
                    "INSERT INTO item_storage_assignments(org_id,location_id,storage_area_id,item_id) VALUES(:org,:loc,:area,:item)"
                ),
                {"org": org_id, "loc": location_id, "area": area_ids[area_name], "item": iid},
            )
            await db.execute(
                text(
                    "INSERT INTO inventory_pars(org_id,location_id,item_id,par_base_quantity) VALUES(:org,:loc,:item,:qty)"
                ),
                {"org": org_id, "loc": location_id, "item": iid, "qty": par},
            )
        po_id = uuid.uuid7()
        supplier_id = supplier_rows["Green Market Produce"]
        await db.execute(
            text(
                "INSERT INTO purchase_orders(id,org_id,location_id,supplier_id,created_by,currency,note) VALUES(:id,:org,:loc,:supplier,:user,'USD','Demo opening delivery')"
            ),
            {
                "id": po_id,
                "org": org_id,
                "loc": location_id,
                "supplier": supplier_id,
                "user": user_id,
            },
        )
        roma = item_ids["Demo Roma Tomatoes"]
        roma_pack = next(p for p in products if p[0] == "Demo Roma Tomatoes")
        qty = Decimal(str(roma_pack[7])) * 2
        po_line = uuid.uuid7()
        await db.execute(
            text(
                "INSERT INTO purchase_order_lines(id,org_id,location_id,purchase_order_id,item_id,pack_id,ordered_packs,ordered_pack_name,ordered_base_quantity_per_pack,ordered_unit_price_minor,ordered_quantity,item_name,base_unit,pack_id_snapshot) VALUES(:id,:org,:loc,:po,:item,:pack,2,:pack_name,:base,:price,:qty,:item_name,'g',:pack)"
            ),
            {
                "id": po_line,
                "org": org_id,
                "loc": location_id,
                "po": po_id,
                "item": roma,
                "pack": pack_ids["Demo Roma Tomatoes"],
                "pack_name": roma_pack[6],
                "base": roma_pack[7],
                "price": roma_pack[8],
                "qty": qty,
                "item_name": "Demo Roma Tomatoes",
            },
        )
        receipt_id = uuid.uuid7()
        await db.execute(
            text(
                "INSERT INTO receipts(id,org_id,location_id,purchase_order_id,received_by,business_date,idempotency_key) VALUES(:id,:org,:loc,:po,:user,current_date,'demo-inventory-receipt-v1')"
            ),
            {"id": receipt_id, "org": org_id, "loc": location_id, "po": po_id, "user": user_id},
        )
        order_by_supplier = {"Green Market Produce": po_id}
        receipt_by_supplier = {"Green Market Produce": receipt_id}
        received = Decimal(str(roma_pack[7]))
        await db.execute(
            text(
                "INSERT INTO receipt_lines(id,org_id,location_id,receipt_id,purchase_order_line_id,item_id,received_base_quantity,total_cost_minor,item_name,base_unit) VALUES(:id,:org,:loc,:receipt,:line,:item,:qty,:cost,'Demo Roma Tomatoes','g')"
            ),
            {
                "id": uuid.uuid7(),
                "org": org_id,
                "loc": location_id,
                "receipt": receipt_id,
                "line": po_line,
                "item": roma,
                "qty": received,
                "cost": 2850,
            },
        )
        await db.execute(
            text(
                "UPDATE purchase_order_lines SET received_quantity=:qty WHERE id=:id AND org_id=:org"
            ),
            {"qty": received, "id": po_line, "org": org_id},
        )
        await db.execute(
            text(
                "UPDATE purchase_orders SET status='partially_received' WHERE id=:id AND org_id=:org"
            ),
            {"id": po_id, "org": org_id},
        )
        await db.execute(
            text(
                "INSERT INTO inventory_levels(org_id,location_id,item_id,on_hand_base_quantity,version) VALUES(:org,:loc,:item,:qty,1) ON CONFLICT(org_id,location_id,item_id) DO UPDATE SET on_hand_base_quantity=excluded.on_hand_base_quantity,version=inventory_levels.version+1,updated_at=now()"
            ),
            {"org": org_id, "loc": location_id, "item": roma, "qty": received},
        )
        await db.execute(
            text(
                "INSERT INTO stock_movements(id,org_id,location_id,item_id,movement_type,source_id,quantity_delta,business_date,item_name,base_unit) VALUES(:id,:org,:loc,:item,'receipt',:source,:qty,current_date,'Demo Roma Tomatoes','g')"
            ),
            {
                "id": uuid.uuid7(),
                "org": org_id,
                "loc": location_id,
                "item": roma,
                "source": receipt_id,
                "qty": received,
            },
        )
        recipe_specs = [
            (
                "Demo Margherita Pizza",
                ("Demo Roma Tomatoes", 120),
                ("Demo Mozzarella", 90),
                ("Demo Basil", 4),
                ("Demo Olive Oil", 8),
            ),
            (
                "Demo Creamy Tomato Pasta",
                ("Demo Roma Tomatoes", 180),
                ("Demo Heavy Cream", 45),
                ("Demo Parmesan", 22),
                ("Demo Basil", 3),
            ),
            (
                "Demo Chicken Parmesan",
                ("Demo Chicken Thighs", 180),
                ("Demo Mozzarella", 60),
                ("Demo Parmesan", 18),
                ("Demo Olive Oil", 12),
            ),
        ]
        recipe_item_names = {ingredient[0] for recipe in recipe_specs for ingredient in recipe[1:]}
        for item_name in sorted(recipe_item_names - {"Demo Roma Tomatoes"}):
            product = next(product for product in products if product[0] == item_name)
            base_unit = str(product[3])
            pack_name = str(product[6])
            pack_quantity = Decimal(str(product[7]))
            unit_price = int(str(product[8]))
            supplier_name = str(product[9])
            if supplier_name not in order_by_supplier:
                supplier_order_id = uuid.uuid7()
                supplier_receipt_id = uuid.uuid7()
                await db.execute(
                    text(
                        "INSERT INTO purchase_orders(id,org_id,location_id,supplier_id,created_by,currency,note) "
                        "VALUES(:id,:org,:loc,:supplier,:user,'USD','Demo opening delivery')"
                    ),
                    {
                        "id": supplier_order_id,
                        "org": org_id,
                        "loc": location_id,
                        "supplier": supplier_rows[supplier_name],
                        "user": user_id,
                    },
                )
                await db.execute(
                    text(
                        "INSERT INTO receipts(id,org_id,location_id,purchase_order_id,received_by,business_date,idempotency_key) "
                        "VALUES(:id,:org,:loc,:po,:user,current_date,:key)"
                    ),
                    {
                        "id": supplier_receipt_id,
                        "org": org_id,
                        "loc": location_id,
                        "po": supplier_order_id,
                        "user": user_id,
                        "key": f"demo-inventory-receipt-{supplier_name.lower().replace(' ', '-')}",
                    },
                )
                order_by_supplier[supplier_name] = supplier_order_id
                receipt_by_supplier[supplier_name] = supplier_receipt_id
            supplier_order_id = order_by_supplier[supplier_name]
            supplier_receipt_id = receipt_by_supplier[supplier_name]
            item_id = item_ids[item_name]
            pack_id = pack_ids[item_name]
            po_line_id = uuid.uuid7()
            received_quantity = Decimal(str(pack_quantity))
            await db.execute(
                text(
                    "INSERT INTO purchase_order_lines(id,org_id,location_id,purchase_order_id,item_id,pack_id,ordered_packs,ordered_pack_name,ordered_base_quantity_per_pack,ordered_unit_price_minor,ordered_quantity,item_name,base_unit,pack_id_snapshot) "
                    "VALUES(:id,:org,:loc,:po,:item,:pack,2,:pack_name,:base,:price,:qty,:item_name,:unit,:pack)"
                ),
                {
                    "id": po_line_id,
                    "org": org_id,
                    "loc": location_id,
                    "po": supplier_order_id,
                    "item": item_id,
                    "pack": pack_id,
                    "pack_name": pack_name,
                    "base": pack_quantity,
                    "price": unit_price,
                    "qty": received_quantity * 2,
                    "item_name": item_name,
                    "unit": base_unit,
                },
            )
            await db.execute(
                text(
                    "INSERT INTO receipt_lines(id,org_id,location_id,receipt_id,purchase_order_line_id,item_id,received_base_quantity,total_cost_minor,item_name,base_unit) "
                    "VALUES(:id,:org,:loc,:receipt,:line,:item,:qty,:cost,:name,:unit)"
                ),
                {
                    "id": uuid.uuid7(),
                    "org": org_id,
                    "loc": location_id,
                    "receipt": supplier_receipt_id,
                    "line": po_line_id,
                    "item": item_id,
                    "qty": received_quantity,
                    "cost": unit_price,
                    "name": item_name,
                    "unit": base_unit,
                },
            )
            await db.execute(
                text(
                    "UPDATE purchase_order_lines SET received_quantity=:qty WHERE id=:id AND org_id=:org"
                ),
                {"qty": received_quantity, "id": po_line_id, "org": org_id},
            )
            await db.execute(
                text(
                    "UPDATE purchase_orders SET status='partially_received' WHERE id=:id AND org_id=:org"
                ),
                {"id": supplier_order_id, "org": org_id},
            )
            await db.execute(
                text(
                    "INSERT INTO inventory_levels(org_id,location_id,item_id,on_hand_base_quantity,version) "
                    "VALUES(:org,:loc,:item,:qty,1) ON CONFLICT(org_id,location_id,item_id) "
                    "DO UPDATE SET on_hand_base_quantity=inventory_levels.on_hand_base_quantity+excluded.on_hand_base_quantity,version=inventory_levels.version+1,updated_at=now()"
                ),
                {"org": org_id, "loc": location_id, "item": item_id, "qty": received_quantity},
            )
            await db.execute(
                text(
                    "INSERT INTO stock_movements(id,org_id,location_id,item_id,movement_type,source_id,quantity_delta,business_date,item_name,base_unit) "
                    "VALUES(:id,:org,:loc,:item,'receipt',:source,:qty,current_date,:name,:unit)"
                ),
                {
                    "id": uuid.uuid7(),
                    "org": org_id,
                    "loc": location_id,
                    "item": item_id,
                    "source": supplier_receipt_id,
                    "qty": received_quantity,
                    "name": item_name,
                    "unit": base_unit,
                },
            )
        for recipe_name, *ingredients in recipe_specs:
            recipe_id = uuid.uuid7()
            await db.execute(
                text(
                    "INSERT INTO recipes(id,org_id,name,yield_quantity,yield_unit) VALUES(:id,:org,:name,1,'portion')"
                ),
                {"id": recipe_id, "org": org_id, "name": recipe_name},
            )
            for item_name, quantity in ingredients:
                await db.execute(
                    text(
                        "INSERT INTO recipe_ingredients(id,org_id,recipe_id,item_id,quantity_base,item_name,base_unit) SELECT :id,:org,:recipe,i.id,:qty,i.name,i.base_unit FROM inventory_items i WHERE i.id=:item AND i.org_id=:org"
                    ),
                    {
                        "id": uuid.uuid7(),
                        "org": org_id,
                        "recipe": recipe_id,
                        "item": item_ids[item_name],
                        "qty": quantity,
                    },
                )
            menu = await db.scalar(
                text(
                    "SELECT id FROM menu_items WHERE org_id=:org AND lower(name) LIKE :name ORDER BY name LIMIT 1"
                ),
                {
                    "org": org_id,
                    "name": f"%{recipe_name.removeprefix('Demo ').split()[0].lower()}%",
                },
            )
            if menu is None:
                menu = await db.scalar(
                    text(
                        "SELECT id FROM menu_items WHERE org_id=:org AND recipe_id IS NULL ORDER BY name LIMIT 1"
                    ),
                    {"org": org_id},
                )
            if menu is not None:
                await db.execute(
                    text("UPDATE menu_items SET recipe_id=:recipe WHERE id=:menu AND org_id=:org"),
                    {"recipe": recipe_id, "menu": menu, "org": org_id},
                )
        print(
            f"Seeded demo inventory in location {location_id}: 15 items, 3 storage areas, one partially received PO, and 3 recipes."
        )
    return 0


WARM_DAYS = 30
DEMO_URL = "http://localhost:3000/login"


@command("demo", "Seed the demo org, connect fake Square, backfill; wait for the last 30 days")
async def demo(args: argparse.Namespace) -> int:
    """Idempotent: re-running reuses the existing connection and backfill."""
    import time
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.db import scoped_session
    from app.identity import service as identity
    from app.integrations import service, sync, temporal
    from app.integrations.models import IntegrationConnection
    from app.integrations.providers.square.business_date import business_date

    if get_settings().square_mode != "fake":
        print("`demo` connects the simulated Square; set SQUARE_MODE=fake.")
        return 2
    await seed_demo(args)
    login = await identity.login(DEMO_EMAIL, DEMO_PASSWORD, user_agent="cli-demo", ip=None)
    org_id, user_id = login.org.id, login.user.id

    async with scoped_session(org_id) as db:
        conn = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.provider == "square",
                IntegrationConnection.status != "disconnected",
            )
        )
        if conn is None:
            conn = IntegrationConnection(
                org_id=org_id, provider="square", mode="fake", created_by=user_id
            )
            db.add(conn)
            await db.flush()
        connection_id, status = conn.id, conn.status
    try:
        if status == "active":
            print(f"Square already connected ({connection_id}); resuming.")
            await temporal.ensure_schedules(org_id, connection_id)
            await service.maybe_start_backfill(org_id, connection_id)
        else:
            print("Connecting simulated Square ...")
            await service.complete_oauth(
                service.sign_state(org_id, connection_id, user_id), "fake-code"
            )
    except (OSError, RuntimeError) as exc:
        print(f"Could not reach Temporal ({exc}). Is the stack up? Run `make up`.")
        return 1

    print(f"Backfilling; waiting for the last {WARM_DAYS} days ...")
    started = time.monotonic()
    while True:
        async with scoped_session(org_id) as db:
            state = await sync._load_state(db, org_id, connection_id)
        done_through = {
            s: (row.backfill_completed_through if row else None)
            for s, row in state.streams.items()
            if s in sync.FACT_STREAMS
        }
        tz, day_start = state.zone
        today = business_date(datetime.now(UTC), tz, day_start)
        target = today - timedelta(days=WARM_DAYS - 1)
        reached = [d for d in done_through.values() if d is not None and d <= target]
        loaded = max(
            (min(WARM_DAYS, (today - d).days + 1) for d in done_through.values() if d), default=0
        )
        if len(reached) == len(done_through) and done_through:
            break
        elapsed = int(time.monotonic() - started)
        print(f"  [{elapsed:>3}s] {loaded}/{WARM_DAYS} days loaded", flush=True)
        if elapsed > 600:
            print("Timed out. Check that the worker is running: `make logs`.")
            return 1
        await asyncio.sleep(2)
    print(f"Last {WARM_DAYS} days loaded in {int(time.monotonic() - started)}s.")
    print("The rest of the 13-month backfill continues in the background.")
    await seed_team(org_id, user_id)
    await seed_inventory_demo(argparse.Namespace())
    print(f"\nOpen {DEMO_URL}\n  email:    {DEMO_EMAIL}\n  password: {DEMO_PASSWORD}")
    print(f"  manager:  {TEAM_GM_EMAIL} (same password)")
    print(f"  employee: {TEAM_EMPLOYEE_EMAIL} (same password; opens the phone view at /my)")
    return 0


TEAM_GM_EMAIL = "gm@chefos.local"
TEAM_EMPLOYEE_EMAIL = "maya@chefos.local"


async def seed_team(org_id: uuid.UUID, owner_user_id: uuid.UUID) -> None:
    """Team demo data: manager and employee logins and a published current week (idempotent)."""
    from app.team import demo as team_demo

    created = await team_demo.seed_logins(org_id, owner_user_id, DEMO_PASSWORD)
    for email in created:
        print(f"Seeded login: {email}")
    shifts = await team_demo.seed_week(org_id, owner_user_id)
    if shifts:
        print(f"Published this week's schedule ({shifts} shifts).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (help_text, _) in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    args = parser.parse_args(argv)
    get_settings()
    return asyncio.run(COMMANDS[args.command][1](args))


if __name__ == "__main__":
    raise SystemExit(main())
