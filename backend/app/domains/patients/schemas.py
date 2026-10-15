"""patients Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domains.patients.models import AlertChannel

E164Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+[1-9]\d{7,14}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Relationship = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=50)
]
Sex = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=20)
]
# Free text so less common genotypes (e.g. S-beta thalassaemia) fit: SS, SC, AS, ...
Genotype = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=2, max_length=20)
]
BloodGroup = Literal["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
CareText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]

# Keys a patient can grant to a family link.
SHARE_SCOPE_KEYS = frozenset({"health_data", "location"})


class GeoPoint(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


def _check_date_of_birth(value: date | None) -> None:
    if value is None:
        return
    if value > datetime.now(UTC).date():
        raise ValueError("date_of_birth can't be in the future")
    if value.year < 1900:
        raise ValueError("date_of_birth is too far in the past")


# ---------------------------------------------------------------- patients
class PatientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    for_self: bool = Field(
        default=True,
        description="True: the record is the caller's own and is linked to their account. "
        "False: the caller creates it for someone else (e.g. a child) and becomes "
        "their primary guardian.",
    )
    guardian_relationship: Relationship | None = Field(
        default=None, description="Required when for_self is false, e.g. 'parent'."
    )
    full_name: Name
    date_of_birth: date
    sex: Sex | None = None
    genotype: Genotype | None = None
    blood_group: BloodGroup | None = None
    home_location: GeoPoint | None = None

    @model_validator(mode="after")
    def _check(self) -> PatientCreate:
        _check_date_of_birth(self.date_of_birth)
        if not self.for_self and self.guardian_relationship is None:
            raise ValueError("guardian_relationship is required when for_self is false")
        if self.for_self and self.guardian_relationship is not None:
            raise ValueError("guardian_relationship only applies when for_self is false")
        return self


class PatientUpdate(BaseModel):
    """Partial update. Send null to clear an optional field."""

    model_config = ConfigDict(extra="forbid")

    full_name: Name | None = None
    date_of_birth: date | None = None
    sex: Sex | None = None
    genotype: Genotype | None = None
    blood_group: BloodGroup | None = None
    home_location: GeoPoint | None = None

    @model_validator(mode="after")
    def _check(self) -> PatientUpdate:
        for required in ("full_name", "date_of_birth"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} can't be null")
        _check_date_of_birth(self.date_of_birth)
        return self


class PatientRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    full_name: str
    date_of_birth: date
    age: int
    is_minor: bool
    sex: str | None
    # Null when the caller's access doesn't include health data or location.
    genotype: str | None
    blood_group: str | None
    home_location: GeoPoint | None
    created_by: uuid.UUID | None
    created_at: datetime
    access: Literal["self", "guardian", "family"] = Field(
        description="How the caller is related to this patient."
    )


# ---------------------------------------------------------------- guardians
class GuardianCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID = Field(description="The guardian's own CareKin account.")
    relationship: Relationship
    is_primary: bool = False
    can_approve_caregivers: bool = True
    receives_crisis_alerts: bool = True


class GuardianUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship: Relationship | None = None
    is_primary: bool | None = None
    can_approve_caregivers: bool | None = None
    receives_crisis_alerts: bool | None = None


class GuardianRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    user_id: uuid.UUID
    relationship: str
    is_primary: bool
    can_approve_caregivers: bool
    receives_crisis_alerts: bool
    created_at: datetime


# ---------------------------------------------------------------- emergency contacts
class EmergencyContactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    phone: E164Phone = Field(description="E.164 format, e.g. +2348012345678.")
    relationship: Relationship
    channel: AlertChannel = AlertChannel.SMS
    priority: int = Field(default=1, ge=1, le=99, description="Alert order, 1 first.")


class EmergencyContactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    phone: E164Phone | None = None
    relationship: Relationship | None = None
    channel: AlertChannel | None = None
    priority: int | None = Field(default=None, ge=1, le=99)


class EmergencyContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    name: str
    phone: str
    relationship: str
    channel: AlertChannel
    priority: int


# ---------------------------------------------------------------- care profile
class CareProfileWrite(BaseModel):
    """Full replacement of the care profile (PUT)."""

    model_config = ConfigDict(extra="forbid")

    known_triggers: CareText | None = None
    medications_overview: CareText | None = Field(
        default=None, description="A list of medications only. No dosing."
    )
    allergies: CareText | None = None
    care_instructions: CareText | None = None
    primary_facility_id: uuid.UUID | None = None


class CareProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    known_triggers: str | None
    medications_overview: str | None
    allergies: str | None
    care_instructions: str | None
    primary_facility_id: uuid.UUID | None
    updated_by: uuid.UUID | None
    updated_at: datetime


# ---------------------------------------------------------------- family links
def _check_share_scope(scope: dict[str, bool] | None) -> None:
    if scope is None:
        return
    unknown = set(scope) - SHARE_SCOPE_KEYS
    if unknown:
        raise ValueError(
            f"Unknown share_scope keys: {sorted(unknown)}. Allowed: {sorted(SHARE_SCOPE_KEYS)}"
        )


class FamilyLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID = Field(description="The family member's CareKin account.")
    share_scope: dict[str, bool] = Field(
        default_factory=dict,
        description="What they may see beyond the basic profile: health_data, location.",
    )
    can_fund: bool = False

    @model_validator(mode="after")
    def _check(self) -> FamilyLinkCreate:
        _check_share_scope(self.share_scope)
        return self


class FamilyLinkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    share_scope: dict[str, bool] | None = None
    can_fund: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> FamilyLinkUpdate:
        _check_share_scope(self.share_scope)
        return self


class FamilyLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    user_id: uuid.UUID
    share_scope: dict[str, bool]
    can_fund: bool
    created_at: datetime
