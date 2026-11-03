"""team: employee shift trade requests

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shift_trade_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("offered_shift_id", sa.Uuid(), nullable=False),
        sa.Column("requested_shift_id", sa.Uuid(), nullable=False),
        sa.Column("requester_employee_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_employee_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), server_default="pending_recipient", nullable=False),
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
            "status IN ('pending_recipient', 'pending_manager', 'approved', 'denied', 'cancelled')",
            name="ck_shift_trade_requests_status",
        ),
        sa.CheckConstraint(
            "offered_shift_id <> requested_shift_id", name="ck_shift_trade_requests_distinct_shifts"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_shift_trade_requests_org_id_organizations"
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["locations.id"], name="fk_shift_trade_requests_location_id_locations"
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["schedules.id"],
            ondelete="CASCADE",
            name="fk_shift_trade_requests_schedule_id_schedules",
        ),
        sa.ForeignKeyConstraint(
            ["offered_shift_id"],
            ["published_shifts.id"],
            ondelete="CASCADE",
            name="fk_shift_trade_requests_offered_shift_id_published_shifts",
        ),
        sa.ForeignKeyConstraint(
            ["requested_shift_id"],
            ["published_shifts.id"],
            ondelete="CASCADE",
            name="fk_shift_trade_requests_requested_shift_id_published_shifts",
        ),
        sa.ForeignKeyConstraint(
            ["requester_employee_id"],
            ["staff_members.id"],
            name="fk_shift_trade_requests_requester_employee_id_staff_members",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_employee_id"],
            ["staff_members.id"],
            name="fk_shift_trade_requests_recipient_employee_id_staff_members",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name="fk_shift_trade_requests_decided_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_shift_trade_requests"),
    )
    op.create_index(
        "ix_shift_trade_requests_location_status", "shift_trade_requests", ["location_id", "status"]
    )
    op.create_index(
        "ix_shift_trade_requests_requester_status",
        "shift_trade_requests",
        ["requester_employee_id", "status"],
    )
    op.create_index(
        "ix_shift_trade_requests_recipient_status",
        "shift_trade_requests",
        ["recipient_employee_id", "status"],
    )
    op.create_index("ix_shift_trade_requests_created_at", "shift_trade_requests", ["created_at"])
    op.execute("ALTER TABLE shift_trade_requests ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY org_isolation ON shift_trade_requests "
        "USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())"
    )


def downgrade() -> None:
    op.drop_table("shift_trade_requests")
