"""auth business logic: registration, OTP login, token rotation, consent, profile.

Flow (ON-1, ON-2):
    new user        register -> (code texted) -> otp/verify -> tokens -> consent
    returning user  otp/send -> login -> tokens
    every 30 min    refresh -> new access + refresh token (old refresh token revoked)

Services commit their own transactions, so an audit row is always written in the
same commit as the change it describes.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.audit import client_ip, write_audit
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.permissions import UserRole
from app.core.security import create_access_token, generate_refresh_token, hash_token, utcnow
from app.domains.auth import otp
from app.domains.auth.models import ConsentRecord, ConsentType, RefreshToken, User
from app.domains.auth.schemas import (
    ConsentRequest,
    ConsentStatus,
    RegisterRequest,
    UpdateMeRequest,
)
from app.integrations.sms import SMSClient

logger = logging.getLogger(__name__)

# Consents a user must give before onboarding is complete (ON-2). Health data is
# needed by anyone whose account will hold a patient's health records.
REQUIRED_CONSENTS: dict[UserRole, tuple[ConsentType, ...]] = {
    UserRole.PATIENT: (ConsentType.TERMS, ConsentType.HEALTH_DATA),
    UserRole.GUARDIAN: (ConsentType.TERMS, ConsentType.HEALTH_DATA),
    UserRole.FAMILY_ABROAD: (ConsentType.TERMS, ConsentType.HEALTH_DATA),
    UserRole.CAREGIVER: (ConsentType.TERMS,),
    UserRole.FACILITY_ADMIN: (ConsentType.TERMS,),
    UserRole.OPS: (ConsentType.TERMS,),
}
# Types granted through /auth/consent. Passport shares are consented per share on Day 9.
SELF_SERVICE_CONSENTS = frozenset(
    {ConsentType.TERMS, ConsentType.HEALTH_DATA, ConsentType.LOCATION, ConsentType.LLM_PROCESSING}
)


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int


def _user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    return request.headers.get("user-agent", "")[:512] or None


async def get_user_by_phone(db: AsyncSession, phone: str) -> User | None:
    return await db.scalar(select(User).where(User.phone == phone))


# ------------------------------------------------------------ registration
async def register(
    db: AsyncSession,
    redis: Redis,
    sms: SMSClient,
    data: RegisterRequest,
    request: Request | None = None,
) -> tuple[User, otp.OtpIssued]:
    """Create the account (unverified) and text a code to confirm the phone.

    A number that registered but never verified can register again: the earlier,
    unconfirmed details are replaced. A verified number gets 409.
    """
    user = await get_user_by_phone(db, data.phone)
    if user is not None and user.is_phone_verified:
        raise ConflictError(
            "An account already exists for this number. Log in instead.", code="phone_taken"
        )

    if user is None:
        user = User(
            phone=data.phone,
            full_name=data.full_name,
            role=data.role,
            email=data.email,
            preferred_language=data.preferred_language,
        )
        db.add(user)
        action = "auth.register"
    else:
        user.full_name = data.full_name
        user.role = data.role
        user.email = data.email
        user.preferred_language = data.preferred_language
        action = "auth.register_retry"

    write_audit(
        db,
        action=action,
        actor_id=user.id,
        entity_type="user",
        entity_id=user.id,
        request=request,
        metadata={"role": data.role.value},
    )
    try:
        await db.commit()
    except IntegrityError as exc:  # two sign-ups for the same number at once
        await db.rollback()
        raise ConflictError(
            "An account already exists for this number. Log in instead.", code="phone_taken"
        ) from exc
    await db.refresh(user)

    issued = await otp.send_otp(redis, sms, data.phone, client_ip=client_ip(request))
    return user, issued


# --------------------------------------------------------------------- OTP
async def request_otp(
    db: AsyncSession, redis: Redis, sms: SMSClient, phone: str, request: Request | None = None
) -> otp.OtpIssued:
    """Text a login code (or a fresh verification code) to an existing account."""
    user = await get_user_by_phone(db, phone)
    if user is None:
        raise NotFoundError(
            "No account uses this number. Register first.", code="account_not_found"
        )
    if not user.is_active:
        raise ForbiddenError("This account has been disabled.", code="account_disabled")
    return await otp.send_otp(redis, sms, phone, client_ip=client_ip(request))


async def verify_phone(
    db: AsyncSession, redis: Redis, phone: str, code: str, request: Request | None = None
) -> tuple[User, IssuedTokens]:
    """Confirm the phone after registration and sign the user in."""
    user = await _active_user_for_code(db, phone)
    await otp.verify_otp(redis, phone, code)

    newly_verified = not user.is_phone_verified
    now = utcnow()
    if newly_verified:
        user.phone_verified_at = now
        write_audit(
            db,
            action="auth.phone_verified",
            actor_id=user.id,
            entity_type="user",
            entity_id=user.id,
            request=request,
        )
    tokens = await _start_session(db, user, request)
    return user, tokens


async def login(
    db: AsyncSession, redis: Redis, phone: str, code: str, request: Request | None = None
) -> tuple[User, IssuedTokens]:
    """Sign in a returning user with the code from /auth/otp/send."""
    user = await _active_user_for_code(db, phone)
    if not user.is_phone_verified:
        raise ForbiddenError(
            "Confirm your phone number first.",
            code="phone_not_verified",
            details={"next": "/auth/otp/verify"},
        )
    await otp.verify_otp(redis, phone, code)
    tokens = await _start_session(db, user, request)
    return user, tokens


async def _active_user_for_code(db: AsyncSession, phone: str) -> User:
    user = await get_user_by_phone(db, phone)
    if user is None:
        raise NotFoundError(
            "No account uses this number. Register first.", code="account_not_found"
        )
    if not user.is_active:
        raise ForbiddenError("This account has been disabled.", code="account_disabled")
    return user


# ------------------------------------------------------------------ tokens
def _new_refresh_row(user: User, request: Request | None) -> tuple[RefreshToken, str]:
    settings = get_settings()
    raw = generate_refresh_token()
    row = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        user_agent=_user_agent(request),
    )
    return row, raw


def _issue(user: User, refresh_raw: str) -> IssuedTokens:
    settings = get_settings()
    access, _ = create_access_token(user.id, user.role.value)
    return IssuedTokens(
        access_token=access,
        refresh_token=refresh_raw,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_expires_in=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )


async def _start_session(db: AsyncSession, user: User, request: Request | None) -> IssuedTokens:
    row, raw = _new_refresh_row(user, request)
    db.add(row)
    user.last_login_at = utcnow()
    write_audit(
        db, action="auth.login", actor_id=user.id, entity_type="user", entity_id=user.id,
        request=request,
    )
    await db.commit()
    await db.refresh(user)
    return _issue(user, raw)


async def refresh_session(
    db: AsyncSession, refresh_token: str, request: Request | None = None
) -> tuple[User, IssuedTokens]:
    """Rotate: revoke the presented refresh token and issue a new pair.

    Presenting a token that was already rotated means it was copied (or the client
    retried after a lost response). Either way, every session for that user is
    revoked and they must log in again: the safe choice for health data.
    """
    row = await db.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_token(refresh_token))
        .with_for_update()
    )
    if row is None:
        raise UnauthorizedError("Your session is invalid. Log in again.", code="refresh_invalid")

    now = utcnow()
    if row.is_revoked:
        await _revoke_all(db, row.user_id, now)
        write_audit(
            db, action="auth.refresh_reuse", actor_id=row.user_id, entity_type="user",
            entity_id=row.user_id, request=request,
        )
        await db.commit()
        logger.warning("Refresh token reuse detected; all sessions revoked for user %s", row.user_id)
        raise UnauthorizedError(
            "Your session was used elsewhere and has been ended. Log in again.",
            code="refresh_reused",
        )
    if row.expires_at <= now:
        raise UnauthorizedError("Your session has expired. Log in again.", code="refresh_expired")

    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("This account is not active.", code="account_disabled")

    row.revoked_at = now
    new_row, raw = _new_refresh_row(user, request)
    db.add(new_row)
    await db.commit()
    return user, _issue(user, raw)


async def logout(db: AsyncSession, refresh_token: str, request: Request | None = None) -> None:
    """Revoke one refresh token. Idempotent: unknown or revoked tokens are ignored."""
    row = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token))
    )
    if row is None or row.is_revoked:
        return
    row.revoked_at = utcnow()
    write_audit(
        db, action="auth.logout", actor_id=row.user_id, entity_type="user",
        entity_id=row.user_id, request=request,
    )
    await db.commit()


async def _revoke_all(db: AsyncSession, user_id: uuid.UUID, now) -> None:  # noqa: ANN001
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


# ----------------------------------------------------------------- consent
async def record_consent(
    db: AsyncSession, user: User, data: ConsentRequest, request: Request | None = None
) -> ConsentRecord:
    settings = get_settings()
    current_version = settings.CONSENT_POLICY_VERSION

    if data.consent_type not in SELF_SERVICE_CONSENTS:
        raise BadRequestError(
            "Passport sharing is consented when a share is created.",
            code="consent_type_not_allowed",
        )
    if data.policy_version is not None and data.policy_version != current_version:
        raise ConflictError(
            "The policy has changed. Show the latest version and ask again.",
            code="policy_outdated",
            details={"current_version": current_version},
        )
    if data.patient_id is not None:
        # Guardian consent on behalf of a minor needs the patients table (Day 5) to
        # check the guardian really manages that patient. Until then, refuse.
        raise BadRequestError(
            "Consent for a patient profile is not available yet.",
            code="patient_consent_unavailable",
        )

    now = utcnow()
    latest = await _latest_consent(db, user.id, data.consent_type)

    if data.granted:
        if latest is not None and latest.granted and latest.policy_version == current_version:
            return latest  # already granted for this version: idempotent
    elif latest is not None and latest.granted and latest.revoked_at is None:
        latest.revoked_at = now

    record = ConsentRecord(
        user_id=user.id,
        patient_id=None,
        consent_type=data.consent_type,
        policy_version=current_version,
        granted=data.granted,
        granted_at=now,
        ip_address=client_ip(request),
    )
    db.add(record)
    write_audit(
        db,
        action="consent.grant" if data.granted else "consent.withdraw",
        actor_id=user.id,
        entity_type="consent_record",
        entity_id=record.id,
        request=request,
        metadata={"consent_type": data.consent_type.value, "policy_version": current_version},
    )
    await db.commit()
    return record


async def _latest_consent(
    db: AsyncSession, user_id: uuid.UUID, consent_type: ConsentType
) -> ConsentRecord | None:
    return await db.scalar(
        select(ConsentRecord)
        .where(
            ConsentRecord.user_id == user_id,
            ConsentRecord.consent_type == consent_type,
            ConsentRecord.patient_id.is_(None),
        )
        .order_by(ConsentRecord.granted_at.desc(), ConsentRecord.id.desc())
        .limit(1)
    )


async def has_consent(db: AsyncSession, user_id: uuid.UUID, consent_type: ConsentType) -> bool:
    """True if the user's latest decision for this type is a grant of the current policy."""
    latest = await _latest_consent(db, user_id, consent_type)
    return (
        latest is not None
        and latest.granted
        and latest.revoked_at is None
        and latest.policy_version == get_settings().CONSENT_POLICY_VERSION
    )


