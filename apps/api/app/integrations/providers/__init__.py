"""Connector registry: `get_connector(provider)` returns the process-wide connector."""

from functools import lru_cache

from app.config import get_settings
from app.integrations.providers.square.connector import SquareConnector


@lru_cache
def _square(mode: str) -> SquareConnector:
    s = get_settings()
    if mode == "sandbox":
        from app.integrations.providers.square.http_client import HttpSquareClient

        return SquareConnector(
            HttpSquareClient(s.square_app_id, s.square_app_secret, square_version=s.square_version),
            mode="sandbox",
            signature_key=s.square_webhook_signature_key,
        )
    from app.integrations.providers.square.fake_client import FakeSquareClient

    return SquareConnector(FakeSquareClient(), mode="fake")


_override: SquareConnector | None = None


def set_connector_override(connector: SquareConnector | None) -> None:
    """Tests inject a connector (for example a fake seller with a controllable clock)."""
    global _override
    _override = connector


def get_connector(provider: str = "square") -> SquareConnector:
    if provider != "square":
        raise ValueError(f"unknown provider {provider!r}")
    return _override or _square(get_settings().square_mode)
