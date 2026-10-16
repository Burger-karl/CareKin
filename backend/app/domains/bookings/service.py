"""bookings business logic: Booking, GuardianApproval, Shift, CheckIn, Review, CareNote.

Who may do what with a booking (PRD section 4):
- patient side: anyone with access to the patient (see patients.service). Only the
                patient or a guardian books, cancels and reviews. A family member
                abroad funds care (payments domain) and can view bookings, seeing the
                service location only if the patient's share_scope grants location.
- caregiver:    the booked caregiver. Accepts, declines, checks in and out, writes
                care notes, reviews the family after each shift (BK-4).
- ops:          reads any booking (incidents and disputes). Can't read care notes.
- anyone else gets 404, so a booking's existence isn't leaked.

Status flow:
    pending_guardian -> requested            guardian approves (TS-3)
    pending_guardian -> declined             guardian rejects
    requested        -> accepted | declined  caregiver decides
    requested, pending_guardian, accepted -> cancelled   by a user, or expired (jobs.py)
    accepted         -> paid                 mark_paid(), called by the payments domain
    paid             -> in_progress          first check-in
    paid, in_progress -> completed           no shift left open and at least one ran
    paid, in_progress -> cancelled           every shift missed: a no-show (jobs.py)
    paid, in_progress, completed -> disputed mark_disputed(), called by trust (TS-5)

Shifts: scheduled -> in_progress -> completed, or scheduled -> missed (jobs.py), or
scheduled -> cancelled when the booking is cancelled, declined or expires.

Every write adds an audit_logs row in the same transaction. Care-note reads are
audited too, since notes can hold health data (HP-4). SMS notices (notifications.py)
go out after the commit and never fail the request.

The caregivers table isn't modelled yet, so caregivers are read through a minimal
table definition (`_caregivers`) matching ERD section 2.3. Until that table exists,
calls that need a caregiver fail with 503 caregivers_unavailable.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any, Literal

from fastapi import Request
from sqlalchemy import BigInteger, ColumnElement, String, Uuid, cast, column, func, select, table
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.exceptions import (
    CareKinError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.core.permissions import UserRole
from app.domains.auth.models import User
from app.domains.bookings import notifications as notices
from app.domains.bookings.models import (
    ApprovalDecision,
    Booking,
    BookingStatus,
    BookingType,
    CareNote,
    CheckIn,
    CheckInKind,
    GuardianApproval,
    Review,
    ReviewDirection,
    Shift,
    ShiftStatus,
)
from app.domains.bookings.schemas import (
    BookingCreate,
    BookingRead,
    CareNoteCreate,
    CheckInCreate,
    CheckInRead,
    GuardianApprovalCreate,
    ReviewCreate,
)
from app.domains.patients.models import FamilyLink, Guardian, Patient
from app.domains.patients.schemas import GeoPoint

# TODO: import from a shared module (e.g. app/core/geo.py) once these move out of patients.
from app.domains.patients.service import (
    PatientAccess,
    PatientNotFound,
    _from_wkb,
    _to_wkt,
    get_access,
)

logger = logging.getLogger(__name__)

# A check-in further than this from the service location is recorded but flagged.
GEOFENCE_RADIUS_M = 200
# How far ahead of the server clock a device timestamp may be.
CLOCK_SKEW = timedelta(minutes=5)
# R3: a check-in may be this early, by the device clock.
CHECK_IN_EARLY = timedelta(hours=1)
# R1/R3: a check-out may come this long after the shift's end, and a shift with no
# check-in becomes missed after it. Long enough for an offline phone to sync.
LATE_GRACE = timedelta(hours=12)
# R2: an accepted booking not paid within this is cancelled.
PAYMENT_WINDOW = timedelta(hours=24)
# TS-4: a minor's overnight care needs a guardian present for this long after the
# first completed booking between a caregiver and the patient.
FIRST_MONTH = timedelta(days=30)
# A booking touching these local hours counts as overnight for TS-4, whatever its type.
LAGOS = timezone(timedelta(hours=1), "Africa/Lagos")  # WAT, no daylight saving
NIGHT_START = time(22)
NIGHT_END = time(6)
# Daily and live-in bookings are split into shifts of at most this length.
SHIFT_LENGTH = timedelta(hours=24)
# Caregiver rates are in kobo.
DEFAULT_CURRENCY = "NGN"
# Bookings in these states hold the caregiver's time; a new one can't overlap them.
ACTIVE_STATUSES = (BookingStatus.ACCEPTED, BookingStatus.PAID, BookingStatus.IN_PROGRESS)
OVERNIGHT_TYPES = (BookingType.OVERNIGHT, BookingType.LIVE_IN)
EARTH_RADIUS_M = 6_371_008.8


# ---------------------------------------------------------------- errors
class BookingNotFound(NotFoundError):
    """Doesn't exist, or the caller has no access to it."""

    code = "booking_not_found"


class BookingAccessDenied(ForbiddenError):
    """The caller can see the booking but may not do this."""

    code = "booking_forbidden"


class BookingConflict(ConflictError):
    code = "booking_conflict"


class InvalidBookingStatus(ConflictError):
    """The booking isn't in a status that allows this."""

    code = "booking_invalid_status"


