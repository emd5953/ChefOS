"""identity, sessions, audit, and row-level security

Revision ID: 0001
Revises:
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db import CIText

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables with an org_id column. Each gets RLS keyed on app.org_id.
ORG_SCOPED = ("memberships", "locations", "location_access", "audit_events")


def _ts(name: str, *, nullable: bool = False, default: bool = True) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if default else None,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # Unset/empty settings read as NULL, so an unscoped query matches nothing (fail closed).
    op.execute(
        "CREATE FUNCTION app_org_id() RETURNS uuid LANGUAGE sql STABLE AS "
        "$$ SELECT nullif(current_setting('app.org_id', true), '')::uuid $$"
    )
    op.execute(
        "CREATE FUNCTION app_user_id() RETURNS uuid LANGUAGE sql STABLE AS "
        "$$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$"
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", CIText(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        _ts("created_at"),
        _ts("disabled_at", nullable=True, default=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        _ts("created_at"),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'manager', 'employee')", name=op.f("ck_memberships_role")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_memberships_org_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memberships_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_table(
        "locations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("address", sa.String(), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("business_day_start", sa.Time(), nullable=False, server_default=sa.text("'04:00'")),
        _ts("created_at"),
        _ts("archived_at", nullable=True, default=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_locations_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_locations")),
    )
    op.create_index("ix_locations_org_id", "locations", ["org_id"])
    op.create_table(
        "location_access",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["location_id"], ["locations.id"], name=op.f("fk_location_access_location_id_locations")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_location_access_org_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_location_access_user_id_users")
        ),
        sa.PrimaryKeyConstraint("org_id", "user_id", "location_id", name=op.f("pk_location_access")),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("csrf_token", sa.String(), nullable=False),
        _ts("created_at"),
        _ts("last_seen_at"),
        _ts("expires_at", default=False),
        _ts("revoked_at", nullable=True, default=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_sessions_org_id_organizations")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("request_id", sa.String(), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_audit_events_actor_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["locations.id"], name=op.f("fk_audit_events_location_id_locations")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_audit_events_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index("ix_audit_events_org_id_created_at", "audit_events", ["org_id", "created_at"])

    # --- Row-level security ---
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY org_isolation ON organizations "
        "USING (id = app_org_id()) WITH CHECK (id = app_org_id())"
    )
    for table in ORG_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} "
            "USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())"
        )
    # Login runs before the org is known: let a user read their own memberships
    # (app.user_id is set only by the login path, after the password check).
    op.execute(
        "CREATE POLICY self_read ON memberships FOR SELECT USING (user_id = app_user_id())"
    )

    # audit_events is append-only for the app role.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM chefos_app")


def downgrade() -> None:
    for table in ("audit_events", "sessions", "location_access", "locations", "memberships"):
        op.drop_table(table)
    op.drop_table("organizations")
    op.drop_table("users")
    op.execute("DROP FUNCTION app_user_id()")
    op.execute("DROP FUNCTION app_org_id()")
