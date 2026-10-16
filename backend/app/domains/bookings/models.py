"""bookings SQLAlchemy models: Booking, GuardianApproval, Shift, CheckIn, Review, CareNote.

Schema follows docs/architecture/ERD.md section 2.5.
- One booking has one or more shifts (a live-in booking spans many). Check-ins hang off
  shifts, so GPS records stay correct for multi-day bookings.
- Escrow is not here: the ERD puts EscrowTransaction in a separate payments domain.
- FKs are ON DELETE RESTRICT: bookings are money and safety records and are never deleted.
- Money is an integer in minor units (kobo) plus an ISO currency code. Never floats.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.encryption import EncryptedText
from app.database import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class BookingType(StrEnum):
    """BK-1."""

    HOURLY = "hourly"
    DAILY = "daily"
    OVERNIGHT = "overnight"
    LIVE_IN = "live_in"


class BookingStatus(StrEnum):
    REQUESTED = "requested"  # waiting for the caregiver
    PENDING_GUARDIAN = "pending_guardian"  # waiting for a guardian (TS-3)
    ACCEPTED = "accepted"  # caregiver said yes, waiting for payment
    DECLINED = "declined"  # caregiver or guardian said no
    PAID = "paid"  # funds held in escrow
    IN_PROGRESS = "in_progress"  # first check-in done
    COMPLETED = "completed"  # every shift finished
    CANCELLED = "cancelled"
    DISPUTED = "disputed"  # escrow held (TS-5)


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ShiftStatus(StrEnum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    MISSED = "missed"  # no check-in by the end of the grace period
    CANCELLED = "cancelled"  # booking cancelled, declined or expired before the shift ran


class CheckInKind(StrEnum):
    CHECK_IN = "check_in"
    CHECK_OUT = "check_out"


class ReviewDirection(StrEnum):
    """Two-way reviews (BK-4)."""

    FAMILY_TO_CAREGIVER = "family_to_caregiver"
    CAREGIVER_TO_FAMILY = "caregiver_to_family"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=_enum_values)


# ---------------------------------------------------------------- models
class Booking(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("starts_at < ends_at", name="starts_before_ends"),
        CheckConstraint("rate_minor >= 0 AND total_minor >= 0", name="money_not_negative"),
        # Finds clashes in a caregiver's schedule.
        Index("ix_bookings_caregiver_id_starts_at", "caregiver_id", "starts_at"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), index=True
    )
    # Indexed by ix_bookings_caregiver_id_starts_at (caregiver_id leads).
    caregiver_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("caregivers.id", ondelete="RESTRICT")
    )
    # Patient or guardian who booked.
    booked_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    booking_type: Mapped[BookingType] = mapped_column(_pg_enum(BookingType, "booking_type"))
    status: Mapped[BookingStatus] = mapped_column(
        _pg_enum(BookingStatus, "booking_status"), index=True
    )
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    # Geofence centre for GPS check-in (BK-3).
    service_location: Mapped[Any] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=True), nullable=False
    )
    service_address: Mapped[str] = mapped_column(Text)
    guardian_present: Mapped[bool] = mapped_column(  # TS-4 overnight rule
        Boolean, default=False, server_default=text("false")
    )
    rate_minor: Mapped[int] = mapped_column(BigInteger)  # hourly rate at booking time
    total_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))  # NGN, USD, GBP
    # Not in the ERD: lets housekeeping expire bookings accepted but never paid.
    accepted_at: Mapped[datetime | None]


class GuardianApproval(UUIDPrimaryKeyMixin, Base):
    """A guardian's decision on a caregiver for a minor's booking (TS-3)."""

    __tablename__ = "guardian_approvals"

    booking_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"), index=True
    )
    # Must have can_approve_caregivers (checked in the service).
    guardian_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guardians.id", ondelete="RESTRICT"), index=True
    )
    decision: Mapped[ApprovalDecision] = mapped_column(
        _pg_enum(ApprovalDecision, "approval_decision")
    )
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Shift(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "shifts"
    __table_args__ = (CheckConstraint("scheduled_start < scheduled_end", name="start_before_end"),)

    booking_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"), index=True
    )
    scheduled_start: Mapped[datetime]
    scheduled_end: Mapped[datetime]
    actual_start: Mapped[datetime | None]  # from check-in
    actual_end: Mapped[datetime | None]  # from check-out
    status: Mapped[ShiftStatus] = mapped_column(
        _pg_enum(ShiftStatus, "shift_status"),
        default=ShiftStatus.SCHEDULED,
        server_default=ShiftStatus.SCHEDULED.value,
    )


class CheckIn(UUIDPrimaryKeyMixin, Base):
    """A GPS check-in or check-out (BK-3). Recorded even outside the geofence, then flagged."""

    __tablename__ = "check_ins"
    # Not in the ERD: one check-in and one check-out per shift, so offline retries
    # racing each other can't both be stored.
    __table_args__ = (UniqueConstraint("shift_id", "kind"),)

    shift_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shifts.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[CheckInKind] = mapped_column(_pg_enum(CheckInKind, "checkin_kind"))
    location: Mapped[Any] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    )
    accuracy_m: Mapped[int | None] = mapped_column(Integer)
    distance_m: Mapped[int] = mapped_column(Integer)  # from the booking's service_location
    within_geofence: Mapped[bool] = mapped_column(Boolean)
    client_timestamp: Mapped[datetime]  # device clock, so offline check-ins keep their time
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())  # server clock
    device_id: Mapped[str | None] = mapped_column(String(128))


class Review(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Two-way rating after each shift (PRD BK-4).

    The ERD has one review per booking (unique booking_id, reviewer_id). The PRD asks for
    a rating after each shift, so reviews hang off shifts; booking_id is kept for lookups.
    """

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("shift_id", "reviewer_id"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"), index=True
    )
    shift_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shifts.id", ondelete="RESTRICT"), index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    reviewee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    direction: Mapped[ReviewDirection] = mapped_column(
        _pg_enum(ReviewDirection, "review_direction")
    )
    rating: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)


class CareNote(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A caregiver's note on a shift (P1). The body may hold health data, so it's encrypted."""

    __tablename__ = "care_notes"

    shift_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shifts.id", ondelete="RESTRICT"), index=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    body: Mapped[str] = mapped_column(EncryptedText)
