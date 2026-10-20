"""team: employees, positions, stations

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db import CIText

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_SCOPED = ("positions", "employee_positions", "stations", "employee_locations")


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["org_id"], ["organizations.id"], name=f"fk_{table}_org_id_organizations"
    )


def upgrade() -> None:
    # staff_members becomes the Employee: team-owned columns next to the POS-owned ones.
    op.add_column("staff_members", sa.Column("email", CIText(), nullable=True))
    op.add_column("staff_members", sa.Column("phone", sa.String(), nullable=True))
    op.add_column("staff_members", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.add_column("staff_members", sa.Column("rate_override", sa.BigInteger(), nullable=True))
    op.add_column(
        "staff_members",
        sa.Column("source", sa.String(), server_default="square", nullable=False),
    )
    op.add_column("staff_members", sa.Column("merged_into_id", sa.Uuid(), nullable=True))
    op.add_column(
        "staff_members",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_foreign_key(
        "fk_staff_members_user_id_users", "staff_members", "users", ["user_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_staff_members_merged_into_id_staff_members",
        "staff_members",
        "staff_members",
        ["merged_into_id"],
        ["id"],
    )
    op.create_unique_constraint("uq_staff_members_org_user", "staff_members", ["org_id", "user_id"])
    op.create_check_constraint("source", "staff_members", "source IN ('square', 'manual')")

    op.create_table(
        "positions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("color", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        _org_fk("positions"),
        sa.PrimaryKeyConstraint("id", name="pk_positions"),
    )
    op.create_index(
        "uq_positions_org_name", "positions", ["org_id", sa.text("lower(name)")], unique=True
    )
    op.create_table(
        "employee_positions",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("position_id", sa.Uuid(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _org_fk("employee_positions"),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["staff_members.id"],
            name="fk_employee_positions_employee_id_staff_members",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["position_id"], ["positions.id"], name="fk_employee_positions_position_id_positions"
        ),
        sa.PrimaryKeyConstraint("employee_id", "position_id", name="pk_employee_positions"),
    )
    op.create_index(
        "uq_employee_positions_primary",
        "employee_positions",
        ["employee_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.create_index("ix_employee_positions_position_id", "employee_positions", ["position_id"])
    op.create_table(
        "stations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        _org_fk("stations"),
        sa.ForeignKeyConstraint(
            ["location_id"], ["locations.id"], name="fk_stations_location_id_locations"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stations"),
    )
    op.create_index("ix_stations_location_id", "stations", ["location_id"])
    op.create_table(
        "employee_locations",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        _org_fk("employee_locations"),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["staff_members.id"],
            name="fk_employee_locations_employee_id_staff_members",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["locations.id"], name="fk_employee_locations_location_id_locations"
        ),
        sa.PrimaryKeyConstraint("employee_id", "location_id", name="pk_employee_locations"),
    )
    op.create_index("ix_employee_locations_location_id", "employee_locations", ["location_id"])

    for table in ORG_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} "
            "USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())"
        )


def downgrade() -> None:
    for table in reversed(ORG_SCOPED):
        op.drop_table(table)
    op.drop_constraint("source", "staff_members", type_="check")
    op.drop_constraint("uq_staff_members_org_user", "staff_members", type_="unique")
    op.drop_constraint(
        "fk_staff_members_merged_into_id_staff_members", "staff_members", type_="foreignkey"
    )
    op.drop_constraint("fk_staff_members_user_id_users", "staff_members", type_="foreignkey")
    for column in ("updated_at", "merged_into_id", "source", "rate_override", "user_id", "phone"):
        op.drop_column("staff_members", column)
    op.drop_column("staff_members", "email")