class BookingRuleViolation(CareKinError):
    """Breaks a business rule, e.g. TS-4 overnight without a guardian."""

    status_code = 422
    code = "booking_rule_violation"


class CaregiverNotFound(NotFoundError):
    code = "caregiver_not_found"


class CaregiversUnavailable(ServiceUnavailableError):
    """The caregivers table doesn't exist yet."""

    code = "caregivers_unavailable"


# ---------------------------------------------------------------- actor
@dataclass(frozen=True)
class Actor:
    """The caller. `request` supplies IP and user agent for the audit log."""

    user_id: uuid.UUID
    role: UserRole
    request: Request | None = None


def _audit(
    db: AsyncSession,
    actor: Actor | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    metadata: dict[str, Any] | None = None,
) -> None:
    write_audit(
        db,
        action=action,
        actor_id=actor.user_id if actor else None,
        entity_type=entity_type,
        entity_id=entity_id,
        request=actor.request if actor else None,
        metadata=metadata,
    )


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)


async def _commit(db: AsyncSession) -> None:
    """Commit, turning constraint failures into BookingConflict."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _sqlstate(exc) == "23505":
            raise BookingConflict("That record already exists") from exc
        raise BookingConflict("The change conflicts with existing data") from exc


# ---------------------------------------------------------------- caregivers
# Read-only view of the columns bookings needs (ERD 2.3). Replace with the
# Caregiver model once app/domains/caregivers/models.py defines it.
_caregivers = table(
    "caregivers",
    column("id", Uuid()),
    column("user_id", Uuid()),
    column("status", String()),
    column("hourly_rate_minor", BigInteger()),
)
CAREGIVER_BOOKABLE_STATUS = "approved"  # TS-1: only approved caregivers are bookable


@dataclass(frozen=True)
class CaregiverInfo:
    id: uuid.UUID
    user_id: uuid.UUID
    status: str
    hourly_rate_minor: int | None


async def _find_caregiver(db: AsyncSession, where: ColumnElement[bool]) -> CaregiverInfo | None:
    query = select(
        _caregivers.c.id,
        _caregivers.c.user_id,
        cast(_caregivers.c.status, String),  # a Postgres enum; compare as text
        _caregivers.c.hourly_rate_minor,
    ).where(where)
    try:
        row = (await db.execute(query)).first()
    except DBAPIError as exc:
        if _sqlstate(exc) != "42P01":  # undefined_table
            raise
        await db.rollback()
        raise CaregiversUnavailable(
            "Caregiver records aren't available yet, so this can't be done."
        ) from exc
    return CaregiverInfo(*row) if row else None


async def _caregiver_by_id(db: AsyncSession, caregiver_id: uuid.UUID) -> CaregiverInfo | None:
    return await _find_caregiver(db, _caregivers.c.id == caregiver_id)


async def _caregiver_by_user(db: AsyncSession, user_id: uuid.UUID) -> CaregiverInfo | None:
    return await _find_caregiver(db, _caregivers.c.user_id == user_id)


async def _lock_caregiver(db: AsyncSession, caregiver_id: uuid.UUID) -> None:
    """Serialise bookings for one caregiver until commit, so two can't both pass the clash check."""
    key = int.from_bytes(caregiver_id.bytes[:8], "big", signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(key)))


async def _has_clash(
    db: AsyncSession,
    caregiver_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    excluding: uuid.UUID | None = None,
) -> bool:
    query = select(Booking.id).where(
        Booking.caregiver_id == caregiver_id,
        Booking.status.in_(ACTIVE_STATUSES),
        Booking.starts_at < ends_at,
        Booking.ends_at > starts_at,
    )
    if excluding is not None:
        query = query.where(Booking.id != excluding)
    return await db.scalar(query.limit(1)) is not None


# ---------------------------------------------------------------- access
Role = Literal["patient_side", "caregiver", "ops"]


@dataclass(frozen=True)
class BookingAccess:
    booking: Booking
    role: Role
    actor_id: uuid.UUID
    patient: PatientAccess | None = None  # set for the patient side

    @property
    def can_manage(self) -> bool:
        """Cancel and review on the patient side: the patient or a guardian."""
        return self.patient is not None and self.patient.can_edit

    @property
    def sees_location(self) -> bool:
        if self.role != "patient_side" or self.patient is None:
            return True
        return self.patient.sees_location

    @property
    def sees_health_data(self) -> bool:
        if self.role == "caregiver":
            return True
        return self.patient is not None and self.patient.sees_health_data


async def get_booking_access(
    db: AsyncSession, actor: Actor, booking_id: uuid.UUID
) -> BookingAccess:
    booking = await db.get(Booking, booking_id)
    if booking is None:
        raise BookingNotFound("Booking not found")
    if actor.role is UserRole.OPS:
        return BookingAccess(booking, "ops", actor.user_id)
    if actor.role is UserRole.CAREGIVER:
        caregiver = await _caregiver_by_user(db, actor.user_id)
        if caregiver is not None and caregiver.id == booking.caregiver_id:
            return BookingAccess(booking, "caregiver", actor.user_id)
        raise BookingNotFound("Booking not found")
    try:
        patient = await get_access(db, booking.patient_id, actor.user_id)
    except PatientNotFound:
        raise BookingNotFound("Booking not found") from None
    return BookingAccess(booking, "patient_side", actor.user_id, patient)


