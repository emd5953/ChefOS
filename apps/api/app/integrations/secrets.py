"""Envelope-encrypted connection credentials (AAD = org_id|connection_id)."""

import json
import uuid
from datetime import datetime

from app.integrations.providers.square.types import Credentials
from app.security.keys import KeyProvider, get_key_provider


def _aad(org_id: uuid.UUID, connection_id: uuid.UUID) -> bytes:
    return f"{org_id}|{connection_id}".encode()


def encrypt_credentials(
    creds: Credentials,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    keys: KeyProvider | None = None,
) -> bytes:
    body = json.dumps(
        {
            "access_token": creds.access_token,
            "refresh_token": creds.refresh_token,
            "expires_at": creds.expires_at.isoformat(),
            "merchant_id": creds.merchant_id,
        }
    ).encode()
    return (keys or get_key_provider()).encrypt(body, _aad(org_id, connection_id))


def decrypt_credentials(
    ciphertext: bytes,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    keys: KeyProvider | None = None,
) -> Credentials:
    data = json.loads((keys or get_key_provider()).decrypt(ciphertext, _aad(org_id, connection_id)))
    return Credentials(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=datetime.fromisoformat(data["expires_at"]),
        merchant_id=data["merchant_id"],
    )
