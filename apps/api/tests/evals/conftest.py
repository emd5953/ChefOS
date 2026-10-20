"""The case-file tests need no database; skip the schema reset that tests/conftest.py applies."""

import pytest


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    return None
