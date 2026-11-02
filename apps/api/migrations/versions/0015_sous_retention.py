"""Bound Sous history retention and expose a narrow, bounded cleanup operation.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sous_conversations",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("""
        UPDATE sous_conversations SET expires_at = created_at +
            CASE WHEN version > 0 THEN interval '30 days' ELSE interval '24 hours' END
    """)
    op.alter_column(
        "sous_conversations", "expires_at", nullable=False,
        server_default=sa.text("now() + interval '24 hours'"),
    )
    op.create_index("ix_sous_conversations_expiry", "sous_conversations", ["expires_at", "id"])
    # The worker uses chefos_app, which cannot enumerate other users' private histories.
    # This definer function accepts only a bounded batch size, never a scope or cutoff.
    # Schema-qualified tables and a locked search_path prevent name-shadowing attacks.
    op.execute("""
        CREATE FUNCTION public.purge_expired_sous_conversations(batch_size integer DEFAULT 100)
        RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE removed integer;
        BEGIN
            IF batch_size IS NULL OR batch_size < 1 OR batch_size > 1000 THEN
                RAISE EXCEPTION 'batch_size must be between 1 and 1000';
            END IF;
            WITH expired AS (
                SELECT id FROM public.sous_conversations
                WHERE expires_at <= statement_timestamp()
                  AND (lease_until IS NULL OR lease_until <= statement_timestamp())
                ORDER BY expires_at, id
                LIMIT batch_size FOR UPDATE SKIP LOCKED
            )
            DELETE FROM public.sous_conversations c USING expired e WHERE c.id = e.id;
            GET DIAGNOSTICS removed = ROW_COUNT;
            RETURN removed;
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.purge_expired_sous_conversations(integer) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.purge_expired_sous_conversations(integer) TO chefos_app"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION public.purge_expired_sous_conversations(integer)")
    op.drop_index("ix_sous_conversations_expiry", table_name="sous_conversations")
    op.drop_column("sous_conversations", "expires_at")
