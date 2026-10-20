"""Envelope encryption behind a KeyProvider seam (local AES-GCM now, KMS-compatible later).

Envelope layout (versioned):
    v1 byte | key_id_len (1 byte) | key_id | wrapped_dk_len (2 bytes, big endian)
    | wrapped_dk (nonce 12 + AES-GCM(dk) 48) | nonce (12) | ciphertext+tag

The data key is wrapped with the master key, using the key_id as AAD so a wrapped key can't be
re-labeled. The caller's AAD (e.g. `org_id|connection_id`) binds ciphertext to its owner.
"""

import os
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 1
_NONCE = 12


class DecryptionError(Exception):
    """Raised for any failure to decrypt. Never carries partial plaintext."""


class KeyProvider(Protocol):
    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes: ...

    def decrypt(self, envelope: bytes, aad: bytes) -> bytes: ...


class LocalKeyProvider:
    def __init__(self, master_keys: dict[str, bytes], active_key_id: str) -> None:
        if active_key_id not in master_keys:
            raise ValueError(f"active key id {active_key_id!r} not in master keys")
        for key_id, key in master_keys.items():
            if len(key) != 32:
                raise ValueError(f"master key {key_id!r} must be 32 bytes")
            if not 0 < len(key_id.encode()) < 256:
                raise ValueError("key id must be 1-255 bytes")
        self._keys = master_keys
        self._active = active_key_id

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        key_id = self._active.encode()
        data_key = AESGCM.generate_key(bit_length=256)
        wrap_nonce = os.urandom(_NONCE)
        wrapped = wrap_nonce + AESGCM(self._keys[self._active]).encrypt(
            wrap_nonce, data_key, key_id
        )
        nonce = os.urandom(_NONCE)
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, aad)
        return (
            bytes([VERSION, len(key_id)])
            + key_id
            + len(wrapped).to_bytes(2, "big")
            + wrapped
            + nonce
            + ciphertext
        )

    def decrypt(self, envelope: bytes, aad: bytes) -> bytes:
        try:
            parsed = self.parse(envelope)
            master = self._keys.get(parsed.key_id)
            if master is None:
                raise DecryptionError(f"unknown key id {parsed.key_id!r}")
            wrap_nonce, wrapped_ct = parsed.wrapped_dk[:_NONCE], parsed.wrapped_dk[_NONCE:]
            data_key = AESGCM(master).decrypt(wrap_nonce, wrapped_ct, parsed.key_id.encode())
            return AESGCM(data_key).decrypt(parsed.nonce, parsed.ciphertext, aad)
        except DecryptionError:
            raise
        except (InvalidTag, ValueError, IndexError) as exc:
            raise DecryptionError("decryption failed") from exc

    @staticmethod
    def parse(envelope: bytes) -> Envelope:
        if len(envelope) < 2:
            raise DecryptionError("envelope too short")
        if envelope[0] != VERSION:
            raise DecryptionError(f"unsupported envelope version {envelope[0]}")
        pos = 2
        key_len = envelope[1]
        key_id = envelope[pos : pos + key_len].decode()
        pos += key_len
        wrapped_len = int.from_bytes(envelope[pos : pos + 2], "big")
        pos += 2
        wrapped = envelope[pos : pos + wrapped_len]
        pos += wrapped_len
        nonce = envelope[pos : pos + _NONCE]
        ciphertext = envelope[pos + _NONCE :]
        if len(wrapped) != wrapped_len or len(nonce) != _NONCE or len(ciphertext) < 16:
            raise DecryptionError("malformed envelope")
        return Envelope(VERSION, key_id, wrapped, nonce, ciphertext)


class Envelope:
    def __init__(
        self, version: int, key_id: str, wrapped_dk: bytes, nonce: bytes, ciphertext: bytes
    ) -> None:
        self.version = version
        self.key_id = key_id
        self.wrapped_dk = wrapped_dk
        self.nonce = nonce
        self.ciphertext = ciphertext


def get_key_provider() -> KeyProvider:
    """The configured provider (LocalKeyProvider from CHEFOS_MASTER_KEY)."""
    from app.config import get_settings

    s = get_settings()
    return LocalKeyProvider({s.chefos_master_key_id: s.master_key_bytes}, s.chefos_master_key_id)