async def consent_status(db: AsyncSession, user: User) -> ConsentStatus:
    granted = {t: await has_consent(db, user.id, t) for t in SELF_SERVICE_CONSENTS}
    required = REQUIRED_CONSENTS.get(user.role, (ConsentType.TERMS,))
    return ConsentStatus(
        terms=granted[ConsentType.TERMS],
        health_data=granted[ConsentType.HEALTH_DATA],
        location=granted[ConsentType.LOCATION],
        llm_processing=granted[ConsentType.LLM_PROCESSING],
        policy_version=get_settings().CONSENT_POLICY_VERSION,
        missing_required=[t for t in required if not granted[t]],
    )


# ----------------------------------------------------------------- profile
async def update_me(
    db: AsyncSession, user: User, data: UpdateMeRequest, request: Request | None = None
) -> User:
    changes = data.model_dump(exclude_unset=True)
    # null is allowed only for email (to remove it); other fields ignore null.
    changes = {k: v for k, v in changes.items() if v is not None or k == "email"}
    if not changes:
        return user
    for field, value in changes.items():
        setattr(user, field, value)
    write_audit(
        db, action="user.update", actor_id=user.id, entity_type="user", entity_id=user.id,
        request=request, metadata={"fields": sorted(changes)},
    )
    await db.commit()
    await db.refresh(user)
    return user
