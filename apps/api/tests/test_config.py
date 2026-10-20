import base64

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def _settings(**kw: str) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_rejects_missing_master_key() -> None:
    with pytest.raises(ValidationError, match="CHEFOS_MASTER_KEY is not set"):
        _settings(chefos_master_key="")


def test_rejects_wrong_length_master_key() -> None:
    with pytest.raises(ValidationError, match="32 bytes"):
        _settings(chefos_master_key=base64.b64encode(b"short").decode())


def test_rejects_non_base64_master_key() -> None:
    with pytest.raises(ValidationError, match="base64"):
        _settings(chefos_master_key="***not base64***")


def test_get_settings_exits_with_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEFOS_MASTER_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit, match="CHEFOS_MASTER_KEY"):
            get_settings()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_square_and_web_defaults() -> None:
    s = _settings(chefos_master_key=base64.b64encode(b"k" * 32).decode())
    assert s.square_mode == "fake"
    assert s.web_origin == "http://localhost:3000"
    assert s.sync_interval_seconds == 60
    assert (
        s.square_app_id == "" and s.square_app_secret == "" and s.square_webhook_signature_key == ""
    )
