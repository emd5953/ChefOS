"""Inventory tables registered in SQLAlchemy metadata for migration drift checks."""

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base

m: MetaData = Base.metadata

Table(
    "suppliers",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("name", String, nullable=False),
    Column("contact_name", String),
    Column("email", String),
    Column("phone", String),
    Column("notes", String),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("org_id", "id", name="uq_suppliers_org_id_id"),
    UniqueConstraint("org_id", "name", name="uq_suppliers_org_id_name"),
)
Table(
    "inventory_items",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("name", String, nullable=False),
    Column("category", String, nullable=False, server_default=""),
    Column("base_unit", String, nullable=False),
    Column("dimension", String, nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("default_supplier_id", UUID(as_uuid=True)),
    Column("currency", String(3), nullable=False, server_default="USD"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("org_id", "id", name="uq_inventory_items_org_id_id"),
    ForeignKeyConstraint(["org_id", "default_supplier_id"], ["suppliers.org_id", "suppliers.id"]),
    CheckConstraint("base_unit IN ('g','ml','ea')", name="base_unit"),
    CheckConstraint("dimension IN ('mass','volume','count')", name="dimension"),
    CheckConstraint(
        "(dimension='mass' AND base_unit='g') OR "
        "(dimension='volume' AND base_unit='ml') OR "
        "(dimension='count' AND base_unit='ea')",
        name="dimension_unit",
    ),
)
Table(
    "item_unit_conversions",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=False),
    Column("unit", String, nullable=False),
    Column("base_units_per_unit", Numeric(18, 6), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint(
        "org_id", "item_id", "unit", name="uq_item_unit_conversions_org_id_item_id_unit"
    ),
    UniqueConstraint("org_id", "id", name="uq_item_unit_conversions_org_id_id"),
    ForeignKeyConstraint(["org_id", "item_id"], ["inventory_items.org_id", "inventory_items.id"]),
    CheckConstraint("base_units_per_unit > 0", name="base_units_per_unit_positive"),
)
Table(
    "item_packs",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=False),
    Column("supplier_id", UUID(as_uuid=True), nullable=False),
    Column("name", String, nullable=False),
    Column("purchase_unit", String, nullable=False),
    Column("base_quantity", Numeric(18, 6), nullable=False),
    Column("last_price_minor", BigInteger),
    Column("currency", String(3), nullable=False, server_default="USD"),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("org_id", "id", name="uq_item_packs_org_id_id"),
    ForeignKeyConstraint(["org_id", "item_id"], ["inventory_items.org_id", "inventory_items.id"]),
    ForeignKeyConstraint(["org_id", "supplier_id"], ["suppliers.org_id", "suppliers.id"]),
    CheckConstraint("base_quantity > 0", name="base_quantity_positive"),
    CheckConstraint("last_price_minor >= 0", name="last_price_minor_nonnegative"),
)
Table(
    "storage_areas",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("location_id", UUID(as_uuid=True), nullable=False),
    Column("name", String, nullable=False),
    Column("sort_order", Integer, nullable=False, server_default="0"),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("org_id", "id", name="uq_storage_areas_org_id_id"),
    UniqueConstraint(
        "org_id", "location_id", "name", name="uq_storage_areas_org_id_location_id_name"
    ),
    ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
)
Table(
    "item_storage_assignments",
    m,
    Column("org_id", UUID(as_uuid=True), primary_key=True),
    Column("location_id", UUID(as_uuid=True), primary_key=True),
    Column("storage_area_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), primary_key=True),
    ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
    ForeignKeyConstraint(
        ["org_id", "storage_area_id"], ["storage_areas.org_id", "storage_areas.id"]
    ),
    ForeignKeyConstraint(["org_id", "item_id"], ["inventory_items.org_id", "inventory_items.id"]),
)
for name in ("inventory_pars", "inventory_levels"):
    columns: list[Any] = [
        Column("org_id", UUID(as_uuid=True), primary_key=True),
        Column("location_id", UUID(as_uuid=True), primary_key=True),
        Column("item_id", UUID(as_uuid=True), primary_key=True),
    ]
    if name == "inventory_pars":
        columns += [
            Column("par_base_quantity", Numeric(18, 6), nullable=False),
            Column(
                "updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")
            ),
        ]
        checks = [CheckConstraint("par_base_quantity >= 0", name="par_base_quantity_nonnegative")]
    else:
        columns += [
            Column("on_hand_base_quantity", Numeric(18, 6), nullable=False, server_default="0"),
            Column("version", BigInteger, nullable=False, server_default="0"),
            Column(
                "updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")
            ),
        ]
        checks = [
            CheckConstraint("on_hand_base_quantity >= 0", name="on_hand_base_quantity_nonnegative")
        ]
    Table(
        name,
        m,
        *columns,
        ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
        ForeignKeyConstraint(
            ["org_id", "item_id"], ["inventory_items.org_id", "inventory_items.id"]
        ),
        *checks,
    )

