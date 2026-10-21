"""identity API keys and push idempotency records

Revision ID: 0012
Revises: 0010
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql = """
    CREATE TABLE api_keys (
      id uuid PRIMARY KEY,
      org_id uuid NOT NULL REFERENCES organizations(id),
      key_hash varchar NOT NULL,
      prefix varchar(16) NOT NULL,
      scopes varchar[] NOT NULL,
      location_id uuid,
      created_by uuid NOT NULL REFERENCES users(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz,
      revoked_at timestamptz,
      CONSTRAINT ck_api_keys_scopes CHECK (
        cardinality(scopes) >= 1
        AND scopes <@ ARRAY['orders:write', 'daily_sales:write']::varchar[]
      ),
      CONSTRAINT fk_api_keys_org_location
        FOREIGN KEY(org_id, location_id) REFERENCES locations(org_id, id),
      CONSTRAINT uq_api_keys_key_hash UNIQUE(key_hash),
      CONSTRAINT uq_api_keys_org_id_id UNIQUE(org_id,id)
    );
    CREATE INDEX ix_api_keys_org_id ON api_keys(org_id);
    CREATE TABLE push_idempotency (
      id uuid PRIMARY KEY,
      org_id uuid NOT NULL REFERENCES organizations(id),
      api_key_id uuid NOT NULL,
      idempotency_key varchar(128) NOT NULL,
      request_hash varchar NOT NULL,
      response jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_push_idempotency_org_key UNIQUE(org_id,idempotency_key),
      CONSTRAINT fk_push_idempotency_api_key
        FOREIGN KEY(org_id,api_key_id) REFERENCES api_keys(org_id,id)
    );
    """
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)
    for table in ("api_keys", "push_idempotency"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} USING (org_id = app_org_id()) "
            f"WITH CHECK (org_id = app_org_id())"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO chefos_app")
    # Push requests arrive with only a bearer secret. Like invitation_for_token, this narrow
    # SECURITY DEFINER lookup is the only way around RLS: it maps the SHA-256 of a live key to
    # (key, org) and returns nothing for unknown, revoked, or expired keys.
    op.execute(
        "CREATE FUNCTION api_key_owner(p_hash text) RETURNS TABLE(id uuid, org_id uuid) "
        "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$ "
        "SELECT k.id,k.org_id FROM api_keys k WHERE k.key_hash=p_hash "
        "AND k.revoked_at IS NULL AND (k.expires_at IS NULL OR k.expires_at > now()) $$"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION api_key_owner(text)")
    op.execute("DROP TABLE push_idempotency CASCADE")
    op.execute("DROP TABLE api_keys CASCADE")
