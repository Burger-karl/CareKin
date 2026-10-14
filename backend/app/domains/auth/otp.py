"""One-time codes over SMS, stored in Redis (ERD 2.1: never in Postgres).

Redis keys (all expire on their own):
    otp:code:{phone}      hash {h: hmac of the code, attempts: wrong guesses}  TTL OTP_EXPIRE_SECONDS
    otp:cooldown:{phone}  set while a resend is not yet allowed                TTL OTP_RESEND_COOLDOWN_SECONDS
    otp:sends:{phone}     codes sent in the current hour                       TTL 1 hour
    otp:ip:{ip}           codes requested from one IP in the current hour       TTL 1 hour

A new code replaces the previous one. A code is single-use and is burned after
OTP_MAX_ATTEMPTS wrong guesses, so brute-forcing a 6-digit code is not practical.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings, get_settings
from app.core.exceptions import BadRequestError, RateLimitedError, ServiceUnavailableError
from app.core.phone import mask_phone
from app.core.security import generate_otp, hash_otp, otp_matches
from app.integrations.sms import SMSClient

logger = logging.getLogger(__name__)

HOUR = 3600


def _code_key(phone: str) -> str:
    return f"otp:code:{phone}"


def _cooldown_key(phone: str) -> str:
    return f"otp:cooldown:{phone}"


def _sends_key(phone: str) -> str:
    return f"otp:sends:{phone}"


def _ip_key(ip: str) -> str:
    return f"otp:ip:{ip}"


@dataclass(frozen=True, slots=True)
class OtpIssued:
    expires_in: int
    resend_after: int
    code: str  # returned to the API only when settings.expose_otp_in_response


def otp_message(code: str, minutes: int) -> str:
    # Plain ASCII keeps it to one SMS segment on every network.
    return (
        f"Your CareKin code is {code}. It expires in {minutes} minutes. "
        "Do not share it with anyone, including CareKin staff."
    )


async def _hit_hourly_limit(redis: Redis, key: str, limit: int, message: str) -> None:
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, HOUR)
    if count > limit:
        ttl = await redis.ttl(key)
        raise RateLimitedError(message, code="otp_rate_limited", retry_after=ttl if ttl > 0 else HOUR)


async def send_otp(
    redis: Redis,
    sms: SMSClient,
    phone: str,
    *,
    client_ip: str | None = None,
    settings: Settings | None = None,
) -> OtpIssued:
    """Generate, store and text a new code. Enforces cooldown and hourly limits."""
    settings = settings or get_settings()
    try:
        cooldown = settings.OTP_RESEND_COOLDOWN_SECONDS
        if cooldown > 0:
            # SET NX succeeds only if no cooldown is running for this phone.
            if not await redis.set(_cooldown_key(phone), "1", ex=cooldown, nx=True):
                ttl = await redis.ttl(_cooldown_key(phone))
                raise RateLimitedError(
                    "Please wait before requesting another code.",
                    code="otp_cooldown",
                    retry_after=ttl if ttl > 0 else cooldown,
                )
        await _hit_hourly_limit(
            redis,
            _sends_key(phone),
            settings.OTP_MAX_SENDS_PER_HOUR,
            "Too many codes requested for this number. Try again later.",
        )
        if client_ip:
            await _hit_hourly_limit(
                redis,
                _ip_key(client_ip),
                settings.OTP_MAX_SENDS_PER_IP_PER_HOUR,
                "Too many codes requested from this network. Try again later.",
            )

        code = generate_otp(settings.OTP_LENGTH)
        await redis.hset(_code_key(phone), mapping={"h": hash_otp(phone, code), "attempts": 0})
        await redis.expire(_code_key(phone), settings.OTP_EXPIRE_SECONDS)
    except RedisError as exc:
        logger.error("Redis unavailable while sending OTP: %s", type(exc).__name__)
        raise ServiceUnavailableError("Codes cannot be sent right now. Try again shortly.") from exc

    try:
        await sms.send(phone, otp_message(code, max(settings.OTP_EXPIRE_SECONDS // 60, 1)))
    except Exception as exc:
        # Let the user retry immediately rather than wait out a cooldown for a code
        # they never received.
        await redis.delete(_code_key(phone), _cooldown_key(phone))
        logger.error("SMS delivery failed for %s: %s", mask_phone(phone), type(exc).__name__)
        raise ServiceUnavailableError(
            "We could not send the code. Try again shortly.", code="sms_failed"
        ) from exc

    logger.info("OTP sent to %s", mask_phone(phone))
    return OtpIssued(
        expires_in=settings.OTP_EXPIRE_SECONDS,
        resend_after=settings.OTP_RESEND_COOLDOWN_SECONDS,
        code=code,
    )


async def verify_otp(
    redis: Redis, phone: str, code: str, *, settings: Settings | None = None
) -> None:
    """Consume the code for `phone`. Raises BadRequestError / RateLimitedError if it fails."""
    settings = settings or get_settings()
    key = _code_key(phone)
    try:
        stored = await redis.hget(key, "h")
        if stored is None:
            raise BadRequestError(
                "The code has expired or was never sent. Request a new one.", code="otp_expired"
            )
        # Count the attempt before checking, so parallel guesses all count.
        attempts = await redis.hincrby(key, "attempts", 1)
        if attempts > settings.OTP_MAX_ATTEMPTS:
            await redis.delete(key)
            raise RateLimitedError(
                "Too many wrong codes. Request a new one.", code="otp_locked"
            )
        if not otp_matches(stored, phone, code):
            remaining = settings.OTP_MAX_ATTEMPTS - attempts
            if remaining <= 0:
                await redis.delete(key)
                raise RateLimitedError(
                    "Too many wrong codes. Request a new one.", code="otp_locked"
                )
            raise BadRequestError(
                "The code is incorrect.",
                code="otp_invalid",
                details={"attempts_remaining": remaining},
            )
        # Single use: only the request that actually deletes the key succeeds.
        if not await redis.delete(key):
            raise BadRequestError(
                "The code has already been used. Request a new one.", code="otp_expired"
            )
    except RedisError as exc:
        logger.error("Redis unavailable while verifying OTP: %s", type(exc).__name__)
        raise ServiceUnavailableError("Codes cannot be checked right now. Try again shortly.") from exc
