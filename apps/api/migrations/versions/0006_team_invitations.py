"""team: invitations

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db import CIText

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("email", CIText(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=True),
        sa.Column("location_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_user_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('manager', 'employee')", name="ck_invitations_role"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_invitations_org_id_organizations"
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["staff_members.id"], name="fk_invitations_employee_id_staff_members"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_invitations_created_by_users"),
        sa.ForeignKeyConstraint(
            ["accepted_user_id"], ["users.id"], name="fk_invitations_accepted_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )
    op.create_index("ix_invitations_org_id_created_at", "invitations", ["org_id", "created_at"])
    op.create_index("ix_invitations_employee_id", "invitations", ["employee_id"])
    op.execute("ALTER TABLE invitations ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY org_isolation ON invitations "
        "USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())"
    )
    # Accepting an invitation happens before any org scope exists. Like
    # integration_connection_for_account, this narrow SECURITY DEFINER lookup is the only way
    # around RLS: it maps a live token hash to (invitation, org) and returns nothing else, and
    # nothing at all for expired, revoked, or accepted invitations.
    op.execute(
        "CREATE FUNCTION invitation_for_token(p_hash text) "
        "RETURNS TABLE (invitation_id uuid, org_id uuid) LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path = public AS $$ SELECT i.id, i.org_id FROM invitations i "
        "WHERE i.token_hash = p_hash AND i.accepted_at IS NULL AND i.revoked_at IS NULL "
        "AND i.expires_at > now() $$"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION invitation_for_token(text)")
    op.drop_table("invitations")
