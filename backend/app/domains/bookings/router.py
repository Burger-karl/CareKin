"""bookings routes: Booking, GuardianApproval, Shift, CheckIn, Review, CareNote.

Errors are CareKinError subclasses, rendered by app.core.exceptions.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import AwareDatetime

from app.core.exceptions import ServiceUnavailableError
from app.core.permissions import UserRole, require_roles
from app.database import DbSession
from app.domains.auth.dependencies import CurrentUser
from app.domains.bookings import jobs, service
from app.domains.bookings.models import BookingStatus
from app.domains.bookings.notifications import Notifier
from app.domains.bookings.schemas import (
    BookingCreate,
    BookingRead,
    CareNoteCreate,
    CareNoteRead,
    CheckInCreate,
    CheckInRead,
    FlaggedCheckInRead,
    GuardianApprovalCreate,
    GuardianApprovalRead,
    HousekeepingRead,
    ReviewCreate,
    ReviewRead,
    ShiftRead,
)
from app.integrations.sms import get_sms_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bookings", tags=["bookings"])


def get_actor(request: Request, user: CurrentUser) -> service.Actor:
    return service.Actor(user_id=user.id, role=user.role, request=request)


def get_notifier() -> Notifier:
    """SMS notices if SMS is set up; otherwise bookings still work, just silently."""
    try:
        return Notifier(get_sms_client())
    except ServiceUnavailableError:
        logger.warning("SMS isn't configured, so booking notices are off")
        return Notifier(None)


Actor = Annotated[service.Actor, Depends(get_actor)]
Notices = Annotated[Notifier, Depends(get_notifier)]


# ---------------------------------------------------------------- ops
@router.get(
    "/ops/flagged-check-ins",
    response_model=list[FlaggedCheckInRead],
    dependencies=[require_roles(UserRole.OPS)],
)
async def list_flagged_check_ins(
    db: DbSession,
    actor: Actor,
    since: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Check-ins made outside the geofence, newest first (R7)."""
    rows = await service.list_flagged_check_ins(db, actor, since, limit, offset)
    return [
        FlaggedCheckInRead(**service.check_in_to_read(c).model_dump(), booking_id=booking_id)
        for c, booking_id in rows
    ]


@router.post(
    "/ops/housekeeping",
    response_model=HousekeepingRead,
    dependencies=[require_roles(UserRole.OPS)],
)
async def run_housekeeping(db: DbSession, actor: Actor, notifier: Notices):
    """Expire stale bookings and close overdue shifts now (R1, R2).

    Meant to run on a schedule; this endpoint is for ops until Celery beat is set up.
    """
    result = await jobs.run_housekeeping(db, actor=actor, notifier=notifier)
    return result.as_dict()


# ---------------------------------------------------------------- bookings
@router.get("", response_model=list[BookingRead])
async def list_bookings(
    db: DbSession,
    actor: Actor,
    status_filter: Annotated[BookingStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """The caller's bookings: as the patient side, as the caregiver, or all of them for ops."""
    return await service.list_bookings(db, actor, status_filter, limit, offset)


@router.post("", response_model=BookingRead, status_code=status.HTTP_201_CREATED)
async def create_booking(data: BookingCreate, db: DbSession, actor: Actor, notifier: Notices):
    """Book a caregiver. A minor's booking waits for guardian approval (TS-3)."""
    return await service.create_booking(db, actor, data, notifier)


@router.get("/{booking_id}", response_model=BookingRead)
async def get_booking(booking_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.get_booking(db, actor, booking_id)


@router.post("/{booking_id}/accept", response_model=BookingRead)
async def accept_booking(booking_id: uuid.UUID, db: DbSession, actor: Actor, notifier: Notices):
    return await service.accept_booking(db, actor, booking_id, notifier)


@router.post("/{booking_id}/decline", response_model=BookingRead)
async def decline_booking(booking_id: uuid.UUID, db: DbSession, actor: Actor, notifier: Notices):
    return await service.decline_booking(db, actor, booking_id, notifier)


@router.post("/{booking_id}/cancel", response_model=BookingRead)
async def cancel_booking(booking_id: uuid.UUID, db: DbSession, actor: Actor, notifier: Notices):
    """Before payment only. After that, raise an incident."""
    return await service.cancel_booking(db, actor, booking_id, notifier)


# ---------------------------------------------------------------- guardian approval
@router.get("/{booking_id}/guardian-approvals", response_model=list[GuardianApprovalRead])
async def list_guardian_approvals(booking_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.list_guardian_approvals(db, actor, booking_id)


@router.post(
    "/{booking_id}/guardian-approvals",
    response_model=GuardianApprovalRead,
    status_code=status.HTTP_201_CREATED,
)
async def decide_guardian_approval(
    booking_id: uuid.UUID,
    data: GuardianApprovalCreate,
    db: DbSession,
    actor: Actor,
    notifier: Notices,
):
    return await service.decide_guardian_approval(db, actor, booking_id, data, notifier)


# ---------------------------------------------------------------- shifts and check-ins
@router.get("/{booking_id}/shifts", response_model=list[ShiftRead])
async def list_shifts(booking_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.list_shifts(db, actor, booking_id)


@router.get("/{booking_id}/shifts/{shift_id}/check-ins", response_model=list[CheckInRead])
async def list_check_ins(booking_id: uuid.UUID, shift_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.list_check_ins(db, actor, booking_id, shift_id)


@router.post(
    "/{booking_id}/shifts/{shift_id}/check-ins",
    response_model=CheckInRead,
    status_code=status.HTTP_201_CREATED,
)
async def record_check_in(
    booking_id: uuid.UUID,
    shift_id: uuid.UUID,
    data: CheckInCreate,
    db: DbSession,
    actor: Actor,
    notifier: Notices,
):
    """GPS check-in or check-out (BK-3). Resending the same kind returns the original."""
    return await service.record_check_in(db, actor, booking_id, shift_id, data, notifier)


# ---------------------------------------------------------------- care notes
@router.get("/{booking_id}/shifts/{shift_id}/care-notes", response_model=list[CareNoteRead])
async def list_care_notes(booking_id: uuid.UUID, shift_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.list_care_notes(db, actor, booking_id, shift_id)


@router.post(
    "/{booking_id}/shifts/{shift_id}/care-notes",
    response_model=CareNoteRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_care_note(
    booking_id: uuid.UUID,
    shift_id: uuid.UUID,
    data: CareNoteCreate,
    db: DbSession,
    actor: Actor,
):
    return await service.add_care_note(db, actor, booking_id, shift_id, data)


# ---------------------------------------------------------------- reviews
@router.get("/{booking_id}/reviews", response_model=list[ReviewRead])
async def list_reviews(booking_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.list_reviews(db, actor, booking_id)


@router.post(
    "/{booking_id}/shifts/{shift_id}/reviews",
    response_model=ReviewRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_review(
    booking_id: uuid.UUID,
    shift_id: uuid.UUID,
    data: ReviewCreate,
    db: DbSession,
    actor: Actor,
):
    """Two-way rating after each shift (BK-4)."""
    return await service.create_review(db, actor, booking_id, shift_id, data)
