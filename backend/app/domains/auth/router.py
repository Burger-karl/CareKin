"""auth routes (build plan Day 4, PRD ON-1 and ON-2).

POST  /auth/register      create an account and text a code
POST  /auth/otp/send      text a new code (resend, or login)
POST  /auth/otp/verify    confirm the phone after registering; returns tokens
POST  /auth/login         returning users: phone + code; returns tokens
POST  /auth/refresh       rotate tokens
POST  /auth/logout        revoke a refresh token (added)
POST  /auth/consent       record a consent decision
GET   /auth/me            current user, role and consent status
PATCH /auth/me            update own profile
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.config import get_settings
from app.core.redis import RedisClient
from app.database import DbSession
from app.domains.auth import service
from app.domains.auth.dependencies import CurrentUser
from app.domains.auth.otp import OtpIssued
from app.domains.auth.schemas import (
    ConsentOut,
    ConsentRequest,
    LoginRequest,
    LogoutRequest,
    MeOut,
    MessageOut,
    OtpSendRequest,
    OtpSentOut,
    OtpVerifyRequest,
    RefreshRequest,
    RegisterOut,
    RegisterRequest,
    TokenOut,
    UpdateMeRequest,
    UserOut,
)
from app.domains.auth.service import IssuedTokens
from app.domains.auth.models import User
from app.integrations.sms import SMSClient, get_sms_client

router = APIRouter(prefix="/auth", tags=["auth"])

ERRORS = {
    400: {"description": "Invalid or expired code"},
    401: {"description": "Not authenticated or session invalid"},
    403: {"description": "Not allowed"},
    404: {"description": "No account for this number"},
    409: {"description": "Conflict"},
    429: {"description": "Rate limited; see Retry-After"},
}


def _otp_out(issued: OtpIssued, message: str) -> dict:
    settings = get_settings()
    return {
        "message": message,
        "expires_in": issued.expires_in,
        "resend_after": issued.resend_after,
        "debug_code": issued.code if settings.expose_otp_in_response else None,
    }


def _token_out(user: User, tokens: IssuedTokens) -> TokenOut:
    return TokenOut(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        refresh_expires_in=tokens.refresh_expires_in,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/register",
    response_model=RegisterOut,
    status_code=status.HTTP_201_CREATED,
    responses={k: ERRORS[k] for k in (409, 429)},
    summary="Register with phone and role; texts a verification code",
)
async def register(
    body: RegisterRequest,
    request: Request,
    db: DbSession,
    redis: RedisClient,
    sms: SMSClient = Depends(get_sms_client),
) -> RegisterOut:
    user, issued = await service.register(db, redis, sms, body, request)
    return RegisterOut(
        user=UserOut.model_validate(user),
        **_otp_out(issued, "Account created. Enter the code we texted to confirm your number."),
    )


@router.post(
    "/otp/send",
    response_model=OtpSentOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={k: ERRORS[k] for k in (403, 404, 429)},
    summary="Text a new code to an existing account (resend or login)",
)
async def otp_send(
    body: OtpSendRequest,
    request: Request,
    db: DbSession,
    redis: RedisClient,
    sms: SMSClient = Depends(get_sms_client),
) -> OtpSentOut:
    issued = await service.request_otp(db, redis, sms, body.phone, request)
    return OtpSentOut(**_otp_out(issued, "Code sent."))


@router.post(
    "/otp/verify",
    response_model=TokenOut,
    responses={k: ERRORS[k] for k in (400, 403, 404, 429)},
    summary="Confirm the phone with the texted code; signs the user in",
)
async def otp_verify(
    body: OtpVerifyRequest, request: Request, db: DbSession, redis: RedisClient
) -> TokenOut:
    user, tokens = await service.verify_phone(db, redis, body.phone, body.code, request)
    return _token_out(user, tokens)


@router.post(
    "/login",
    response_model=TokenOut,
    responses={k: ERRORS[k] for k in (400, 403, 404, 429)},
    summary="Log in with phone and the code from /auth/otp/send",
)
async def login(body: LoginRequest, request: Request, db: DbSession, redis: RedisClient) -> TokenOut:
    user, tokens = await service.login(db, redis, body.phone, body.code, request)
    return _token_out(user, tokens)


@router.post(
    "/refresh",
    response_model=TokenOut,
    responses={401: ERRORS[401]},
    summary="Exchange a refresh token for a new access + refresh token",
)
async def refresh(body: RefreshRequest, request: Request, db: DbSession) -> TokenOut:
    user, tokens = await service.refresh_session(db, body.refresh_token, request)
    return _token_out(user, tokens)


@router.post("/logout", response_model=MessageOut, summary="Revoke a refresh token")
async def logout(body: LogoutRequest, request: Request, db: DbSession) -> MessageOut:
    await service.logout(db, body.refresh_token, request)
    return MessageOut(message="Logged out.")


@router.post(
    "/consent",
    response_model=ConsentOut,
    status_code=status.HTTP_201_CREATED,
    responses={k: ERRORS[k] for k in (400, 401, 409)},
    summary="Grant or withdraw a consent (required before health data is stored)",
)
async def consent(
    body: ConsentRequest, request: Request, user: CurrentUser, db: DbSession
) -> ConsentOut:
    record = await service.record_consent(db, user, body, request)
    return ConsentOut.model_validate(record)


@router.get("/me", response_model=MeOut, responses={401: ERRORS[401]}, summary="Current user")
async def get_me(user: CurrentUser, db: DbSession) -> MeOut:
    status_ = await service.consent_status(db, user)
    return MeOut(**UserOut.model_validate(user).model_dump(), consents=status_)


@router.patch(
    "/me", response_model=MeOut, responses={401: ERRORS[401]}, summary="Update own profile"
)
async def update_me(
    body: UpdateMeRequest, request: Request, user: CurrentUser, db: DbSession
) -> MeOut:
    user = await service.update_me(db, user, body, request)
    status_ = await service.consent_status(db, user)
    return MeOut(**UserOut.model_validate(user).model_dump(), consents=status_)
