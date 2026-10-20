import pytest

from app.security.keys import DecryptionError, LocalKeyProvider, get_key_provider

MASTER = b"m" * 32
AAD = b"org-1|conn-1"


def _provider(key_id: str = "k1") -> LocalKeyProvider:
    return LocalKeyProvider({key_id: MASTER}, key_id)


def test_round_trip() -> None:
    p = _provider()
    envelope = p.encrypt(b"oauth-token", AAD)
    assert b"oauth-token" not in envelope
    assert p.decrypt(envelope, AAD) == b"oauth-token"


def test_each_encryption_uses_a_fresh_data_key() -> None:
    p = _provider()
    assert p.encrypt(b"same", AAD) != p.encrypt(b"same", AAD)


def test_empty_plaintext_round_trips() -> None:
    p = _provider()
    assert p.decrypt(p.encrypt(b"", AAD), AAD) == b""


def test_wrong_aad_fails() -> None:
    p = _provider()
    with pytest.raises(DecryptionError):
        p.decrypt(p.encrypt(b"secret", AAD), b"org-2|conn-1")


def test_tampering_anywhere_fails() -> None:
    p = _provider()
    envelope = p.encrypt(b"secret", AAD)
    for i in range(2, len(envelope)):  # skip version/len bytes (covered separately)
        bad = bytearray(envelope)
        bad[i] ^= 0x01
        with pytest.raises(DecryptionError):
            p.decrypt(bytes(bad), AAD)


def test_truncated_or_garbage_envelope_fails() -> None:
    p = _provider()
    envelope = p.encrypt(b"secret", AAD)
    for bad in (b"", b"\x01", envelope[:10], envelope[:-1], b"\x01\x05abc"):
        with pytest.raises(DecryptionError):
            p.decrypt(bad, AAD)


def test_wrong_key_id_fails() -> None:
    envelope = _provider("k1").encrypt(b"secret", AAD)
    with pytest.raises(DecryptionError, match="unknown key id"):
        _provider("k2").decrypt(envelope, AAD)


def test_same_key_id_different_master_key_fails() -> None:
    envelope = _provider().encrypt(b"secret", AAD)
    other = LocalKeyProvider({"k1": b"x" * 32}, "k1")
    with pytest.raises(DecryptionError):
        other.decrypt(envelope, AAD)


def test_envelope_version_parsing() -> None:
    p = _provider("key-7")
    envelope = p.encrypt(b"secret", AAD)
    parsed = LocalKeyProvider.parse(envelope)
    assert parsed.version == 1
    assert parsed.key_id == "key-7"
    assert envelope[0] == 1
    future = bytes([2]) + envelope[1:]
    with pytest.raises(DecryptionError, match="unsupported envelope version"):
        p.decrypt(future, AAD)


def test_rotation_old_envelopes_still_decrypt() -> None:
    old = LocalKeyProvider({"k1": MASTER}, "k1").encrypt(b"secret", AAD)
    rotated = LocalKeyProvider({"k1": MASTER, "k2": b"n" * 32}, "k2")
    assert rotated.decrypt(old, AAD) == b"secret"
    assert LocalKeyProvider.parse(rotated.encrypt(b"x", AAD)).key_id == "k2"


def test_rejects_bad_master_key_length() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        LocalKeyProvider({"k1": b"short"}, "k1")


def test_configured_provider_uses_settings() -> None:
    p = get_key_provider()
    assert LocalKeyProvider.parse(p.encrypt(b"x", AAD)).key_id == "test-1"
