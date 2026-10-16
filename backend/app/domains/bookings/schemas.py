"""bookings Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domains.bookings.models import (
    ApprovalDecision,
    BookingStatus,
    BookingType,
    CheckInKind,
    ReviewDirection,
    ShiftStatus,
)

# TODO: import from a shared module (e.g. app/core/geo.py) once GeoPoint moves there.
from app.domains.patients.schemas import GeoPoint

Address = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
NoteBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
DeviceId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]

# Shortest and longest booking the API accepts.
MIN_BOOKING = timedelta(hours=1)
MAX_BOOKING = timedelta(days=31)


# ---------------------------------------------------------------- bookings
class BookingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    caregiver_id: uuid.UUID
    booking_type: BookingType
    starts_at: AwareDatetime = Field(description="With a timezone, e.g. 2026-11-02T08:00:00+01:00.")
    ends_at: AwareDatetime
    service_location: GeoPoint = Field(description="Where care happens; the check-in geofence.")
    service_address: Address
    guardian_present: bool = Field(
        default=False,
        description="A guardian will be present. Needed for overnight and live-in bookings "
        "in the first month with a caregiver (TS-4).",
    )

    @model_validator(mode="after")
    def _check(self) -> BookingCreate:
        length = self.ends_at - self.starts_at
        if length < MIN_BOOKING:
            raise ValueError("A booking must be at least 1 hour long and end after it starts")
        if length > MAX_BOOKING:
            raise ValueError("A booking can't be longer than 31 days")
        return self


class BookingRead(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    caregiver_id: uuid.UUID
    booked_by: uuid.UUID | None
    booking_type: BookingType
    status: BookingStatus
    starts_at: datetime
    ends_at: datetime
    # Null for a family member the patient hasn't shared their location with.
    service_location: GeoPoint | None
    service_address: str | None
    guardian_present: bool
    rate_minor: int = Field(description="Hourly rate in minor units (kobo).")
    total_minor: int
    currency: str
    created_at: datetime
    access: Literal["patient_side", "caregiver", "ops"] = Field(
        description="How the caller is related to this booking."
    )


# ---------------------------------------------------------------- guardian approval
class GuardianApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    reason: Reason | None = None


class GuardianApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    booking_id: uuid.UUID
    guardian_id: uuid.UUID
    decision: ApprovalDecision
    reason: str | None
    decided_at: datetime


# ---------------------------------------------------------------- shifts and check-ins
class ShiftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    booking_id: uuid.UUID
    scheduled_start: datetime
    scheduled_end: datetime
    actual_start: datetime | None
    actual_end: datetime | None
    status: ShiftStatus


class CheckInCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CheckInKind
    location: GeoPoint
    accuracy_m: int | None = Field(default=None, ge=0, le=100_000)
    client_timestamp: AwareDatetime = Field(
        description="When it happened on the device. Can be in the past for offline check-ins."
    )
    device_id: DeviceId | None = None


class CheckInRead(BaseModel):
    id: uuid.UUID
    shift_id: uuid.UUID
    kind: CheckInKind
    location: GeoPoint
    accuracy_m: int | None
    distance_m: int
    within_geofence: bool
    client_timestamp: datetime
    received_at: datetime
    device_id: str | None


class FlaggedCheckInRead(CheckInRead):
    """A check-in outside the geofence, for the ops queue."""

    booking_id: uuid.UUID


class HousekeepingRead(BaseModel):
    expired: int = Field(description="Stale bookings cancelled (not confirmed or not paid).")
    shifts_missed: int
    shifts_auto_closed: int = Field(description="Checked in but never checked out.")
    completed: int
    no_shows: int = Field(description="Paid bookings where every shift was missed.")


# ---------------------------------------------------------------- reviews
class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: int = Field(ge=1, le=5)
    comment: Comment | None = None


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    booking_id: uuid.UUID
    shift_id: uuid.UUID
    reviewer_id: uuid.UUID
    reviewee_id: uuid.UUID
    direction: ReviewDirection
    rating: int
    comment: str | None
    created_at: datetime


# ---------------------------------------------------------------- care notes
class CareNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: NoteBody


class CareNoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shift_id: uuid.UUID
    author_id: uuid.UUID | None
    body: str
    created_at: datetime
