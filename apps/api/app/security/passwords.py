import asyncio
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 12
_hasher = PasswordHasher()  # Argon2id, library defaults


@lru_cache
def _common() -> frozenset[str]:
    path = Path(__file__).with_name("common_passwords.txt")
    return frozenset(line.strip().lower() for line in path.read_text().splitlines() if line.strip())


def validate_password(password: str, *, email: str | None = None) -> str | None:
    """Return an error message, or None if the password is acceptable."""
    if len(password) < MIN_LENGTH:
        return f"Must be at least {MIN_LENGTH} characters"
    if password.lower() in _common():
        return "This password is too common"
    if email and password.lower() == email.lower():
        return "Must not be the same as your email"
    if len(set(password)) < 4:
        return "Too repetitive"
    return None


def hash_password_sync(password: str) -> str:
    return _hasher.hash(password)


def verify_password_sync(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


# Verified against when the email is unknown, so timing doesn't reveal which emails exist.
DUMMY_HASH = hash_password_sync("chefos-dummy-password-for-timing")


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(hash_password_sync, password)


async def verify_password(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(verify_password_sync, password_hash, password)
