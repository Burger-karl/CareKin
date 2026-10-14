"""Unit tests for auth helpers: phone numbers, tokens, OTP hashing. No database needed."""

import uuid
from datetime import timedelta

import pytest

from app.core.phone import InvalidPhoneNumber, mask_phone, normalize_phone
from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    generate_otp,
    generate_refresh_token,
    hash_otp,
    hash_token,
    otp_matches,
)


@pytest.mark.parametrize(
    "raw",
    [
        "08031234567",
        "0803 123 4567",
        "0803-123-4567",
        "8031234567",
        "2348031234567",
        "+2348031234567",
        "+234 803 123 4567",
        "+234(0)8031234567",
        "002348031234567",
    ],
)
def test_nigerian_formats_normalise(raw):
    assert normalize_phone(raw) == "+2348031234567"


@pytest.mark.parametrize("raw", ["09051234567", "07011234567", "08101234567"])
def test_other_nigerian_prefixes(raw):
    assert normalize_phone(raw).startswith("+234")


def test_international_numbers_kept():
    assert normalize_phone("+44 7700 900123") == "+447700900123"
    assert normalize_phone("+1 (202) 555-0143") == "+12025550143"


@pytest.mark.parametrize(
    "raw", ["", "123", "06031234567", "+234603123456", "0803123456", "08031234567890", "+0123456789", "phone"]
)
def test_invalid_numbers(raw):
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone(raw)


def test_mask_phone():
    assert mask_phone("+2348031234567") == "+234803***4567"


def test_access_token_round_trip():
    user_id = uuid.uuid4()
    token, expires_at = create_access_token(user_id, "guardian")
    claims = decode_access_token(token)
    assert claims.user_id == user_id
    assert claims.role == "guardian"
    assert claims.expires_at == expires_at.replace(microsecond=0)


def test_expired_access_token():
    token, _ = create_access_token(uuid.uuid4(), "patient", expires_delta=timedelta(seconds=-1))
    with pytest.raises(TokenError) as exc:
        decode_access_token(token)
    assert exc.value.reason == "expired"


def test_alg_none_token_rejected():
    import jwt

    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "access", "iss": "carekin-api", "iat": 1, "exp": 4102444800},
        key=None,
        algorithm="none",
    )
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_refresh_tokens_are_random_and_hashed():
    a, b = generate_refresh_token(), generate_refresh_token()
    assert a != b and len(a) >= 60
    assert hash_token(a) != a and len(hash_token(a)) == 64


def test_otp_generation_and_matching():
    codes = {generate_otp(6) for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    assert len(codes) > 150  # random, not a fixed pattern
    h = hash_otp("+2348031234567", "123456")
    assert otp_matches(h, "+2348031234567", "123456")
    assert not otp_matches(h, "+2348031234567", "123457")
    assert not otp_matches(h, "+2348039999999", "123456")  # bound to the phone
