"""mapping issue resolution

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mapping_issues", sa.Column("resolution", sa.String(), nullable=True))
    op.add_column("mapping_issues", sa.Column("resolved_entity_id", sa.Uuid(), nullable=True))
    op.add_column(
        "mapping_issues", sa.Column("occurrences_at_resolve", sa.BigInteger(), nullable=True)
    )
    # Issues ignored before this migration: treat what they had then as the baseline.
    op.execute(
        "UPDATE mapping_issues SET resolution = 'ignored', occurrences_at_resolve = occurrences "
        "WHERE resolved_at IS NOT NULL"
    )
    op.create_check_constraint(
        "resolution",
        "mapping_issues",
        "resolution IS NULL OR resolution IN ('ignored', 'mapped')",
    )


def downgrade() -> None:
    op.drop_constraint("resolution", "mapping_issues", type_="check")
    op.drop_column("mapping_issues", "occurrences_at_resolve")
    op.drop_column("mapping_issues", "resolved_entity_id")
    op.drop_column("mapping_issues", "resolution")
