"""Structural guardrails: scope rules, schema/model drift, passwords."""

import ast
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import AsyncEngine

import app.models  # noqa: F401
from app.db import Base
from app.security.passwords import validate_password

APP_DIR = Path(__file__).resolve().parents[1] / "app"
UNSCOPED_ALLOWED = {
    Path("db.py"),
    Path("security/sessions.py"),
    Path("identity/service.py"),
}


def test_unscoped_session_is_only_imported_by_auth_code() -> None:
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        rel = path.relative_to(APP_DIR)
        if rel in UNSCOPED_ALLOWED:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            if "unscoped_session" in names:
                offenders.append(str(rel))
    assert not offenders, f"unscoped_session used outside the auth code paths: {offenders}"


async def test_models_match_migrations(owner_engine: AsyncEngine) -> None:
    """Autogenerate must see no drift between app.models and the migrated schema."""

    def diff(sync_conn):  # type: ignore[no-untyped-def]
        ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})
        return compare_metadata(ctx, Base.metadata)

    async with owner_engine.connect() as conn:
        result = await conn.run_sync(diff)
    assert result == []


def test_password_policy() -> None:
    assert validate_password("short") is not None
    assert validate_password("password1234") is not None
    assert validate_password("PASSWORD1234") is not None
    assert validate_password("correct-horse-battery-9") is None