def _require_caregiver(access: BookingAccess) -> None:
    if access.role != "caregiver":
        raise BookingAccessDenied("Only the booked caregiver can do this")


def _require_manage(access: BookingAccess) -> None:
    if not access.can_manage:
        raise BookingAccessDenied("Only the patient or a guardian can do this")


def _require_status(booking: Booking, *allowed: BookingStatus) -> None:
    if booking.status not in allowed:
        raise InvalidBookingStatus(
            f"This booking is {booking.status.value}, so that can't be done now",
            details={"status": booking.status.value, "allowed": [s.value for s in allowed]},
        )


def _needs_guardian(patient: Patient) -> bool:
    """Same rule as patients: no login, or a minor (ON-3, TS-3)."""
    return patient.user_id is None or patient.is_minor


# ---------------------------------------------------------------- shift bookkeeping
async def cancel_open_shifts(db: AsyncSession, booking_id: uuid.UUID) -> None:
    """R5: shifts that will never run are marked cancelled, not left scheduled."""
    for shift in await db.scalars(
        select(Shift).where(Shift.booking_id == booking_id, Shift.status == ShiftStatus.SCHEDULED)
    ):
        shift.status = ShiftStatus.CANCELLED


async def settle_booking(
    db: AsyncSession, booking: Booking
) -> Literal["completed", "no_show"] | None:
    """Close a paid or running booking once no shift is open (R1).

    Completed if at least one shift ran. If every shift was missed it's a no-show:
    cancelled, so the payments domain refunds it. Payments should only release escrow
    for completed shifts.
    """
    if booking.status not in (BookingStatus.PAID, BookingStatus.IN_PROGRESS):
        return None
    await db.flush()
    counts = dict(
        (
            await db.execute(
                select(Shift.status, func.count())
                .where(Shift.booking_id == booking.id)
                .group_by(Shift.status)
            )
        ).all()
    )
    if counts.get(ShiftStatus.SCHEDULED) or counts.get(ShiftStatus.IN_PROGRESS):
        return None
    if counts.get(ShiftStatus.COMPLETED):
        booking.status = BookingStatus.COMPLETED
        return "completed"
    booking.status = BookingStatus.CANCELLED
    return "no_show"


# ---------------------------------------------------------------- notices
def when(moment: datetime) -> str:
    """A time as people in Enugu read it, e.g. 'Mon 02 Nov, 08:00'."""
    return moment.astimezone(LAGOS).strftime("%a %d %b, %H:%M")


async def _phones(db: AsyncSession, user_ids: Iterable[uuid.UUID | None]) -> list[str]:
    ids = [i for i in user_ids if i is not None]
    if not ids:
        return []
    return list(await db.scalars(select(User.phone).where(User.id.in_(ids), User.is_active)))


async def booker_phones(db: AsyncSession, booking: Booking) -> list[str]:
    """Whoever booked, plus the patient if they have their own login."""
    patient_user = await db.scalar(select(Patient.user_id).where(Patient.id == booking.patient_id))
    return await _phones(db, [booking.booked_by, patient_user])


async def guardian_phones(
    db: AsyncSession, booking: Booking, *, approvers_only: bool = False
) -> list[str]:
    query = select(Guardian.user_id).where(Guardian.patient_id == booking.patient_id)
    if approvers_only:
        query = query.where(Guardian.can_approve_caregivers)
    return await _phones(db, await db.scalars(query))


async def safety_phones(db: AsyncSession, booking: Booking) -> list[str]:
    """For safety notices: the patient, the booker and every guardian."""
    return await booker_phones(db, booking) + await guardian_phones(db, booking)


async def caregiver_phones(db: AsyncSession, booking: Booking) -> list[str]:
    caregiver = await _caregiver_by_id(db, booking.caregiver_id)
    return await _phones(db, [caregiver.user_id] if caregiver else [])


async def notify(
    db: AsyncSession,
    notifier: notices.Notifier,
    recipients: Callable[[], Awaitable[list[str]]],
    body: str,
) -> None:
    """Look up recipients and send. Runs after commit, so it must never raise."""
    if notifier.sms is None:
        return
    try:
        phones = await recipients()
    except Exception:
        logger.warning("Couldn't look up recipients for a booking notice", exc_info=True)
        await db.rollback()
        return
    await notifier.send(phones, body)


# ---------------------------------------------------------------- helpers
def _now() -> datetime:
    return datetime.now(UTC)


