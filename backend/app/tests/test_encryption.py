"""Tests: field-level encryption (core/encryption.py, PRD HP-4)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from app.config import Settings, get_settings
from app.core import encryption
from app.core.encryption import DecryptionError, EncryptedText, EncryptionKeyMissing


@pytest.fixture
def keys(monkeypatch):
    """Point the real key loader at fresh keys (not a stub), and clean up after."""

    def use(current: str | None, previous: list[str] | None = None) -> None:
        settings = get_settings()
        monkeypatch.setattr(
            settings, "FIELD_ENCRYPTION_KEY", SecretStr(current) if current else None
        )
        monkeypatch.setattr(
            settings, "FIELD_ENCRYPTION_PREVIOUS_KEYS", [SecretStr(k) for k in previous or []]
        )
        encryption.reset_key_cache()

    yield use
    encryption.reset_key_cache()


def new_key() -> str:
    return Fernet.generate_key().decode()


def test_round_trip_and_ciphertext_differs(keys):
    keys(new_key())
    column = EncryptedText()
    stored = column.process_bind_param("HbSS", None)
    assert b"HbSS" not in stored
    assert column.process_result_value(stored, None) == "HbSS"
    # Fernet uses a random IV: the same value never encrypts the same way twice.
    assert column.process_bind_param("HbSS", None) != stored


def test_none_passes_through(keys):
    keys(new_key())
    column = EncryptedText()
    assert column.process_bind_param(None, None) is None
    assert column.process_result_value(None, None) is None


def test_unicode_round_trip(keys):
    keys(new_key())
    note = "Ọ́ dị mma — 37.5°C"
    assert encryption.decrypt(encryption.encrypt(note)) == note


def test_missing_key_is_a_clear_error(keys):
    keys(None)
    with pytest.raises(EncryptionKeyMissing, match="FIELD_ENCRYPTION_KEY is not set"):
        encryption.encrypt("x")


def test_wrong_key_or_tampering_fails_loudly(keys):
    keys(new_key())
    stored = encryption.encrypt("secret")
    tampered = stored[:-2] + (b"A" if stored[-2:-1] != b"A" else b"B") + stored[-1:]
    with pytest.raises(DecryptionError):
        encryption.decrypt(tampered)
    keys(new_key())
    with pytest.raises(DecryptionError, match="decrypt"):
        encryption.decrypt(stored)


def test_rotation_reads_old_data_and_rewrites_with_new_key(keys):
    old, new = new_key(), new_key()
    keys(old)
    stored = encryption.encrypt("allergy: none")

    keys(new, previous=[old])
    assert encryption.decrypt(stored) == "allergy: none"  # old data still readable
    rotated = encryption.rotate(stored)

    keys(new)  # old key retired
    assert encryption.decrypt(rotated) == "allergy: none"
    with pytest.raises(DecryptionError):
        encryption.decrypt(stored)


def test_previous_keys_setting_is_parsed_and_validated():
    a, b = new_key(), new_key()
    settings = Settings(_env_file=None, FIELD_ENCRYPTION_PREVIOUS_KEYS=f"{a}, {b}")
    assert [k.get_secret_value() for k in settings.FIELD_ENCRYPTION_PREVIOUS_KEYS] == [a, b]
    assert (
        Settings(_env_file=None, FIELD_ENCRYPTION_PREVIOUS_KEYS="").FIELD_ENCRYPTION_PREVIOUS_KEYS
        == []
    )
    with pytest.raises(ValidationError, match="key 2 is not a Fernet key"):
        Settings(_env_file=None, FIELD_ENCRYPTION_PREVIOUS_KEYS=f"{a},not-a-key")


def test_patients_models_still_export_encrypted_text():
    from app.domains.patients.models import EncryptedText as FromPatients

    assert FromPatients is EncryptedText
