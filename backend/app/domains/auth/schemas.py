"""auth Pydantic schemas: the request and response contract for web and mobile."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.core.permissions import SELF_REGISTRATION_ROLES, UserRole
from app.core.phone import InvalidPhoneNumber, normalize_phone
from app.domains.auth.models import ConsentType, PreferredLanguage


def _phone(value: str) -> str:
    try:
        return normalize_phone(value)
    except InvalidPhoneNumber as exc:
        raise ValueError(str(exc)) from exc


PhoneNumber = Annotated[
    str,
    Field(min_length=7, max_length=24, examples=["08031234567", "+2348031234567"]),
    AfterValidator(_phone),
]
OtpCode = Annotated[str, Field(pattern=r"^\d{4,8}$", examples=["123456"])]

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_name(value: str) -> str:
    value = " ".join(value.split())
    if len(value) < 2:
        raise ValueError("Enter your full name")
    return value


def _clean_email(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if not value:
        return None
    if not _EMAIL.match(value):
        raise ValueError("Enter a valid email address")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# -------------------------------------------------------------- requests
class RegisterRequest(StrictModel):
    phone: PhoneNumber
    full_name: Annotated[str, Field(max_length=120), AfterValidator(_clean_name)]
    role: UserRole = Field(
        description="patient, guardian, caregiver or facility_admin. ops accounts are created by an admin."
    )
    email: Annotated[str | None, Field(max_length=254), AfterValidator(_clean_email)] = None
    preferred_language: PreferredLanguage = PreferredLanguage.ENGLISH

    @field_validator("role")
    @classmethod
    def _self_service_role(cls, role: UserRole) -> UserRole:
        if role not in SELF_REGISTRATION_ROLES:
            allowed = ", ".join(sorted(r.value for r in SELF_REGISTRATION_ROLES))
            raise ValueError(f"Choose one of: {allowed}")
        return role


class OtpSendRequest(StrictModel):
    phone: PhoneNumber


class OtpVerifyRequest(StrictModel):
    phone: PhoneNumber
    code: OtpCode


class LoginRequest(StrictModel):
    phone: PhoneNumber
    code: OtpCode


class RefreshRequest(StrictModel):
    refresh_token: Annotated[str, Field(min_length=20, max_length=200)]


class LogoutRequest(RefreshRequest):
    pass


class ConsentRequest(StrictModel):
    consent_type: ConsentType
    granted: bool = Field(description="false withdraws an earlier consent of this type")
    policy_version: str | None = Field(
        default=None,
        max_length=32,
        description="Version the user saw. Omit to use the current version; a stale one is rejected.",
    )
    patient_id: uuid.UUID | None = Field(
        default=None,
        description="Guardians only: the patient (minor) this consent is given for. Day 5.",
    )


class UpdateMeRequest(StrictModel):
    """Phone and role cannot be changed here: phone changes need a fresh OTP flow."""

    full_name: Annotated[str | None, Field(max_length=120)] = None
    email: Annotated[str | None, Field(max_length=254), AfterValidator(_clean_email)] = None
    preferred_language: PreferredLanguage | None = None

    @field_validator("full_name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_name(value)


# ------------------------------------------------------------- responses
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    phone: str
    email: str | None
    full_name: str
    role: UserRole
    preferred_language: PreferredLanguage
    phone_verified: bool = Field(
        validation_alias=AliasChoices("is_phone_verified", "phone_verified")
    )
    is_active: bool
    created_at: datetime


class ConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    consent_type: ConsentType
    granted: bool
    policy_version: str
    patient_id: uuid.UUID | None
    granted_at: datetime
    revoked_at: datetime | None


class ConsentStatus(BaseModel):
    """Current position per consent type, for the user themself (not per patient)."""

    terms: bool
    health_data: bool
    location: bool
    llm_processing: bool
    policy_version: str = Field(description="The current policy version on the server")
    missing_required: list[ConsentType] = Field(
        description="Consents the app must collect before onboarding can finish"
    )


class MeOut(UserOut):
    consents: ConsentStatus


class OtpSentOut(BaseModel):
    message: str
    expires_in: int = Field(description="Seconds until the code expires")
    resend_after: int = Field(description="Seconds before another code can be requested")
    debug_code: str | None = Field(
        default=None,
        description="Local development only (mock SMS). Always null in staging and production.",
    )


class RegisterOut(OtpSentOut):
    user: UserOut


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Seconds until the access token expires")
    refresh_expires_in: int
    user: UserOut


class MessageOut(BaseModel):
    message: str
