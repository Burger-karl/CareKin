"""Tokens and one-time codes.

- Access tokens are short-lived JWTs (HS256, signed with SECRET_KEY).
- Refresh tokens are opaque random strings. Only their SHA-256 hash is stored
  (refresh_tokens.token_hash), so a database leak does not leak usable tokens.
- OTPs are random digits. Only an HMAC of the code is kept in Redis.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import Settings, get_settings

ACCESS_TOKEN_TYPE = "access"
JWT_ISSUER = "carekin-api"


class TokenError(Exception):
    """The token is missing, malformed, expired or of the wrong type."""

    def __init__(self, reason: str) -> None:
        self.reason = reason  # "expired" or "invalid"
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    role: str
    expires_at: datetime
    jti: str


def utcnow() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ access
def create_access_token(
    user_id: uuid.UUID,
    role: str,
    *,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Return (token, expires_at)."""
    settings = settings or get_settings()
    now = now or utcnow()
    expires_at = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iss": JWT_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(
        payload, settings.SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM
    )
    return token, expires_at


def decode_access_token(token: str, *, settings: Settings | None = None) -> AccessTokenClaims:
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],  # never trust the header's alg
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid") from exc

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise TokenError("invalid")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError) as exc:
        raise TokenError("invalid") from exc
    return AccessTokenClaims(
        user_id=user_id,
        role=str(payload.get("role", "")),
        expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        jti=str(payload.get("jti", "")),
    )


# ----------------------------------------------------------------- refresh
def generate_refresh_token() -> str:
    """64 characters of URL-safe randomness (384 bits)."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """SHA-256 hex digest. Refresh tokens are high-entropy, so no salt is needed."""
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------- OTP
def generate_otp(length: int) -> str:
    """Uniformly random digits from the OS CSPRNG, leading zeros allowed."""
    return f"{secrets.randbelow(10**length):0{length}d}"


def hash_otp(phone: str, code: str, *, settings: Settings | None = None) -> str:
    """Keyed hash binding the code to the phone number.

    A 6-digit code has only a million values, so a plain hash would be trivial to
    reverse. Keying it with SECRET_KEY means a Redis dump alone reveals nothing.
    """
    settings = settings or get_settings()
    key = settings.SECRET_KEY.get_secret_value().encode()
    return hmac.new(key, f"otp:{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def otp_matches(expected_hash: str, phone: str, code: str) -> bool:
    return hmac.compare_digest(expected_hash, hash_otp(phone, code))
