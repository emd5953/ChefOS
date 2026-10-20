"""Inventory tables registered in SQLAlchemy metadata for migration drift checks."""

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
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
