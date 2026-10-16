"""Booking housekeeping (R1, R2 in BOOKINGS_LOG.md). Run every 5 to 15 minutes.

There's no Celery app in the project yet. Once there is, schedule
`run_housekeeping` with Celery beat. Until then ops can trigger it with
POST /bookings/ops/housekeeping.

1. Expire stale bookings (R2):
   - requested or pending_guardian whose start time has passed, and
   - accepted but not paid within PAYMENT_WINDOW, or whose start time has passed.
   They become cancelled and their shifts cancelled.
2. Close shifts that will never be checked out properly (R1), LATE_GRACE after the end:
   - scheduled (no check-in)          -> missed
   - in_progress (no check-out)       -> completed with actual_end left NULL, so ops
                                         and payments can see the check-out is missing.
3. Settle each booking those shifts belong to: completed if any shift ran, or
   cancelled as a no-show if every shift was missed (payments refunds it).

Rows are locked with SKIP LOCKED, so two runs at once don't fight, and a booking
being paid or checked in at that moment is simply picked up next run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.bookings import notifications as notices
from app.domains.bookings import service
from app.domains.bookings.models import Booking, BookingStatus, Shift, ShiftStatus


@dataclass
class HousekeepingResult:
    expired: int = 0
    shifts_missed: int = 0
    shifts_auto_closed: int = 0
    completed: int = 0
    no_shows: int = 0
    # Notices to send after the commit: (booking, kind, extra)
    _notices: list[tuple[Booking, str, datetime | str | None]] = field(
        default_factory=list, repr=False
    )

    def as_dict(self) -> dict[str, int]:
        return {
            "expired": self.expired,
            "shifts_missed": self.shifts_missed,
            "shifts_auto_closed": self.shifts_auto_closed,
            "completed": self.completed,
            "no_shows": self.no_shows,
        }


async def _expire_stale(
    db: AsyncSession, now: datetime, actor: service.Actor | None, result: HousekeepingResult
) -> None:
    unanswered = and_(
        Booking.status.in_((BookingStatus.REQUESTED, BookingStatus.PENDING_GUARDIAN)),
        Booking.starts_at <= now,
    )
    unpaid = and_(
        Booking.status == BookingStatus.ACCEPTED,
        or_(Booking.starts_at <= now, Booking.accepted_at <= now - service.PAYMENT_WINDOW),
    )
    stale = await db.scalars(
        select(Booking).where(or_(unanswered, unpaid)).with_for_update(skip_locked=True)
    )
    for booking in stale:
        previous = booking.status
        reason = "not_paid" if previous is BookingStatus.ACCEPTED else "not_confirmed"
        booking.status = BookingStatus.CANCELLED
        await service.cancel_open_shifts(db, booking.id)
        service._audit(
            db,
            actor,
            "booking.expire",
            "booking",
            booking.id,
            {"previous_status": previous.value, "reason": reason},
        )
        result.expired += 1
        result._notices.append((booking, "expired", reason))
        if previous is not BookingStatus.PENDING_GUARDIAN:  # the caregiver had seen it
            result._notices.append((booking, "expired_caregiver", reason))


async def _close_overdue_shifts(
    db: AsyncSession, now: datetime, actor: service.Actor | None, result: HousekeepingResult
) -> set[uuid.UUID]:
    overdue = await db.scalars(
        select(Shift)
        .join(Booking, Booking.id == Shift.booking_id)
        .where(
            Shift.status.in_((ShiftStatus.SCHEDULED, ShiftStatus.IN_PROGRESS)),
            Shift.scheduled_end <= now - service.LATE_GRACE,
            Booking.status.in_((BookingStatus.PAID, BookingStatus.IN_PROGRESS)),
        )
        .with_for_update(of=Shift, skip_locked=True)
    )
    touched: set[uuid.UUID] = set()
    for shift in overdue:
        if shift.status is ShiftStatus.SCHEDULED:
            shift.status = ShiftStatus.MISSED
            action = "shift.missed"
            result.shifts_missed += 1
            booking = await db.get(Booking, shift.booking_id)
            assert booking is not None
            result._notices.append((booking, "missed", shift.scheduled_start))
        else:
            shift.status = ShiftStatus.COMPLETED  # actual_end stays NULL: no check-out
            action = "shift.auto_close"
            result.shifts_auto_closed += 1
        service._audit(db, actor, action, "shift", shift.id, {"booking_id": str(shift.booking_id)})
        touched.add(shift.booking_id)
    return touched


async def _settle(
    db: AsyncSession,
    booking_ids: set[uuid.UUID],
    actor: service.Actor | None,
    result: HousekeepingResult,
) -> None:
    for booking_id in booking_ids:
        booking = await db.get(Booking, booking_id, with_for_update={"skip_locked": True})
        if booking is None:
            continue  # locked by a payment or check-in right now; next run settles it
        outcome = await service.settle_booking(db, booking)
        if outcome == "completed":
            result.completed += 1
            service._audit(db, actor, "booking.complete", "booking", booking.id)
        elif outcome == "no_show":
            result.no_shows += 1
            service._audit(db, actor, "booking.no_show", "booking", booking.id)
            result._notices.append((booking, "no_show", None))


async def _send_notices(
    db: AsyncSession, notifier: notices.Notifier, result: HousekeepingResult
) -> None:
    for booking, kind, extra in result._notices:
        starts = service.when(booking.starts_at)
        if kind == "expired":
            await service.notify(
                db,
                notifier,
                lambda b=booking: service.booker_phones(db, b),
                notices.expired(starts, str(extra)),
            )
        elif kind == "expired_caregiver":
            await service.notify(
                db,
                notifier,
                lambda b=booking: service.caregiver_phones(db, b),
                notices.expired(starts, str(extra)),
            )
        elif kind == "missed" and isinstance(extra, datetime):
            await service.notify(
                db,
                notifier,
                lambda b=booking: service.safety_phones(db, b),
                notices.shift_missed(service.when(extra)),
            )
        elif kind == "no_show":
            await service.notify(
                db,
                notifier,
                lambda b=booking: service.safety_phones(db, b),
                notices.no_show(starts),
            )


async def run_housekeeping(
    db: AsyncSession,
    now: datetime | None = None,
    actor: service.Actor | None = None,
    notifier: notices.Notifier = notices.NO_NOTICES,
) -> HousekeepingResult:
    """One pass of R1 and R2. `actor` is the ops user when triggered by hand, else None."""
    now = now or datetime.now(UTC)
    result = HousekeepingResult()
    await _expire_stale(db, now, actor, result)
    touched = await _close_overdue_shifts(db, now, actor, result)
    await _settle(db, touched, actor, result)
    await db.commit()
    await _send_notices(db, notifier, result)
    return result
