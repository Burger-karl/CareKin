"""Booking-level check-in, check-out and review (Day 12, BK-3 and BK-4).

service.py works one shift at a time. A phone usually knows only the booking, so these
functions choose the right shift and hand over to the service, which keeps every rule
(geofence flagging, offline timestamps, access, audit) in one place.

How the shift is chosen:
- check-in:  the first shift whose window contains the device time (1 hour before the
             start until the end). Resending the same check-in finds the same shift.
- check-out: the shift that is in progress. Resending a check-out with the same device
             time finds the shift that already has it.
- review:    the shift in `shift_id`, or else the latest completed shift this person
             has not rated yet.

Releasing escrow after the last check-out belongs to the payments domain (Day 11) and
is not done here.
"""

from __future__ import annotations

import uuid

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.bookings import notifications as notices
from app.domains.bookings import service
from app.domains.bookings.models import CheckIn, CheckInKind, Review, Shift, ShiftStatus
from app.domains.bookings.schemas import CheckInCreate, CheckInRead, Comment, DeviceId, ReviewCreate
from app.domains.patients.schemas import GeoPoint


# ---------------------------------------------------------------- request bodies
class BookingCheckIn(BaseModel):
    """Body of POST /bookings/{id}/checkin and /checkout. The route supplies the kind."""

    model_config = ConfigDict(extra="forbid")

    location: GeoPoint
    accuracy_m: int | None = Field(default=None, ge=0, le=100_000)
    client_timestamp: AwareDatetime = Field(
        description="When it happened on the device. Can be in the past for offline use."
    )
    device_id: DeviceId | None = None


class BookingReview(BaseModel):
    """Body of POST /bookings/{id}/review."""

    model_config = ConfigDict(extra="forbid")

    rating: int = Field(ge=1, le=5)
    comment: Comment | None = None
    shift_id: uuid.UUID | None = Field(
        default=None, description="Which shift to rate. Omit for the latest unrated one."
    )


# ---------------------------------------------------------------- shift choice
async def _shifts(db: AsyncSession, booking_id: uuid.UUID) -> list[Shift]:
    rows = await db.scalars(
        select(Shift).where(Shift.booking_id == booking_id).order_by(Shift.scheduled_start)
    )
    return list(rows)


async def _shift_for_check_in(
    db: AsyncSession, booking_id: uuid.UUID, moment: AwareDatetime
) -> Shift:
    for shift in await _shifts(db, booking_id):
        opens = shift.scheduled_start - service.CHECK_IN_EARLY
        if opens <= moment < shift.scheduled_end:
            return shift
    raise service.BookingRuleViolation(
        "There is no shift to check in to at that time", code="no_shift_in_window"
    )


async def _shift_for_check_out(
    db: AsyncSession, booking_id: uuid.UUID, moment: AwareDatetime
) -> Shift:
    resent = await db.scalar(
        select(Shift)
        .join(CheckIn, CheckIn.shift_id == Shift.id)
        .where(
            Shift.booking_id == booking_id,
            CheckIn.kind == CheckInKind.CHECK_OUT,
            CheckIn.client_timestamp == moment,
        )
    )
    if resent is not None:
        return resent
    for shift in await _shifts(db, booking_id):
        if shift.status is ShiftStatus.IN_PROGRESS:
            return shift
    raise service.BookingRuleViolation(
        "No shift is in progress for this booking. Check in first.", code="no_shift_in_progress"
    )


async def _shift_to_review(
    db: AsyncSession, actor: service.Actor, booking_id: uuid.UUID, shift_id: uuid.UUID | None
) -> uuid.UUID:
    if shift_id is not None:
        return shift_id
    shifts = await _shifts(db, booking_id)
    completed = [s for s in shifts if s.status is ShiftStatus.COMPLETED]
    if not completed:
        raise service.BookingRuleViolation("A shift can be rated once it's checked out")
    rated = set(
        await db.scalars(
            select(Review.shift_id).where(
                Review.booking_id == booking_id, Review.reviewer_id == actor.user_id
            )
        )
    )
    for shift in reversed(completed):  # latest first
        if shift.id not in rated:
            return shift.id
    raise service.BookingConflict("You've already rated every completed shift")


# ---------------------------------------------------------------- actions
async def check_in(
    db: AsyncSession,
    actor: service.Actor,
    booking_id: uuid.UUID,
    data: BookingCheckIn,
    notifier: notices.Notifier,
) -> CheckInRead:
    await service.get_booking_access(db, actor, booking_id)  # 404 if it isn't theirs
    shift = await _shift_for_check_in(db, booking_id, data.client_timestamp)
    body = CheckInCreate(kind=CheckInKind.CHECK_IN, **data.model_dump())
    return await service.record_check_in(db, actor, booking_id, shift.id, body, notifier)


async def check_out(
    db: AsyncSession,
    actor: service.Actor,
    booking_id: uuid.UUID,
    data: BookingCheckIn,
    notifier: notices.Notifier,
) -> CheckInRead:
    await service.get_booking_access(db, actor, booking_id)
    shift = await _shift_for_check_out(db, booking_id, data.client_timestamp)
    body = CheckInCreate(kind=CheckInKind.CHECK_OUT, **data.model_dump())
    return await service.record_check_in(db, actor, booking_id, shift.id, body, notifier)


async def review(
    db: AsyncSession, actor: service.Actor, booking_id: uuid.UUID, data: BookingReview
) -> Review:
    await service.get_booking_access(db, actor, booking_id)
    shift_id = await _shift_to_review(db, actor, booking_id, data.shift_id)
    return await service.create_review(
        db, actor, booking_id, shift_id, ReviewCreate(rating=data.rating, comment=data.comment)
    )