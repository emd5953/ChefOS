"""Audited inventory waste entries and stock movements.

Revision ID: 0011
Revises: 0010
"""
# ruff: noqa: E501

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT stock_movements_movement_type_check")
    op.execute(
        "ALTER TABLE stock_movements ADD CONSTRAINT stock_movements_movement_type_check CHECK(movement_type IN ('count','receipt','waste'))"
    )
    op.execute("""CREATE TABLE waste_entries (
      id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organizations(id), location_id uuid NOT NULL,
      item_id uuid NOT NULL, quantity_base numeric(18,6) NOT NULL, unit_cost_minor numeric(18,6),
      reason varchar NOT NULL, station_id uuid, shift_id uuid, business_date date NOT NULL,
      idempotency_key varchar(128) NOT NULL, created_by uuid NOT NULL REFERENCES users(id),
      created_at timestamptz NOT NULL DEFAULT now(), CONSTRAINT uq_waste_entries_org_id UNIQUE(org_id,id),
      CONSTRAINT uq_waste_entries_org_idempotency_key UNIQUE(org_id,idempotency_key),
      FOREIGN KEY(org_id,location_id) REFERENCES locations(org_id,id),
      FOREIGN KEY(org_id,item_id) REFERENCES inventory_items(org_id,id),
      CHECK(quantity_base>0 AND (unit_cost_minor IS NULL OR unit_cost_minor>=0))
    )""")
    op.execute("ALTER TABLE waste_entries ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY org_isolation ON waste_entries USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON waste_entries TO chefos_app")


def downgrade() -> None:
    op.execute("DROP TABLE waste_entries CASCADE")
    op.execute("DELETE FROM stock_movements WHERE movement_type='waste'")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT stock_movements_movement_type_check")
    op.execute(
        "ALTER TABLE stock_movements ADD CONSTRAINT stock_movements_movement_type_check CHECK(movement_type IN ('count','receipt'))"
    )
