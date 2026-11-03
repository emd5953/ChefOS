"""team: open shift coverage requests

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shift_coverage_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("published_shift_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'cancelled')",
            name="ck_shift_coverage_requests_status",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_shift_coverage_requests_org_id_organizations"
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="fk_shift_coverage_requests_location_id_locations",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["schedules.id"],
            ondelete="CASCADE",
            name="fk_shift_coverage_requests_schedule_id_schedules",
        ),
        sa.ForeignKeyConstraint(
            ["published_shift_id"],
            ["published_shifts.id"],
            ondelete="CASCADE",
            name="fk_shift_coverage_requests_published_shift_id_published_shifts",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["staff_members.id"],
            name="fk_shift_coverage_requests_employee_id_staff_members",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name="fk_shift_coverage_requests_decided_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_shift_coverage_requests"),
    )
    op.create_index(
        "ix_shift_coverage_requests_location_status",
        "shift_coverage_requests",
        ["location_id", "status"],
    )
    op.create_index(
        "ix_shift_coverage_requests_employee_status",
        "shift_coverage_requests",
        ["employee_id", "status"],
    )
    op.create_index(
        "uq_shift_coverage_requests_pending_shift",
        "shift_coverage_requests",
        ["published_shift_id", "employee_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.execute("ALTER TABLE shift_coverage_requests ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY org_isolation ON shift_coverage_requests "
        "USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())"
    )


def downgrade() -> None:
    op.drop_table("shift_coverage_requests")
