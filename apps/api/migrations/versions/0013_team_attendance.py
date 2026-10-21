"""team: clock-in alerts (attendance)

Late and missed clock-in alerts reuse `notifications`; this index makes them one row per
recipient, shift, and kind, so the detector can insert with ON CONFLICT DO NOTHING on every
sync. No new table: attendance itself is computed from published shifts and timecards.

Revision ID: 0013
Revises: 0010
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_notifications_attendance",
        "notifications",
        ["user_id", "kind", "entity_id"],
        unique=True,
        postgresql_where=sa.text("kind IN ('shift_late', 'shift_missed')"),
    )


def downgrade() -> None:
    op.drop_index("uq_notifications_attendance", table_name="notifications")
