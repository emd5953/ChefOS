"""Private Sous conversations and idempotent turns.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sous_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_attempt_id", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "user_id", "id", name="uq_sous_conversation_owner"),
        sa.ForeignKeyConstraint(["org_id", "location_id"], ["locations.org_id", "locations.id"]),
    )
    op.create_table(
        "sous_turns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("client_turn_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.String(), nullable=False),
        sa.Column("parent_version", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("answer", pg.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "user_id", "client_turn_id", name="uq_sous_turn_request"),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id", "conversation_id"],
            ["sous_conversations.org_id", "sous_conversations.user_id", "sous_conversations.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_sous_turns_history",
        "sous_turns",
        ["org_id", "user_id", "conversation_id", "parent_version"],
    )
    for table in ("sous_conversations", "sous_turns"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        scope = "org_id = app_org_id() AND user_id = app_user_id()"
        op.execute(f"CREATE POLICY owner_isolation ON {table} USING ({scope}) WITH CHECK ({scope})")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO chefos_app")


def downgrade() -> None:
    op.drop_table("sous_turns")
    op.drop_table("sous_conversations")
