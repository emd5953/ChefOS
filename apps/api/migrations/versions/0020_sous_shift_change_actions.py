"""Allow Sous proposals to adjust or remove an existing published shift.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("sous_schedule_proposals", "payload", nullable=True)
    op.add_column(
        "sous_schedule_proposals",
        sa.Column("action", sa.String(), server_default="add", nullable=False),
    )
    op.add_column("sous_schedule_proposals", sa.Column("target_shift_id", sa.Uuid(), nullable=True))
    op.add_column(
        "sous_schedule_proposals",
        sa.Column("before_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        "ck_sous_schedule_proposals_action",
        "sous_schedule_proposals",
        "action IN ('add','update','remove')",
    )


def downgrade() -> None:
    op.execute("UPDATE sous_schedule_proposals SET payload='{}'::jsonb WHERE payload IS NULL")
    op.alter_column("sous_schedule_proposals", "payload", nullable=False)
    op.drop_constraint(
        "ck_sous_schedule_proposals_action", "sous_schedule_proposals", type_="check"
    )
    op.drop_column("sous_schedule_proposals", "before_snapshot")
    op.drop_column("sous_schedule_proposals", "target_shift_id")
    op.drop_column("sous_schedule_proposals", "action")
