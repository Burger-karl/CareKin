"""caregivers Pydantic schemas (Day 6): the request/response contract.

Follows the style of app/domains/auth/schemas.py: strict requests that reject
unknown fields, small reusable validators, and `*Out` models for responses.

Three views of a caregiver exist on purpose:
- CaregiverOut        the caregiver themself and ops (includes guarantors,
                      certifications and verification progress)
- CaregiverPublicOut  everyone else: no guarantors, no NIN, no exact location,
                      no verification internals
- VerifyOut           ops only: includes each check's stored result
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    model_validator,
)

from app.core.phone import InvalidPhoneNumber, normalize_phone
from app.domains.caregivers.models import (
    CaregiverStatus,
    CaregiverTier,
    CertType,
    CheckStatus,
    CheckType,
    ReviewStatus,
)

GUARANTORS_REQUIRED = 2  # TS-1: two guarantors per caregiver
VERIFIABLE_CHECKS = (CheckType.NIN, CheckType.BACKGROUND)  # Day 6 /verify only
MAX_AVAILABILITY_WINDOWS = 50


# ------------------------------------------------------------ validators
def _phone(value: str) -> str:
    try:
        return normalize_phone(value)
    except InvalidPhoneNumber as exc:
        raise ValueError(str(exc)) from exc


def _clean_text(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("This field cannot be empty")
    return value


def _clean_languages(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in values:
        item = " ".join(raw.split()).lower()
        if not item:
            raise ValueError("Language names cannot be empty")
        if len(item) > 40:
            raise ValueError("Language names are limited to 40 characters")
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


PhoneNumber = Annotated[
    str,
    Field(min_length=7, max_length=24, examples=["08031234567", "+2348031234567"]),
    AfterValidator(_phone),
]
ShortText = Annotated[str, AfterValidator(_clean_text)]
Languages = Annotated[
    list[str],
    Field(max_length=10, examples=[["english", "igbo"]]),
    AfterValidator(_clean_languages),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _both_or_neither(latitude: float | None, longitude: float | None) -> None:
    if (latitude is None) != (longitude is None):
        raise ValueError("Send base_latitude and base_longitude together")


# -------------------------------------------------------------- requests
class GuarantorIn(StrictModel):
    full_name: Annotated[ShortText, Field(min_length=2, max_length=120)]
    phone: PhoneNumber
    relationship: Annotated[ShortText, Field(max_length=60, examples=["Employer", "Pastor"])]
    address: Annotated[ShortText, Field(min_length=5, max_length=300)]


class CaregiverCreateRequest(StrictModel):
    """POST /caregivers. The caregiver is the logged-in user (role: caregiver)."""

    tier: CaregiverTier
    bio: Annotated[str | None, Field(max_length=2000)] = None
    languages: Languages = Field(default_factory=list)
    years_experience: Annotated[int | None, Field(ge=0, le=60)] = None
    hourly_rate_minor: Annotated[
        int | None, Field(ge=0, description="Hourly rate in kobo (1 naira = 100 kobo)")
    ] = None
    base_latitude: Annotated[float | None, Field(ge=-90, le=90)] = None
    base_longitude: Annotated[float | None, Field(ge=-180, le=180)] = None
    service_radius_km: Annotated[int | None, Field(ge=1, le=200)] = None
    guarantors: list[GuarantorIn] = Field(
        min_length=GUARANTORS_REQUIRED,
        max_length=GUARANTORS_REQUIRED,
        description="Exactly two guarantors are required",
    )

    @model_validator(mode="after")
    def _check(self) -> CaregiverCreateRequest:
        _both_or_neither(self.base_latitude, self.base_longitude)
        phones = [g.phone for g in self.guarantors]
        if len(set(phones)) != len(phones):
            raise ValueError("Each guarantor needs a different phone number")
        return self


class CaregiverUpdateRequest(StrictModel):
    """PATCH /caregivers/{id}. Only the fields sent are changed.

    Tier, status and guarantors are not editable here: tier changes alter which
    verification steps apply, and status is only moved by ops actions.
    """

    bio: Annotated[str | None, Field(max_length=2000)] = None
    languages: Languages | None = None
    years_experience: Annotated[int | None, Field(ge=0, le=60)] = None
    hourly_rate_minor: Annotated[int | None, Field(ge=0)] = None
    base_latitude: Annotated[float | None, Field(ge=-90, le=90)] = None
    base_longitude: Annotated[float | None, Field(ge=-180, le=180)] = None
    service_radius_km: Annotated[int | None, Field(ge=1, le=200)] = None

    @model_validator(mode="after")
    def _check(self) -> CaregiverUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("Send at least one field to update")
        _both_or_neither(self.base_latitude, self.base_longitude)
        return self


class CertificationCreateRequest(StrictModel):
    """Metadata for POST /caregivers/{id}/certifications.

    The router receives these as multipart form fields next to the uploaded
    file and builds this model from them. The file itself is checked in the
    service (type and size), not here.
    """

    cert_type: CertType
    title: Annotated[ShortText, Field(min_length=2, max_length=200)]
    issuer: Annotated[ShortText | None, Field(max_length=200)] = None
    licence_number: Annotated[ShortText | None, Field(max_length=50)] = None
    expires_at: date | None = None

    @model_validator(mode="after")
    def _check(self) -> CertificationCreateRequest:
        if self.cert_type == CertType.NURSING_LICENCE and not self.licence_number:
            raise ValueError("A nursing licence needs its licence number")
        if self.cert_type != CertType.NURSING_LICENCE and self.licence_number:
            raise ValueError("Licence numbers apply to nursing licences only")
        if self.expires_at is not None and self.expires_at <= date.today():
            raise ValueError("This certificate has already expired")
        return self


class AvailabilityWindowIn(StrictModel):
    day_of_week: Annotated[int, Field(ge=0, le=6, description="0 = Monday ... 6 = Sunday")]
    start_time: time
    end_time: time
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def _check(self) -> AvailabilityWindowIn:
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot be before valid_from")
        return self


def _ranges_overlap(a: AvailabilityWindowIn, b: AvailabilityWindowIn) -> bool:
    """True when two windows on the same weekday clash in time and in dates."""
    if a.day_of_week != b.day_of_week:
        return False
    if not (a.start_time < b.end_time and b.start_time < a.end_time):
        return False
    a_from, a_to = a.valid_from or date.min, a.valid_to or date.max
    b_from, b_to = b.valid_from or date.min, b.valid_to or date.max
    return a_from <= b_to and b_from <= a_to


class AvailabilitySetRequest(StrictModel):
    """POST /caregivers/{id}/availability. Replaces the caregiver's whole schedule."""

    windows: list[AvailabilityWindowIn] = Field(max_length=MAX_AVAILABILITY_WINDOWS)

    @model_validator(mode="after")
    def _no_overlaps(self) -> AvailabilitySetRequest:
        for i, first in enumerate(self.windows):
            for second in self.windows[i + 1 :]:
                if _ranges_overlap(first, second):
                    raise ValueError("Availability windows on the same day cannot overlap")
        return self


