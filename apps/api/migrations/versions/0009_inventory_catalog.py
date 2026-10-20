"""inventory catalog and stock foundation

Revision ID: 0009
Revises: 0004
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("inventory_levels", "inventory_pars", "item_storage_assignments", "storage_areas", "item_packs", "item_unit_conversions", "inventory_items", "suppliers")


def upgrade() -> None:
    statements = """
    ALTER TABLE locations ADD CONSTRAINT uq_locations_org_id_id UNIQUE(org_id,id);
    CREATE TABLE suppliers (
      id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organizations(id), name varchar NOT NULL,
      contact_name varchar, email varchar, phone varchar, notes varchar, active boolean NOT NULL DEFAULT true,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_suppliers_org_id_id UNIQUE(org_id,id), CONSTRAINT uq_suppliers_org_id_name UNIQUE(org_id,name)
    );
    CREATE TABLE inventory_items (
      id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organizations(id), name varchar NOT NULL,
      category varchar NOT NULL DEFAULT '', base_unit varchar NOT NULL CONSTRAINT ck_inventory_items_base_unit CHECK(base_unit IN ('g','ml','ea')),
      dimension varchar NOT NULL CONSTRAINT ck_inventory_items_dimension CHECK(dimension IN ('mass','volume','count')),
      active boolean NOT NULL DEFAULT true, default_supplier_id uuid, currency varchar(3) NOT NULL DEFAULT 'USD',
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_inventory_items_org_id_id UNIQUE(org_id,id), FOREIGN KEY(org_id,default_supplier_id) REFERENCES suppliers(org_id,id),
      CONSTRAINT ck_inventory_items_dimension_unit CHECK ((dimension='mass' AND base_unit='g') OR (dimension='volume' AND base_unit='ml') OR (dimension='count' AND base_unit='ea'))
    );
    CREATE TABLE item_unit_conversions (
      id uuid PRIMARY KEY, org_id uuid NOT NULL, item_id uuid NOT NULL, unit varchar NOT NULL,
      base_units_per_unit numeric(18,6) NOT NULL CHECK(base_units_per_unit > 0),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_item_unit_conversions_org_id_item_id_unit UNIQUE(org_id,item_id,unit), CONSTRAINT uq_item_unit_conversions_org_id_id UNIQUE(org_id,id),
      FOREIGN KEY(org_id,item_id) REFERENCES inventory_items(org_id,id)
    );
    CREATE TABLE item_packs (
      id uuid PRIMARY KEY, org_id uuid NOT NULL, item_id uuid NOT NULL, supplier_id uuid NOT NULL,
      name varchar NOT NULL, purchase_unit varchar NOT NULL, base_quantity numeric(18,6) NOT NULL CHECK(base_quantity > 0),
      last_price_minor bigint CHECK(last_price_minor >= 0), currency varchar(3) NOT NULL DEFAULT 'USD',
      active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_item_packs_org_id_id UNIQUE(org_id,id), FOREIGN KEY(org_id,item_id) REFERENCES inventory_items(org_id,id),
      FOREIGN KEY(org_id,supplier_id) REFERENCES suppliers(org_id,id)
    );
    CREATE TABLE storage_areas (
      id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organizations(id), location_id uuid NOT NULL,
      name varchar NOT NULL, sort_order integer NOT NULL DEFAULT 0, active boolean NOT NULL DEFAULT true,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_storage_areas_org_id_id UNIQUE(org_id,id), CONSTRAINT uq_storage_areas_org_id_location_id_name UNIQUE(org_id,location_id,name), FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id)
    );
    CREATE TABLE item_storage_assignments (
      org_id uuid NOT NULL, location_id uuid NOT NULL, storage_area_id uuid NOT NULL, item_id uuid NOT NULL,
      PRIMARY KEY(org_id,location_id,item_id), FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id),
      FOREIGN KEY(org_id,storage_area_id) REFERENCES storage_areas(org_id,id), FOREIGN KEY(org_id,item_id) REFERENCES inventory_items(org_id,id)
    );
    CREATE TABLE inventory_pars (
      org_id uuid NOT NULL, location_id uuid NOT NULL, item_id uuid NOT NULL,
      par_base_quantity numeric(18,6) NOT NULL CHECK(par_base_quantity >= 0), updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(org_id,location_id,item_id), FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id),
      FOREIGN KEY(org_id,item_id) REFERENCES inventory_items(org_id,id)
    );
    CREATE TABLE inventory_levels (
      org_id uuid NOT NULL, location_id uuid NOT NULL, item_id uuid NOT NULL,
      on_hand_base_quantity numeric(18,6) NOT NULL DEFAULT 0 CHECK(on_hand_base_quantity >= 0),
      version bigint NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(org_id,location_id,item_id), FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id),
      FOREIGN KEY(org_id,item_id) REFERENCES inventory_items(org_id,id)
    );
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)
    for table in reversed(TABLES):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY org_isolation ON {table} USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO chefos_app")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE {table} CASCADE")
    op.execute("ALTER TABLE locations DROP CONSTRAINT uq_locations_org_id_id")
