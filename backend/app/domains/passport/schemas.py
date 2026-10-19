"""passport Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.domains.passport.models import PassportEntryType

Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Details = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]

# Longest and shortest share a patient can grant (HP-3: time-limited).
MIN_SHARE_HOURS = 1
MAX_SHARE_HOURS = 24 * 30


class PassportEntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_type: PassportEntryType
    occurred_at: AwareDatetime = Field(description="When it happened, with a timezone.")
    summary: Summary = Field(description="One line, e.g. 'Transfusion, 2 units'. Encrypted.")
    details: Details | None = Field(default=None, description="Optional notes. Encrypted.")
    facility_id: uuid.UUID | None = Field(
        default=None, description="Where it happened (visit, transfusion, crisis)."
    )


class PassportEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    entry_type: PassportEntryType
    occurred_at: datetime
    summary: str
    details: str | None
    facility_id: uuid.UUID | None
    facility_name: str | None = None
    crisis_event_id: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: datetime


class PassportPatient(BaseModel):
    id: uuid.UUID
    full_name: str
    date_of_birth: date
    sex: str | None
    genotype: str | None
    blood_group: str | None


class PassportProfile(BaseModel):
    """From the care profile. Lists only: CareKin never gives dosing advice."""

    known_triggers: str | None
    medications_overview: str | None
    allergies: str | None
    care_instructions: str | None


class PassportAccessRead(BaseModel):
    via: Literal["self", "guardian", "family", "facility"]
    can_manage: bool
    share_id: uuid.UUID | None = None
    facility_id: uuid.UUID | None = None
    expires_at: datetime | None = None
    entry_types: list[PassportEntryType] = Field(description="The entry types this caller sees.")


class PassportRead(BaseModel):
    patient: PassportPatient
    profile: PassportProfile | None = Field(
        description="Null when there is no care profile, or a share leaves it out."
    )
    entries: list[PassportEntryRead]
    total_entries: int = Field(description="Entries this caller may see, before paging.")
    access: PassportAccessRead
    disclaimer: str


class PassportShareCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_id: uuid.UUID
    entry_types: list[PassportEntryType] | None = Field(
        default=None, description="What to share. Default: every entry type."
    )
    include_profile: bool = Field(
        default=True,
        description="Also share genotype, blood group, allergies, triggers and medicines list.",
    )
    expires_in_hours: int = Field(default=72, ge=MIN_SHARE_HOURS, le=MAX_SHARE_HOURS)

    @field_validator("entry_types")
    @classmethod
    def _not_empty(cls, value: list[PassportEntryType] | None) -> list[PassportEntryType] | None:
        if value is not None and not value:
            raise ValueError("Share at least one entry type, or leave entry_types out for all")
        return sorted(set(value), key=list(PassportEntryType).index) if value else value


class PassportShareRead(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    facility_id: uuid.UUID
    facility_name: str | None = None
    consent_record_id: uuid.UUID
    granted_by: uuid.UUID | None
    entry_types: list[PassportEntryType]
    include_profile: bool
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime
    status: Literal["active", "expired", "revoked"]


class ReceivedShareRead(BaseModel):
    """For a facility admin: a patient who shared their passport with your facility."""

    share_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    facility_id: uuid.UUID
    facility_name: str
    entry_types: list[PassportEntryType]
    include_profile: bool
    expires_at: datetime
    created_at: datetime
