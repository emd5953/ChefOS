"""Settings, read from the environment (and the repo-root .env for host-side runs)."""

import base64
import binascii
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-root .env for host-side runs (tests, CLI). Absent inside the container, where Compose
# supplies the environment.
_ENV_FILE = next(
    (p / ".env" for p in Path(__file__).resolve().parents[:4] if (p / ".env").exists()), None
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "postgresql+asyncpg://chefos_app:chefos_app@localhost:5432/chefos"
    migrations_database_url: str = (
        "postgresql+asyncpg://chefos_owner:chefos_owner@localhost:5432/chefos"
    )
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "chefos"
    s3_secret_key: str = "chefos-dev-secret"  # noqa: S105
    s3_bucket: str = "chefos-raw"
    chefos_master_key: str = ""
    chefos_master_key_id: str = "local-1"
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_enabled: bool = True
    session_cookie_secure: bool = False
    env: Literal["local", "test"] = "local"

    square_mode: Literal["fake", "sandbox"] = "fake"
    square_app_id: str = ""
    square_app_secret: str = ""
    square_webhook_signature_key: str = ""
    web_origin: str = "http://localhost:3000"
    sync_interval_seconds: int = 60
    square_version: str = "2025-10-16"
    # Public base of the API as the browser sees it (Next rewrites /api/* to the API).
    public_api_base: str = ""
    # Notification URL registered with Square (used in webhook HMAC verification).
    square_webhook_url: str = ""

    # Sous assistant. ollama = local model (nothing leaves the machine); fake = scripted replies.
    sous_provider: Literal["ollama", "fake"] = "ollama"
    ollama_url: str = "http://host.docker.internal:11434"
    sous_model: str = "qwen3:8b"
    sous_timeout_seconds: int = 90

    @field_validator("chefos_master_key")
    @classmethod
    def _check_master_key(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "CHEFOS_MASTER_KEY is not set. Run `make up` (it generates one) or set a "
                "base64-encoded 32-byte key: openssl rand -base64 32"
            )
        try:
            raw = base64.b64decode(value, validate=True)
        except binascii.Error as exc:
            raise ValueError("CHEFOS_MASTER_KEY is not valid base64") from exc
        if len(raw) != 32:
            raise ValueError(f"CHEFOS_MASTER_KEY must decode to 32 bytes, got {len(raw)}")
        return value

    @property
    def api_base(self) -> str:
        return (self.public_api_base or f"{self.web_origin.rstrip('/')}/api").rstrip("/")

    @property
    def webhook_url(self) -> str:
        return self.square_webhook_url or f"{self.api_base}/v1/webhooks/square"

    @property
    def master_key_bytes(self) -> bytes:
        return base64.b64decode(self.chefos_master_key)


@lru_cache
def get_settings() -> Settings:
    """Load settings once. Exits the process with a clear message if they are invalid."""
    try:
        return Settings()
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']).upper()}: {e['msg']}" for e in exc.errors()
        )
        raise SystemExit(f"ChefOS configuration error: {details}") from None