def price_minor(rate_minor: int, starts_at: datetime, ends_at: datetime) -> int:
    """Hourly rate times the booked time, rounded up to the next minor unit."""
    seconds = int((ends_at - starts_at).total_seconds())
    return -(-rate_minor * seconds // 3600)


def split_into_shifts(
    booking_type: BookingType, starts_at: datetime, ends_at: datetime
) -> Iterator[tuple[datetime, datetime]]:
    """Hourly and overnight bookings are one shift; daily and live-in get one per 24 hours."""
    if booking_type in (BookingType.HOURLY, BookingType.OVERNIGHT):
        yield starts_at, ends_at
        return
    cursor = starts_at
    while cursor < ends_at:
        end = min(cursor + SHIFT_LENGTH, ends_at)
        yield cursor, end
        cursor = end


def covers_night(starts_at: datetime, ends_at: datetime) -> bool:
    """True if any part of the booking falls between 22:00 and 06:00 Lagos time."""
    day = starts_at.astimezone(LAGOS).date() - timedelta(days=1)
    last = ends_at.astimezone(LAGOS).date()
    while day <= last:
        night_start = datetime.combine(day, NIGHT_START, LAGOS)
        night_end = datetime.combine(day + timedelta(days=1), NIGHT_END, LAGOS)
        if starts_at < night_end and ends_at > night_start:
            return True
        day += timedelta(days=1)
    return False


def distance_m(a: GeoPoint, b: GeoPoint) -> int:
    """Great-circle distance in metres (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a.latitude, a.longitude, b.latitude, b.longitude))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return round(2 * EARTH_RADIUS_M * math.asin(math.sqrt(h)))


def to_read(access: BookingAccess) -> BookingRead:
    booking = access.booking
    location = access.sees_location
    return BookingRead(
        id=booking.id,
        patient_id=booking.patient_id,
        caregiver_id=booking.caregiver_id,
        booked_by=booking.booked_by,
        booking_type=booking.booking_type,
        status=booking.status,
        starts_at=booking.starts_at,
        ends_at=booking.ends_at,
        service_location=_from_wkb(booking.service_location) if location else None,
        service_address=booking.service_address if location else None,
        guardian_present=booking.guardian_present,
        rate_minor=booking.rate_minor,
        total_minor=booking.total_minor,
        currency=booking.currency,
        created_at=booking.created_at,
        access=access.role,
    )


def check_in_to_read(check_in: CheckIn) -> CheckInRead:
    return CheckInRead(
        id=check_in.id,
        shift_id=check_in.shift_id,
        kind=check_in.kind,
        location=_from_wkb(check_in.location),
        accuracy_m=check_in.accuracy_m,
        distance_m=check_in.distance_m,
        within_geofence=check_in.within_geofence,
        client_timestamp=check_in.client_timestamp,
        received_at=check_in.received_at,
        device_id=check_in.device_id,
    )


async def _check_overnight_rule(
    db: AsyncSession,
    patient: Patient,
    caregiver_id: uuid.UUID,
    data: BookingCreate,
) -> None:
    """TS-4: a minor's caregiver can't do overnight care without a guardian present in the
    first month. Any booking touching the night counts, so the type can't be used to skip it.
    """
    if not patient.is_minor or data.guardian_present:
        return
    if data.booking_type not in OVERNIGHT_TYPES and not covers_night(data.starts_at, data.ends_at):
        return
    first_completed = await db.scalar(
        select(func.min(Booking.starts_at)).where(
            Booking.patient_id == patient.id,
            Booking.caregiver_id == caregiver_id,
            Booking.status == BookingStatus.COMPLETED,
        )
    )
    if first_completed is None or data.starts_at < first_completed + FIRST_MONTH:
        raise BookingRuleViolation(
            "A guardian must be present for a minor's overnight care during the first "
            "month with a caregiver",
            code="guardian_presence_required",
            details={
                "first_completed_booking": first_completed.isoformat() if first_completed else None
            },
        )


# ---------------------------------------------------------------- bookings
async def create_booking(
    db: AsyncSession,
    actor: Actor,
    data: BookingCreate,
    notifier: notices.Notifier = notices.NO_NOTICES,
) -> BookingRead:
    patient_access = await get_access(db, data.patient_id, actor.user_id)
    if not patient_access.can_edit:
        # PRD section 4: family abroad funds care; the patient or a guardian books it.
        raise BookingAccessDenied("Only the patient or a guardian can book care")
    patient = patient_access.patient
    guardian: Guardian | None = None
    if patient_access.role == "guardian":
        guardian = await db.scalar(
            select(Guardian).where(
                Guardian.patient_id == patient.id, Guardian.user_id == actor.user_id
            )
        )

    if data.starts_at <= _now():
        raise BookingRuleViolation("A booking must start in the future")
    caregiver = await _caregiver_by_id(db, data.caregiver_id)
    if caregiver is None:
        raise CaregiverNotFound("Caregiver not found")
    if caregiver.status != CAREGIVER_BOOKABLE_STATUS:
        raise BookingRuleViolation("This caregiver isn't approved for bookings")
    if caregiver.hourly_rate_minor is None:
        raise BookingRuleViolation("This caregiver hasn't set a rate yet")
    if caregiver.user_id in (actor.user_id, patient.user_id):
        raise BookingRuleViolation("A caregiver can't be booked to care for themselves")

    await _lock_caregiver(db, caregiver.id)
    if await _has_clash(db, caregiver.id, data.starts_at, data.ends_at):
        raise BookingConflict(
            "The caregiver already has a booking at that time", code="caregiver_unavailable"
        )
    await _check_overnight_rule(db, patient, caregiver.id, data)

    # TS-3: a minor's booking waits for a guardian, unless a guardian who may approve booked it.
    self_approved = guardian is not None and guardian.can_approve_caregivers
    if _needs_guardian(patient) and not self_approved:
        status = BookingStatus.PENDING_GUARDIAN
    else:
        status = BookingStatus.REQUESTED

    booking = Booking(
        patient_id=patient.id,
        caregiver_id=caregiver.id,
        booked_by=actor.user_id,
        booking_type=data.booking_type,
        status=status,
        starts_at=data.starts_at,
        ends_at=data.ends_at,
        service_location=_to_wkt(data.service_location),
        service_address=data.service_address,
        guardian_present=data.guardian_present,
        rate_minor=caregiver.hourly_rate_minor,
        total_minor=price_minor(caregiver.hourly_rate_minor, data.starts_at, data.ends_at),
        currency=DEFAULT_CURRENCY,
    )
    db.add(booking)
    for start, end in split_into_shifts(data.booking_type, data.starts_at, data.ends_at):
        db.add(Shift(booking_id=booking.id, scheduled_start=start, scheduled_end=end))
    if _needs_guardian(patient) and self_approved:
        assert guardian is not None
        db.add(
            GuardianApproval(
                booking_id=booking.id, guardian_id=guardian.id, decision=ApprovalDecision.APPROVED
            )
        )
    _audit(
        db,
        actor,
        "booking.create",
        "booking",
        booking.id,
        {
            "patient_id": str(patient.id),
            "caregiver_id": str(caregiver.id),
            "booking_type": data.booking_type.value,
            "status": status.value,
        },
    )
    await _commit(db)
    await db.refresh(booking)
    if status is BookingStatus.PENDING_GUARDIAN:
        await notify(
            db,
            notifier,
            lambda: guardian_phones(db, booking, approvers_only=True),
            notices.approval_needed(when(booking.starts_at)),
        )
    else:
        await notify(
            db,
            notifier,
            lambda: caregiver_phones(db, booking),
            notices.new_request(booking.booking_type.value, when(booking.starts_at)),
        )
    return to_read(BookingAccess(booking, "patient_side", actor.user_id, patient_access))


async def _patient_accesses(db: AsyncSession, actor: Actor) -> dict[uuid.UUID, PatientAccess]:
    """Every patient the caller is connected to, keyed by id."""
    own = select(Patient.id).where(Patient.user_id == actor.user_id)
    guarded = select(Guardian.patient_id).where(Guardian.user_id == actor.user_id)
    linked = select(FamilyLink.patient_id).where(FamilyLink.user_id == actor.user_id)
    ids = set(await db.scalars(own.union(guarded, linked)))
    return {pid: await get_access(db, pid, actor.user_id) for pid in ids}


async def list_bookings(
    db: AsyncSession,
    actor: Actor,
    status: BookingStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[BookingRead]:
    """The caller's bookings, newest start first."""
    query = select(Booking)
    patients: dict[uuid.UUID, PatientAccess] = {}
    role: Role
    if actor.role is UserRole.OPS:
        role = "ops"
    elif actor.role is UserRole.CAREGIVER:
        role = "caregiver"
        caregiver = await _caregiver_by_user(db, actor.user_id)
        if caregiver is None:
            return []
        query = query.where(Booking.caregiver_id == caregiver.id)
    else:
        role = "patient_side"
        patients = await _patient_accesses(db, actor)
        if not patients:
            return []
        query = query.where(Booking.patient_id.in_(patients))
    if status is not None:
        query = query.where(Booking.status == status)
    query = query.order_by(Booking.starts_at.desc()).limit(limit).offset(offset)
    return [
        to_read(BookingAccess(b, role, actor.user_id, patients.get(b.patient_id)))
        for b in await db.scalars(query)
    ]


async def get_booking(db: AsyncSession, actor: Actor, booking_id: uuid.UUID) -> BookingRead:
    return to_read(await get_booking_access(db, actor, booking_id))


async def accept_booking(
    db: AsyncSession,
    actor: Actor,
    booking_id: uuid.UUID,
    notifier: notices.Notifier = notices.NO_NOTICES,
) -> BookingRead:
    access = await get_booking_access(db, actor, booking_id)
    _require_caregiver(access)
    booking = access.booking
    _require_status(booking, BookingStatus.REQUESTED)
    if booking.starts_at <= _now():
        raise BookingRuleViolation("This booking's start time has passed")
    await _lock_caregiver(db, booking.caregiver_id)
    if await _has_clash(
        db, booking.caregiver_id, booking.starts_at, booking.ends_at, excluding=booking.id
    ):
        raise BookingConflict(
            "You already have a booking at that time", code="caregiver_unavailable"
        )
    booking.status = BookingStatus.ACCEPTED
    booking.accepted_at = _now()
    _audit(db, actor, "booking.accept", "booking", booking.id)
    await _commit(db)
    await notify(
        db, notifier, lambda: booker_phones(db, booking), notices.accepted(when(booking.starts_at))
    )
    return to_read(access)


async def decline_booking(
    db: AsyncSession,
    actor: Actor,
    booking_id: uuid.UUID,
    notifier: notices.Notifier = notices.NO_NOTICES,
) -> BookingRead:
    access = await get_booking_access(db, actor, booking_id)
    _require_caregiver(access)
    booking = access.booking
    _require_status(booking, BookingStatus.REQUESTED)
    booking.status = BookingStatus.DECLINED
    await cancel_open_shifts(db, booking.id)
    _audit(db, actor, "booking.decline", "booking", booking_id)
    await _commit(db)
    await notify(
        db, notifier, lambda: booker_phones(db, booking), notices.declined(when(booking.starts_at))
    )
    return to_read(access)


async def cancel_booking(
    db: AsyncSession,
    actor: Actor,
    booking_id: uuid.UUID,
    notifier: notices.Notifier = notices.NO_NOTICES,
) -> BookingRead:
    """Either side can cancel before payment. After that, money is in escrow and ops decide."""
    access = await get_booking_access(db, actor, booking_id)
    if access.role != "caregiver":
        _require_manage(access)
    _require_status(
        access.booking,
        BookingStatus.PENDING_GUARDIAN,
        BookingStatus.REQUESTED,
        BookingStatus.ACCEPTED,
    )
    booking = access.booking
    previous = booking.status
    booking.status = BookingStatus.CANCELLED
    await cancel_open_shifts(db, booking.id)
    _audit(
        db,
        actor,
        "booking.cancel",
        "booking",
        booking_id,
        {"previous_status": previous.value, "by": access.role},
    )
    await _commit(db)
    # Tell the other side. The caregiver only knows about it once it reached them.
    if access.role == "caregiver":
        await notify(
            db,
            notifier,
            lambda: booker_phones(db, booking),
            notices.cancelled(when(booking.starts_at)),
        )
    elif previous is not BookingStatus.PENDING_GUARDIAN:
        await notify(
            db,
            notifier,
            lambda: caregiver_phones(db, booking),
            notices.cancelled(when(booking.starts_at)),
        )
    return to_read(access)


# Called by other domains inside their own transaction. They commit.
async def mark_paid(db: AsyncSession, booking_id: uuid.UUID) -> Booking:
    """Payments domain: the payment succeeded and funds are held in escrow."""
    booking = await db.get(Booking, booking_id, with_for_update=True)
    if booking is None:
        raise BookingNotFound("Booking not found")
    _require_status(booking, BookingStatus.ACCEPTED)
    booking.status = BookingStatus.PAID
    _audit(db, None, "booking.paid", "booking", booking.id)
    return booking


async def mark_disputed(
    db: AsyncSession, booking_id: uuid.UUID, actor: Actor | None = None
) -> Booking:
    """Trust domain: a dispute was raised, so escrow must be held (TS-5)."""
    booking = await db.get(Booking, booking_id, with_for_update=True)
    if booking is None:
        raise BookingNotFound("Booking not found")
    _require_status(booking, BookingStatus.PAID, BookingStatus.IN_PROGRESS, BookingStatus.COMPLETED)
    previous = booking.status
    booking.status = BookingStatus.DISPUTED
    _audit(db, actor, "booking.dispute", "booking", booking.id, {"previous_status": previous.value})
    return booking


async def assigned_caregiver_ids(
    db: AsyncSession, patient_id: uuid.UUID, at: datetime | None = None
) -> list[uuid.UUID]:
    """Crisis domain: caregivers on a paid or running booking for the patient right now (CR-1)."""
    at = at or _now()
    result = await db.scalars(
        select(Booking.caregiver_id)
        .where(
            Booking.patient_id == patient_id,
            Booking.status.in_((BookingStatus.PAID, BookingStatus.IN_PROGRESS)),
            Booking.starts_at <= at,
            Booking.ends_at > at,
        )
        .distinct()
    )
    return list(result)


# ---------------------------------------------------------------- guardian approval
async def list_guardian_approvals(
    db: AsyncSession, actor: Actor, booking_id: uuid.UUID
) -> list[GuardianApproval]:
    access = await get_booking_access(db, actor, booking_id)
    if access.role == "caregiver":
        raise BookingAccessDenied("Guardian decisions are visible to the family only")
    result = await db.scalars(
        select(GuardianApproval)
        .where(GuardianApproval.booking_id == booking_id)
        .order_by(GuardianApproval.decided_at)
    )
    return list(result)


async def decide_guardian_approval(
    db: AsyncSession,
    actor: Actor,
    booking_id: uuid.UUID,
    data: GuardianApprovalCreate,
    notifier: notices.Notifier = notices.NO_NOTICES,
) -> GuardianApproval:
    """TS-3: a guardian approves or rejects the caregiver for a minor's booking."""
    access = await get_booking_access(db, actor, booking_id)
    guardian = None
    if access.patient is not None and access.patient.role == "guardian":
        guardian = await db.scalar(
            select(Guardian).where(
                Guardian.patient_id == access.booking.patient_id,
                Guardian.user_id == actor.user_id,
            )
        )
    if guardian is None or not guardian.can_approve_caregivers:
        raise BookingAccessDenied("Only a guardian allowed to approve caregivers can do this")
    booking = access.booking
    _require_status(booking, BookingStatus.PENDING_GUARDIAN)
    approval = GuardianApproval(
        booking_id=booking.id,
        guardian_id=guardian.id,
        decision=data.decision,
        reason=data.reason,
    )
    db.add(approval)
    approved = data.decision is ApprovalDecision.APPROVED
    booking.status = BookingStatus.REQUESTED if approved else BookingStatus.DECLINED
    if not approved:
        await cancel_open_shifts(db, booking.id)
    _audit(
        db,
        actor,
        "booking.guardian_approve" if approved else "booking.guardian_reject",
        "booking",
        booking.id,
        {"guardian_id": str(guardian.id)},
    )
    await _commit(db)
    await db.refresh(approval)
    if approved:
        await notify(
            db,
            notifier,
            lambda: caregiver_phones(db, booking),
            notices.new_request(booking.booking_type.value, when(booking.starts_at)),
        )
    else:
        await notify(
            db,
            notifier,
            lambda: booker_phones(db, booking),
            notices.guardian_rejected(when(booking.starts_at)),
        )
    return approval


# ---------------------------------------------------------------- shifts and check-ins
async def _get_shift(db: AsyncSession, booking_id: uuid.UUID, shift_id: uuid.UUID) -> Shift:
    shift = await db.get(Shift, shift_id)
    if shift is None or shift.booking_id != booking_id:
        raise BookingNotFound("Shift not found")
    return shift


async def list_shifts(db: AsyncSession, actor: Actor, booking_id: uuid.UUID) -> list[Shift]:
    await get_booking_access(db, actor, booking_id)
    result = await db.scalars(
        select(Shift).where(Shift.booking_id == booking_id).order_by(Shift.scheduled_start)
    )
    return list(result)


async def list_check_ins(
    db: AsyncSession, actor: Actor, booking_id: uuid.UUID, shift_id: uuid.UUID
) -> list[CheckInRead]:
    access = await get_booking_access(db, actor, booking_id)
    if not access.sees_location:
        raise BookingAccessDenied("The patient hasn't shared location with you")
    await _get_shift(db, booking_id, shift_id)
    result = await db.scalars(
        select(CheckIn).where(CheckIn.shift_id == shift_id).order_by(CheckIn.client_timestamp)
    )
    return [check_in_to_read(c) for c in result]


async def record_check_in(
    db: AsyncSession,
    actor: Actor,
    booking_id: uuid.UUID,
    shift_id: uuid.UUID,
    data: CheckInCreate,
    notifier: notices.Notifier = notices.NO_NOTICES,
) -> CheckInRead:
    """BK-3: GPS check-in or check-out. Offline replays return the original record."""
    access = await get_booking_access(db, actor, booking_id)
    _require_caregiver(access)
    booking = access.booking
    shift = await _get_shift(db, booking_id, shift_id)

    async def _existing() -> CheckIn | None:
        return await db.scalar(
            select(CheckIn).where(CheckIn.shift_id == shift_id, CheckIn.kind == data.kind)
        )

    if (existing := await _existing()) is not None:
        return check_in_to_read(existing)

    _require_status(booking, BookingStatus.PAID, BookingStatus.IN_PROGRESS)
    ts = data.client_timestamp
    if ts > _now() + CLOCK_SKEW:
        raise BookingRuleViolation("client_timestamp is in the future; check the device clock")
    if data.kind is CheckInKind.CHECK_IN:
        if shift.status is ShiftStatus.MISSED:
            raise BookingRuleViolation(
                "This shift was marked missed. Contact CareKin support.", code="shift_missed"
            )
        if shift.status is not ShiftStatus.SCHEDULED:
            raise BookingRuleViolation("This shift has already started")
        # R3: by the device clock, so an offline check-in is judged by when it happened.
        opens = shift.scheduled_start - CHECK_IN_EARLY
        if not opens <= ts < shift.scheduled_end:
            raise BookingRuleViolation(
                "Check-in is only possible from 1 hour before the shift until it ends",
                code="outside_shift_window",
                details={
                    "opens_at": opens.isoformat(),
                    "closes_at": shift.scheduled_end.isoformat(),
                },
            )
    else:
        if shift.status is not ShiftStatus.IN_PROGRESS or shift.actual_start is None:
            raise BookingRuleViolation("Check in before checking out")
        if ts < shift.actual_start:
            raise BookingRuleViolation("Check-out can't be before check-in")
        closes = shift.scheduled_end + LATE_GRACE
        if ts > closes:
            raise BookingRuleViolation(
                "Check-out is only possible up to 12 hours after the shift ends",
                code="outside_shift_window",
                details={"closes_at": closes.isoformat()},
            )

    centre = _from_wkb(booking.service_location)
    assert centre is not None  # service_location is NOT NULL
    distance = distance_m(centre, data.location)
    check_in = CheckIn(
        shift_id=shift.id,
        kind=data.kind,
        location=_to_wkt(data.location),
        accuracy_m=data.accuracy_m,
        distance_m=distance,
        within_geofence=distance <= GEOFENCE_RADIUS_M,
        client_timestamp=ts,
        device_id=data.device_id,
    )
    db.add(check_in)
    try:
        await db.flush()
    except IntegrityError:
        # R4: an identical request won the race; hand back what it stored.
        await db.rollback()
        if (existing := await _existing()) is not None:
            return check_in_to_read(existing)
        raise

    if data.kind is CheckInKind.CHECK_IN:
        shift.actual_start = ts
        shift.status = ShiftStatus.IN_PROGRESS
        booking.status = BookingStatus.IN_PROGRESS
    else:
        shift.actual_end = ts
        shift.status = ShiftStatus.COMPLETED
        await settle_booking(db, booking)

    _audit(
        db,
        actor,
        f"booking.{data.kind.value}",
        "check_in",
        check_in.id,
        {
            "booking_id": str(booking.id),
            "shift_id": str(shift.id),
            "within_geofence": check_in.within_geofence,
            "distance_m": distance,
        },
    )
    await _commit(db)
    await db.refresh(check_in)
    if not check_in.within_geofence:  # R7
        await notify(
            db,
            notifier,
            lambda: safety_phones(db, booking),
            notices.outside_geofence(data.kind.value, distance),
        )
    return check_in_to_read(check_in)


async def list_flagged_check_ins(
    db: AsyncSession,
    actor: Actor,
    since: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple[CheckIn, uuid.UUID]]:
    """R7: ops queue of check-ins made outside the geofence, newest first."""
    if actor.role is not UserRole.OPS:
        raise BookingAccessDenied("Only operations can see flagged check-ins")
    query = (
        select(CheckIn, Shift.booking_id)
        .join(Shift, Shift.id == CheckIn.shift_id)
        .where(CheckIn.within_geofence.is_(False))
    )
    if since is not None:
        query = query.where(CheckIn.received_at >= since)
    query = query.order_by(CheckIn.received_at.desc()).limit(limit).offset(offset)
    return [(row[0], row[1]) for row in (await db.execute(query)).all()]


# ---------------------------------------------------------------- reviews
async def list_reviews(db: AsyncSession, actor: Actor, booking_id: uuid.UUID) -> list[Review]:
    await get_booking_access(db, actor, booking_id)
    result = await db.scalars(
        select(Review).where(Review.booking_id == booking_id).order_by(Review.created_at)
    )
    return list(result)


async def create_review(
    db: AsyncSession,
    actor: Actor,
    booking_id: uuid.UUID,
    shift_id: uuid.UUID,
    data: ReviewCreate,
) -> Review:
    """BK-4: two-way ratings after each shift."""
    access = await get_booking_access(db, actor, booking_id)
    booking = access.booking
    if access.role == "caregiver":
        direction = ReviewDirection.CAREGIVER_TO_FAMILY
        reviewee_id = booking.booked_by
        if reviewee_id is None:
            raise BookingRuleViolation("The account that made this booking no longer exists")
    else:
        _require_manage(access)
        direction = ReviewDirection.FAMILY_TO_CAREGIVER
        caregiver = await _caregiver_by_id(db, booking.caregiver_id)
        if caregiver is None:
            raise CaregiverNotFound("Caregiver not found")
        reviewee_id = caregiver.user_id
    shift = await _get_shift(db, booking_id, shift_id)
    if shift.status is not ShiftStatus.COMPLETED:
        raise BookingRuleViolation("A shift can be rated once it's checked out")
    already = await db.scalar(
        select(Review.id).where(Review.shift_id == shift.id, Review.reviewer_id == actor.user_id)
    )
    if already is not None:
        raise BookingConflict("You've already rated this shift")
    review = Review(
        booking_id=booking.id,
        shift_id=shift.id,
        reviewer_id=actor.user_id,
        reviewee_id=reviewee_id,
        direction=direction,
        rating=data.rating,
        comment=data.comment,
    )
    db.add(review)
    _audit(
        db,
        actor,
        "review.create",
        "review",
        review.id,
        {
            "booking_id": str(booking.id),
            "shift_id": str(shift.id),
            "direction": direction.value,
            "rating": data.rating,
        },
    )
    await _commit(db)
    await db.refresh(review)
    return review


# ---------------------------------------------------------------- care notes
async def list_care_notes(
    db: AsyncSession, actor: Actor, booking_id: uuid.UUID, shift_id: uuid.UUID
) -> list[CareNote]:
    access = await get_booking_access(db, actor, booking_id)
    if not access.sees_health_data:
        raise BookingAccessDenied("Care notes are visible to the caregiver and the family only")
    await _get_shift(db, booking_id, shift_id)
    notes = list(
        await db.scalars(
            select(CareNote).where(CareNote.shift_id == shift_id).order_by(CareNote.created_at)
        )
    )
    if notes:
        _audit(
            db,
            actor,
            "care_note.view",
            "shift",
            shift_id,
            {"booking_id": str(booking_id), "count": len(notes)},
        )
        await _commit(db)
    return notes


async def add_care_note(
    db: AsyncSession,
    actor: Actor,
    booking_id: uuid.UUID,
    shift_id: uuid.UUID,
    data: CareNoteCreate,
) -> CareNote:
    access = await get_booking_access(db, actor, booking_id)
    _require_caregiver(access)
    _require_status(access.booking, BookingStatus.IN_PROGRESS, BookingStatus.COMPLETED)
    shift = await _get_shift(db, booking_id, shift_id)
    if shift.status not in (ShiftStatus.IN_PROGRESS, ShiftStatus.COMPLETED):
        raise BookingRuleViolation("Notes can only be added to a shift that has started")
    note = CareNote(shift_id=shift.id, author_id=actor.user_id, body=data.body)
    db.add(note)
    _audit(db, actor, "care_note.create", "care_note", note.id, {"booking_id": str(booking_id)})
    await _commit(db)
    await db.refresh(note)
    return note
