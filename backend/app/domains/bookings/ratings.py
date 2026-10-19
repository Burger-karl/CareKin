"""Caregiver rating summary (BK-4), kept on caregivers.avg_rating and rating_count.

Only family-to-caregiver reviews count. The summary is recomputed from the reviews
table each time, so it can never drift from the rows it describes.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.bookings.models import Booking, Review, ReviewDirection
from app.domains.caregivers.models import Caregiver

_TWO_PLACES = Decimal("0.01")


async def refresh_caregiver_rating(db: AsyncSession, caregiver_id: uuid.UUID) -> None:
    """Recompute a caregiver's average and count inside the caller's transaction.

    Call it after adding a review and before committing. The caregiver row is locked
    first, so two reviews arriving together are counted one after the other.
    """
    await db.execute(select(Caregiver.id).where(Caregiver.id == caregiver_id).with_for_update())
    await db.flush()  # the new review must be visible to the aggregate below
    average, count = (
        await db.execute(
            select(func.avg(Review.rating), func.count(Review.id))
            .join(Booking, Booking.id == Review.booking_id)
            .where(
                Booking.caregiver_id == caregiver_id,
                Review.direction == ReviewDirection.FAMILY_TO_CAREGIVER,
            )
        )
    ).one()
    await db.execute(
        update(Caregiver)
        .where(Caregiver.id == caregiver_id)
        .values(
            avg_rating=Decimal(average).quantize(_TWO_PLACES) if count else None,
            rating_count=count,
        )
    )