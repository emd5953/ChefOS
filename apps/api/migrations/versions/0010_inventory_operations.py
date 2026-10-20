"""Count sessions, purchasing, receipts, and recipes.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "recipe_ingredients",
    "recipes",
    "receipt_lines",
    "receipts",
    "purchase_order_lines",
    "purchase_orders",
    "stock_movements",
    "count_lines",
    "count_sessions",
)


def upgrade() -> None:
    sql = """
    CREATE TABLE count_sessions (
      id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organizations(id), location_id uuid NOT NULL,
      storage_area_id uuid NOT NULL, status varchar NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted')),
      started_by uuid NOT NULL REFERENCES users(id), submitted_by uuid REFERENCES users(id),
      started_at timestamptz NOT NULL DEFAULT now(), submitted_at timestamptz, note varchar,
      CONSTRAINT uq_count_sessions_org_id UNIQUE(org_id,id), FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id),
      FOREIGN KEY(org_id,storage_area_id) REFERENCES storage_areas(org_id,id)
    );
    CREATE TABLE count_lines (
      id uuid PRIMARY KEY, org_id uuid NOT NULL, location_id uuid NOT NULL, session_id uuid NOT NULL,
      item_id uuid NOT NULL, expected_base_quantity numeric(18,6) NOT NULL,
      expected_level_version bigint NOT NULL, counted_base_quantity numeric(18,6),
      entered_quantity numeric(18,6), entered_unit varchar, exception_state varchar NOT NULL DEFAULT 'clear'
        CHECK(exception_state IN ('clear','review_required','confirmed')), note varchar, item_name varchar NOT NULL,
      base_unit varchar NOT NULL, CONSTRAINT uq_count_lines_org_id UNIQUE(org_id,session_id,item_id),
      FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id),
      FOREIGN KEY(org_id,session_id) REFERENCES count_sessions(org_id,id),
      FOREIGN KEY(org_id,item_id) REFERENCES inventory_items(org_id,id),
      CHECK(expected_base_quantity>=0 AND (counted_base_quantity IS NULL OR counted_base_quantity>=0))
    );
    CREATE TABLE stock_movements (
      id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organizations(id), location_id uuid NOT NULL,
      item_id uuid NOT NULL, movement_type varchar NOT NULL CHECK(movement_type IN ('count','receipt')),
      source_id uuid NOT NULL, quantity_delta numeric(18,6) NOT NULL, business_date date NOT NULL,
      item_name varchar NOT NULL, base_unit varchar NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id),
      FOREIGN KEY(org_id,item_id) REFERENCES inventory_items(org_id,id)
    );
    CREATE TABLE purchase_orders (
      id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organizations(id), location_id uuid NOT NULL,
      supplier_id uuid NOT NULL, status varchar NOT NULL DEFAULT 'open' CHECK(status IN ('open','partially_received','closed')),
      ordered_at timestamptz NOT NULL DEFAULT now(), expected_at date, created_by uuid NOT NULL REFERENCES users(id),
      currency varchar(3) NOT NULL DEFAULT 'USD', note varchar, created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(), CONSTRAINT uq_purchase_orders_org_id UNIQUE(org_id,id),
      FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id),
      FOREIGN KEY(org_id,supplier_id) REFERENCES suppliers(org_id,id)
    );
    CREATE TABLE purchase_order_lines (
      id uuid PRIMARY KEY, org_id uuid NOT NULL, location_id uuid NOT NULL, purchase_order_id uuid NOT NULL,
      item_id uuid NOT NULL, pack_id uuid, ordered_packs numeric(18,6) NOT NULL,
      ordered_pack_name varchar NOT NULL, ordered_base_quantity_per_pack numeric(18,6) NOT NULL,
      ordered_unit_price_minor bigint NOT NULL, ordered_quantity numeric(18,6) NOT NULL,
      received_quantity numeric(18,6) NOT NULL DEFAULT 0, closed_quantity numeric(18,6) NOT NULL DEFAULT 0,
      item_name varchar NOT NULL, base_unit varchar NOT NULL, pack_id_snapshot uuid,
      CONSTRAINT uq_purchase_order_lines_org_id UNIQUE(org_id,id), FOREIGN KEY(org_id,purchase_order_id) REFERENCES purchase_orders(org_id,id),
      FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id),
      FOREIGN KEY(org_id,item_id) REFERENCES inventory_items(org_id,id),
      FOREIGN KEY(org_id,pack_id) REFERENCES item_packs(org_id,id),
      CHECK(ordered_packs>0 AND ordered_base_quantity_per_pack>0 AND ordered_quantity>0 AND ordered_unit_price_minor>=0),
      CHECK(received_quantity>=0 AND closed_quantity>=0)
    );
    CREATE TABLE receipts (
      id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organizations(id), location_id uuid NOT NULL,
      purchase_order_id uuid NOT NULL, received_by uuid NOT NULL REFERENCES users(id), received_at timestamptz NOT NULL DEFAULT now(),
      business_date date NOT NULL, idempotency_key varchar(128) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_receipts_org_id_id UNIQUE(org_id,id),
      CONSTRAINT uq_receipts_org_idempotency_key UNIQUE(org_id,idempotency_key),
      FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id),
      FOREIGN KEY(org_id,purchase_order_id) REFERENCES purchase_orders(org_id,id)
    );
    CREATE TABLE receipt_lines (
      id uuid PRIMARY KEY, org_id uuid NOT NULL, location_id uuid NOT NULL, receipt_id uuid NOT NULL,
      purchase_order_line_id uuid NOT NULL, item_id uuid NOT NULL, received_base_quantity numeric(18,6) NOT NULL,
      total_cost_minor bigint NOT NULL, item_name varchar NOT NULL, base_unit varchar NOT NULL,
      CONSTRAINT uq_receipt_lines_org_id UNIQUE(org_id,id), FOREIGN KEY(org_id,receipt_id) REFERENCES receipts(org_id,id),
      FOREIGN KEY(org_id,purchase_order_line_id) REFERENCES purchase_order_lines(org_id,id),
      FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id), FOREIGN KEY(org_id,item_id) REFERENCES inventory_items(org_id,id),
      CHECK(received_base_quantity>0 AND total_cost_minor>=0)
    );
    CREATE TABLE recipes (
      id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organizations(id), name varchar NOT NULL,
      yield_quantity numeric(18,6) NOT NULL, yield_unit varchar NOT NULL, active boolean NOT NULL DEFAULT true,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_recipes_org_id UNIQUE(org_id,id), CHECK(yield_quantity>0)
    );
    CREATE TABLE recipe_ingredients (
      id uuid PRIMARY KEY, org_id uuid NOT NULL, recipe_id uuid NOT NULL, item_id uuid NOT NULL,
      quantity_base numeric(18,6) NOT NULL, item_name varchar NOT NULL, base_unit varchar NOT NULL,
      CONSTRAINT uq_recipe_ingredients_org_id UNIQUE(org_id,recipe_id,item_id), FOREIGN KEY(org_id,recipe_id) REFERENCES recipes(org_id,id),
      FOREIGN KEY(org_id,item_id) REFERENCES inventory_items(org_id,id), CHECK(quantity_base>0)
    );
    ALTER TABLE menu_items ADD CONSTRAINT fk_menu_items_recipe_org FOREIGN KEY(org_id,recipe_id) REFERENCES recipes(org_id,id);
    ALTER TABLE menu_items ADD CONSTRAINT uq_menu_items_org_id_id UNIQUE(org_id,id);
    """
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO chefos_app")


def downgrade() -> None:
    op.execute("ALTER TABLE menu_items DROP CONSTRAINT fk_menu_items_recipe_org")
    op.execute("ALTER TABLE menu_items DROP CONSTRAINT uq_menu_items_org_id_id")
    for table in TABLES:
        op.execute(f"DROP TABLE {table} CASCADE")
