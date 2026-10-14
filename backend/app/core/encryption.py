"""Field-level encryption (health passport data)."""
"""Field-level encryption for health data (PRD HP-4, ERD conventions).

Health fields are encrypted in the application before they reach Postgres, so a
database dump, a replica or a backup alone reveals nothing. Use the column type:

    from app.core.encryption import EncryptedText

    allergies: Mapped[str | None] = mapped_column(EncryptedText)

Encrypted columns are stored as `bytea` and can't be filtered, sorted or indexed in
SQL. In migrations write them as `sa.LargeBinary()`; never import this module there.

Keys (Fernet: AES-128-CBC + HMAC-SHA256, with a timestamp):
    FIELD_ENCRYPTION_KEY             encrypts everything new. Required in staging/production.
    FIELD_ENCRYPTION_PREVIOUS_KEYS   comma-separated old keys, still accepted for reading.

To rotate: generate a new key, move the current one into FIELD_ENCRYPTION_PREVIOUS_KEYS,
deploy, re-encrypt existing rows with `rotate()`, then drop the old key.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import LargeBinary
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

GENERATE_KEY_HINT = (
    'Generate one: python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)


class EncryptionKeyMissing(RuntimeError):
    """FIELD_ENCRYPTION_KEY isn't set."""


class DecryptionError(RuntimeError):
    """Stored data doesn't match any configured key, or was tampered with."""


@lru_cache
def _fernet() -> MultiFernet:
    """The current key first (used to encrypt), then previous keys (decrypt only)."""
    settings = get_settings()
    if settings.FIELD_ENCRYPTION_KEY is None:
        raise EncryptionKeyMissing(
            "FIELD_ENCRYPTION_KEY is not set, so health data can't be encrypted. "
            + GENERATE_KEY_HINT
        )
    keys = [settings.FIELD_ENCRYPTION_KEY, *settings.FIELD_ENCRYPTION_PREVIOUS_KEYS]
    return MultiFernet([Fernet(key.get_secret_value().encode()) for key in keys])


def reset_key_cache() -> None:
    """Forget the cached keys, e.g. after changing settings in tests."""
    _fernet.cache_clear()


def encrypt(value: str) -> bytes:
    return _fernet().encrypt(value.encode())


def decrypt(token: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(token)).decode()
    except InvalidToken as exc:
        raise DecryptionError(
            "Could not decrypt a health field: no configured FIELD_ENCRYPTION_KEY matches "
            "the key it was written with, or the stored value was altered"
        ) from exc


def rotate(token: bytes) -> bytes:
    """Re-encrypt a stored value with the current key (for key rotation jobs)."""
    try:
        return _fernet().rotate(bytes(token))
    except InvalidToken as exc:
        raise DecryptionError("Could not rotate a value: no configured key matches") from exc


class EncryptedText(TypeDecorator[str]):
    """Text encrypted with Fernet before it reaches Postgres (stored as `bytea`)."""

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        return encrypt(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return decrypt(value)