class VerifyRequest(StrictModel):
    """POST /caregivers/{id}/verify (ops only)."""

    checks: list[CheckType] = Field(
        default_factory=lambda: list(VERIFIABLE_CHECKS),
        min_length=1,
        description="Which checks to run. Defaults to nin and background.",
    )
    nin: SecretStr | None = Field(
        default=None,
        description="The caregiver's 11-digit NIN. Sent to the provider, never stored.",
    )

    @model_validator(mode="after")
    def _check(self) -> VerifyRequest:
        if len(set(self.checks)) != len(self.checks):
            raise ValueError("Each check can only be listed once")
        unsupported = [c.value for c in self.checks if c not in VERIFIABLE_CHECKS]
        if unsupported:
            allowed = ", ".join(c.value for c in VERIFIABLE_CHECKS)
            raise ValueError(f"Cannot run {', '.join(unsupported)} here. Allowed: {allowed}")
        wants_nin = CheckType.NIN in self.checks
        if wants_nin and self.nin is None:
            raise ValueError("The nin check needs the caregiver's NIN")
        if self.nin is not None:
            if not wants_nin:
                raise ValueError("Send nin only when running the nin check")
            if not re.fullmatch(r"\d{11}", self.nin.get_secret_value()):
                raise ValueError("A NIN is exactly 11 digits")
        return self


# ------------------------------------------------------------- responses
class GuarantorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    phone: str
    relationship: str
    address: str
    verified_at: datetime | None


class CertificationOut(BaseModel):
    """Owner/ops view. The storage key is never exposed, only whether a file exists."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cert_type: CertType
    title: str
    issuer: str | None
    licence_number: str | None
    status: ReviewStatus
    has_document: bool
    expires_at: date | None
    verified_at: datetime | None
    created_at: datetime


class PublicCertificationOut(BaseModel):
    """What a patient sees: verified credentials only, no numbers or files."""

    model_config = ConfigDict(from_attributes=True)

    cert_type: CertType
    title: str
    issuer: str | None
    expires_at: date | None


class AvailabilityWindowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    day_of_week: int
    start_time: time
    end_time: time
    valid_from: date | None
    valid_to: date | None


class VerificationProgressOut(BaseModel):
    """What the caregiver sees about a check: its progress, never the provider's data."""

    model_config = ConfigDict(from_attributes=True)

    check_type: CheckType
    status: CheckStatus
    completed_at: datetime | None


class VerificationCheckOut(VerificationProgressOut):
    """Ops view: adds the provider details and the stored (minimal) result."""

    id: uuid.UUID
    provider: str
    provider_ref: str | None
    result: dict | None
    created_at: datetime


class CaregiverOut(BaseModel):
    """Owner/ops view of a caregiver.

    base_latitude and base_longitude are not columns: the service reads them
    from the geography point and supplies them when building this model.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    tier: CaregiverTier
    status: CaregiverStatus
    bio: str | None
    languages: list[str]
    years_experience: int | None
    hourly_rate_minor: int | None
    base_latitude: float | None = None
    base_longitude: float | None = None
    service_radius_km: int | None
    avg_rating: Decimal | None
    rating_count: int
    approved_at: datetime | None
    created_at: datetime
    guarantors: list[GuarantorOut]
    certifications: list[CertificationOut]
    verification_checks: list[VerificationProgressOut]


class CaregiverPublicOut(BaseModel):
    """Public-safe profile. Only approved caregivers are shown to other users.

    full_name comes from the caregiver's user account, which the service supplies.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    tier: CaregiverTier
    bio: str | None
    languages: list[str]
    years_experience: int | None
    hourly_rate_minor: int | None
    service_radius_km: int | None
    avg_rating: Decimal | None
    rating_count: int
    certifications: list[PublicCertificationOut]
    availability_windows: list[AvailabilityWindowOut]


class AvailabilityOut(BaseModel):
    windows: list[AvailabilityWindowOut]


class VerifyOut(BaseModel):
    caregiver_id: uuid.UUID
    status: CaregiverStatus = Field(description="The caregiver's status after the checks ran")
    checks: list[VerificationCheckOut]