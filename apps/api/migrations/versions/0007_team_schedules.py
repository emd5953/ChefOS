"""team: schedules, shifts, published shifts, notifications

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_SCOPED = ("schedules", "shifts", "published_shifts", "notifications")


def _ts(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if default else None,
    )


def _fk(table: str, column: str, target: str, ondelete: str | None = None) -> sa.ForeignKeyConstraint:
    ref_table = target.split(".")[0]
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table}_{column}_{ref_table}", ondelete=ondelete
    )


def _shift_columns(table: str) -> list[sa.SchemaItem]:
    return [
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=True),
        sa.Column("position_id", sa.Uuid(), nullable=False),
        sa.Column("station_id", sa.Uuid(), nullable=True),
        sa.Column("business_date", sa.Date(), nullable=False),
        _ts("starts_at"),
        _ts("ends_at"),
        sa.Column("break_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("notes", sa.String(), server_default="", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _fk(table, "org_id", "organizations.id"),
        _fk(table, "location_id", "locations.id"),
        _fk(table, "schedule_id", "schedules.id", "CASCADE"),
        _fk(table, "employee_id", "staff_members.id"),
        _fk(table, "position_id", "positions.id"),
        _fk(table, "station_id", "stations.id"),
        _fk(table, "created_by", "users.id"),
    ]


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        _ts("published_at", nullable=True),
        sa.Column("published_by", sa.Uuid(), nullable=True),
        _ts("updated_at", default=True),
        sa.CheckConstraint(
            "extract(isodow from week_start) = 1", name="ck_schedules_week_start_monday"
        ),
        _fk("schedules", "org_id", "organizations.id"),
        _fk("schedules", "location_id", "locations.id"),
        _fk("schedules", "published_by", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_schedules"),
        sa.UniqueConstraint("location_id", "week_start", name="uq_schedules_location_week"),
    )
    op.create_index("ix_schedules_org_id", "schedules", ["org_id"])
    op.create_table(
        "shifts",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_shift_columns("shifts"),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.CheckConstraint("ends_at > starts_at", name="ck_shifts_ends_after_start"),
        sa.CheckConstraint("break_minutes >= 0", name="ck_shifts_break_minutes"),
        sa.PrimaryKeyConstraint("id", name="pk_shifts"),
    )
    op.create_index("ix_shifts_schedule_id", "shifts", ["schedule_id"])
    op.create_index("ix_shifts_employee_id_starts_at", "shifts", ["employee_id", "starts_at"])
    op.create_table(
        "published_shifts",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_shift_columns("published_shifts"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("published_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_published_shifts"),
    )
    op.create_index("ix_published_shifts_schedule_id", "published_shifts", ["schedule_id"])
    op.create_index(
        "ix_published_shifts_employee_id_starts_at",
        "published_shifts",
        ["employee_id", "starts_at"],
    )
    op.create_index(
        "ix_published_shifts_location_date", "published_shifts", ["location_id", "business_date"]
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), server_default="", nullable=False),
        sa.Column("link", sa.String(), nullable=True),
        sa.Column("entity_type", sa.String(), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        _ts("created_at", default=True),
        _ts("read_at", nullable=True),
        _fk("notifications", "org_id", "organizations.id"),
        _fk("notifications", "user_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
    )
    op.create_index("ix_notifications_user_id_read_at", "notifications", ["user_id", "read_at"])

    for table in ORG_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} "
            "USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())"
        )


def downgrade() -> None:
    for table in ("notifications", "published_shifts", "shifts", "schedules"):
        op.drop_table(table)