# Operational inventory tables are registered here as Core tables because the workflow writes
# use explicit SQL to make transaction boundaries and row locks visible in each operation.
Table(
    "count_sessions",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("location_id", UUID(as_uuid=True), nullable=False),
    Column("storage_area_id", UUID(as_uuid=True), nullable=False),
    Column("status", String, nullable=False, server_default="draft"),
    Column("started_by", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("submitted_by", UUID(as_uuid=True), ForeignKey("users.id")),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("submitted_at", DateTime(timezone=True)),
    Column("note", String),
    UniqueConstraint("org_id", "id"),
    ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
    ForeignKeyConstraint(
        ["org_id", "storage_area_id"], ["storage_areas.org_id", "storage_areas.id"]
    ),
)
Table(
    "count_lines",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("location_id", UUID(as_uuid=True), nullable=False),
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=False),
    Column("expected_base_quantity", Numeric(18, 6), nullable=False),
    Column("expected_level_version", BigInteger, nullable=False),
    Column("counted_base_quantity", Numeric(18, 6)),
    Column("entered_quantity", Numeric(18, 6)),
    Column("entered_unit", String),
    Column("exception_state", String, nullable=False, server_default="clear"),
    Column("note", String),
    Column("item_name", String, nullable=False),
    Column("base_unit", String, nullable=False),
    UniqueConstraint("org_id", "session_id", "item_id"),
    ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
    ForeignKeyConstraint(["org_id", "session_id"], ["count_sessions.org_id", "count_sessions.id"]),
    ForeignKeyConstraint(["org_id", "item_id"], ["inventory_items.org_id", "inventory_items.id"]),
)
Table(
    "stock_movements",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("location_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=False),
    Column("movement_type", String, nullable=False),
    Column("source_id", UUID(as_uuid=True), nullable=False),
    Column("quantity_delta", Numeric(18, 6), nullable=False),
    Column("business_date", Date, nullable=False),
    Column("item_name", String, nullable=False),
    Column("base_unit", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
    ForeignKeyConstraint(["org_id", "item_id"], ["inventory_items.org_id", "inventory_items.id"]),
)
Table(
    "waste_entries",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("location_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=False),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("unit_cost_minor", Numeric(18, 6)),
    Column("reason", String, nullable=False),
    Column("station_id", UUID(as_uuid=True)),
    Column("shift_id", UUID(as_uuid=True)),
    Column("business_date", Date, nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("created_by", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("org_id", "id", name="uq_waste_entries_org_id"),
    UniqueConstraint("org_id", "idempotency_key", name="uq_waste_entries_org_idempotency_key"),
    ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
    ForeignKeyConstraint(["org_id", "item_id"], ["inventory_items.org_id", "inventory_items.id"]),
    CheckConstraint("quantity_base > 0", name="quantity_base_positive"),
)
Table(
    "purchase_orders",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("location_id", UUID(as_uuid=True), nullable=False),
    Column("supplier_id", UUID(as_uuid=True), nullable=False),
    Column("status", String, nullable=False, server_default="open"),
    Column("ordered_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("expected_at", Date),
    Column("created_by", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("currency", String(3), nullable=False, server_default="USD"),
    Column("note", String),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("org_id", "id"),
    ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
    ForeignKeyConstraint(["org_id", "supplier_id"], ["suppliers.org_id", "suppliers.id"]),
)
Table(
    "purchase_order_lines",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("location_id", UUID(as_uuid=True), nullable=False),
    Column("purchase_order_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=False),
    Column("pack_id", UUID(as_uuid=True)),
    Column("ordered_packs", Numeric(18, 6), nullable=False),
    Column("ordered_pack_name", String, nullable=False),
    Column("ordered_base_quantity_per_pack", Numeric(18, 6), nullable=False),
    Column("ordered_unit_price_minor", BigInteger, nullable=False),
    Column("ordered_quantity", Numeric(18, 6), nullable=False),
    Column("received_quantity", Numeric(18, 6), nullable=False, server_default="0"),
    Column("closed_quantity", Numeric(18, 6), nullable=False, server_default="0"),
    Column("item_name", String, nullable=False),
    Column("base_unit", String, nullable=False),
    Column("pack_id_snapshot", UUID(as_uuid=True)),
    UniqueConstraint("org_id", "id"),
    ForeignKeyConstraint(
        ["org_id", "purchase_order_id"], ["purchase_orders.org_id", "purchase_orders.id"]
    ),
    ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
    ForeignKeyConstraint(["org_id", "item_id"], ["inventory_items.org_id", "inventory_items.id"]),
    ForeignKeyConstraint(["org_id", "pack_id"], ["item_packs.org_id", "item_packs.id"]),
)
Table(
    "receipts",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("location_id", UUID(as_uuid=True), nullable=False),
    Column("purchase_order_id", UUID(as_uuid=True), nullable=False),
    Column("received_by", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("business_date", Date, nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("org_id", "id", name="uq_receipts_org_id_id"),
    UniqueConstraint("org_id", "idempotency_key", name="uq_receipts_org_idempotency_key"),
    ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
    ForeignKeyConstraint(
        ["org_id", "purchase_order_id"], ["purchase_orders.org_id", "purchase_orders.id"]
    ),
)
Table(
    "receipt_lines",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("location_id", UUID(as_uuid=True), nullable=False),
    Column("receipt_id", UUID(as_uuid=True), nullable=False),
    Column("purchase_order_line_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=False),
    Column("received_base_quantity", Numeric(18, 6), nullable=False),
    Column("total_cost_minor", BigInteger, nullable=False),
    Column("item_name", String, nullable=False),
    Column("base_unit", String, nullable=False),
    UniqueConstraint("org_id", "id"),
    ForeignKeyConstraint(["org_id", "receipt_id"], ["receipts.org_id", "receipts.id"]),
    ForeignKeyConstraint(
        ["org_id", "purchase_order_line_id"],
        ["purchase_order_lines.org_id", "purchase_order_lines.id"],
    ),
    ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
    ForeignKeyConstraint(["org_id", "item_id"], ["inventory_items.org_id", "inventory_items.id"]),
)
Table(
    "recipes",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False),
    Column("name", String, nullable=False),
    Column("yield_quantity", Numeric(18, 6), nullable=False),
    Column("yield_unit", String, nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("org_id", "id"),
)
Table(
    "recipe_ingredients",
    m,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("recipe_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=False),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("item_name", String, nullable=False),
    Column("base_unit", String, nullable=False),
    UniqueConstraint("org_id", "recipe_id", "item_id"),
    ForeignKeyConstraint(["org_id", "recipe_id"], ["recipes.org_id", "recipes.id"]),
    ForeignKeyConstraint(["org_id", "item_id"], ["inventory_items.org_id", "inventory_items.id"]),
)
