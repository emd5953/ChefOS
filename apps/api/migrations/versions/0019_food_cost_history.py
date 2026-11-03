"""food cost: immutable recipe and menu mapping history

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "food_cost_baselines",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("org_id"),
    )
    op.create_table(
        "recipe_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("yield_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("yield_unit", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "effective_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["org_id", "recipe_id"], ["recipes.org_id", "recipes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id", name="uq_recipe_versions_org_id_id"),
        sa.UniqueConstraint("org_id", "recipe_id", "version", name="uq_recipe_versions_version"),
    )
    op.create_index(
        "ix_recipe_versions_effective", "recipe_versions", ["org_id", "recipe_id", "effective_at"]
    )
    op.create_table(
        "recipe_version_ingredients",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_version_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("quantity_base", sa.Numeric(18, 6), nullable=False),
        sa.Column("item_name", sa.String(), nullable=False),
        sa.Column("base_unit", sa.String(), nullable=False),
        sa.CheckConstraint("quantity_base > 0", name="recipe_version_quantity_positive"),
        sa.ForeignKeyConstraint(
            ["org_id", "recipe_version_id"],
            ["recipe_versions.org_id", "recipe_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "item_id"], ["inventory_items.org_id", "inventory_items.id"]
        ),
        sa.PrimaryKeyConstraint("recipe_version_id", "item_id"),
    )
    op.create_table(
        "menu_recipe_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("menu_item_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=True),
        sa.Column(
            "effective_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["org_id", "menu_item_id"], ["menu_items.org_id", "menu_items.id"]),
        sa.ForeignKeyConstraint(["org_id", "recipe_id"], ["recipes.org_id", "recipes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_menu_recipe_assignments_effective",
        "menu_recipe_assignments",
        ["org_id", "menu_item_id", "effective_at"],
    )

    # Migration time is the historical reporting baseline. Existing current state is explicitly
    # snapshotted here; older sales remain unavailable because no prior history exists.
    op.execute(
        "INSERT INTO food_cost_baselines(org_id,effective_at) "
        "SELECT id, transaction_timestamp() FROM organizations"
    )
    op.execute(
        "INSERT INTO recipe_versions(id,org_id,recipe_id,version,name,yield_quantity,"
        "yield_unit,active,effective_at) "
        "SELECT gen_random_uuid(),r.org_id,r.id,1,r.name,r.yield_quantity,r.yield_unit,"
        "r.active,transaction_timestamp() FROM recipes r"
    )
    op.execute(
        "INSERT INTO recipe_version_ingredients(org_id,recipe_version_id,item_id,"
        "quantity_base,item_name,base_unit) "
        "SELECT rv.org_id,rv.id,ri.item_id,ri.quantity_base,ri.item_name,ri.base_unit "
        "FROM recipe_versions rv JOIN recipe_ingredients ri "
        "ON ri.org_id=rv.org_id AND ri.recipe_id=rv.recipe_id"
    )
    op.execute(
        "INSERT INTO menu_recipe_assignments(id,org_id,menu_item_id,recipe_id,effective_at) "
        "SELECT gen_random_uuid(),m.org_id,m.id,m.recipe_id,transaction_timestamp() "
        "FROM menu_items m"
    )

    op.execute(
        "CREATE FUNCTION reject_food_cost_history_mutation() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
        "'food cost history is append-only'; END $$"
    )
    op.execute(
        "CREATE FUNCTION set_food_cost_baseline_for_new_org() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN INSERT INTO food_cost_baselines(org_id,effective_at) "
        "VALUES(NEW.id,transaction_timestamp()); RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER food_cost_baseline_new_org AFTER INSERT ON organizations "
        "FOR EACH ROW EXECUTE FUNCTION set_food_cost_baseline_for_new_org()"
    )
    for table in ("recipe_versions", "recipe_version_ingredients", "menu_recipe_assignments"):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_food_cost_history_mutation()"
        )

    for table in (
        "food_cost_baselines",
        "recipe_versions",
        "recipe_version_ingredients",
        "menu_recipe_assignments",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} USING (org_id = app_org_id()) "
            "WITH CHECK (org_id = app_org_id())"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO chefos_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS food_cost_baseline_new_org ON organizations")
    op.execute("DROP FUNCTION IF EXISTS set_food_cost_baseline_for_new_org()")
    op.drop_table("menu_recipe_assignments")
    op.drop_table("recipe_version_ingredients")
    op.drop_table("recipe_versions")
    op.drop_table("food_cost_baselines")
    op.execute("DROP FUNCTION reject_food_cost_history_mutation()")
