"""team: availability and time off

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_SCOPED = ("availability_sets", "availability_blocks", "time_off_requests")


def _fk(table: str, column: str, target: str, ondelete: str | None = None) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table}_{column}_{target.split('.')[0]}", ondelete=ondelete
    )


def _decision(table: str) -> list[sa.SchemaItem]:
    return [
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(), nullable=True),
        _fk(table, "decided_by", "users.id"),
    ]


def upgrade() -> None:
    op.create_table(
        "availability_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("note", sa.String(), server_default="", nullable=False),
        sa.Column("submitted_by", sa.Uuid(), nullable=False),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        *_decision("availability_sets"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'superseded')",
            name="ck_availability_sets_status",
        ),
        _fk("availability_sets", "org_id", "organizations.id"),
        _fk("availability_sets", "employee_id", "staff_members.id"),
        _fk("availability_sets", "submitted_by", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_availability_sets"),
    )
    op.create_index(
        "ix_availability_sets_employee_id_status", "availability_sets", ["employee_id", "status"]
    )
    op.create_table(
        "availability_blocks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("set_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_availability_blocks_weekday"),
        sa.CheckConstraint(
            "(start_time IS NULL AND end_time IS NULL) OR "
            "(start_time IS NOT NULL AND end_time IS NOT NULL AND start_time < end_time)",
            name="ck_availability_blocks_window",
        ),
        _fk("availability_blocks", "org_id", "organizations.id"),
        _fk("availability_blocks", "set_id", "availability_sets.id", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_availability_blocks"),
    )
    op.create_index("ix_availability_blocks_set_id", "availability_blocks", ["set_id"])
    op.create_table(
        "time_off_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("reason", sa.String(), server_default="", nullable=False),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        *_decision("time_off_requests"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'cancelled')",
            name="ck_time_off_requests_status",
        ),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_time_off_requests_dates"),
        _fk("time_off_requests", "org_id", "organizations.id"),
        _fk("time_off_requests", "employee_id", "staff_members.id"),
        _fk("time_off_requests", "created_by", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_time_off_requests"),
    )
    op.create_index(
        "ix_time_off_requests_employee_id_starts_on",
        "time_off_requests",
        ["employee_id", "starts_on"],
    )
    op.create_index(
        "ix_time_off_requests_org_id_status", "time_off_requests", ["org_id", "status"]
    )
    for table in ORG_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} "
            "USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())"
        )


def downgrade() -> None:
    for table in ("time_off_requests", "availability_blocks", "availability_sets"):
        op.drop_table(table)
