"""trust business logic: incidents and disputes (TS-2, TS-5).

Who may do what:
- report:  anyone signed in. With a booking: only someone who can see it (the patient
           side, the booked caregiver, ops); others get 404, as for the booking itself.
           A dispute (which holds money) needs a stake: the patient or a guardian,
           family allowed to fund care, the caregiver, or ops.
- read:    the reporter, the person it's about, and ops. Others get 404.
- update:  ops only (status, severity, assignment, resolution, flag as a dispute).

Disputes (TS-5): an incident with is_dispute on a booking whose payment is held puts
the booking into `disputed` and the escrow `on_hold_dispute`, so nothing is released.
A safety report is never lost to a partner outage: our state holds the money and the
settlement sweep tells the partner later. When the last open dispute on a booking is
resolved or dismissed, the booking goes back to its status before the dispute and the
escrow is held normally again (then released or refunded as usual). To settle a dispute
in the family's favour, ops refunds first (allowed during a dispute), then closes it.

Every write is audited. Descriptions and resolutions are encrypted and never put in
the audit metadata.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.exceptions import CareKinError, ConflictError, NotFoundError
from app.core.permissions import UserRole
from app.domains.auth.models import User
from app.domains.bookings import service as bookings
from app.domains.bookings.models import Booking, BookingStatus
from app.domains.bookings.notifications import NO_NOTICES, Notifier
from app.domains.caregivers.models import Caregiver
from app.domains.payments import notifications as texts
from app.domains.payments import service as payments
from app.domains.trust.models import OPEN_STATUSES, Incident, IncidentStatus
from app.domains.trust.rules import response_due
from app.domains.trust.schemas import IncidentCreate, IncidentUpdate
from app.integrations.escrow import EscrowClient

Actor = bookings.Actor

# Allowed status moves. Closed incidents are final; a new report can be filed.
TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.OPEN: frozenset(
        {IncidentStatus.INVESTIGATING, IncidentStatus.RESOLVED, IncidentStatus.DISMISSED}
    ),
    IncidentStatus.INVESTIGATING: frozenset({IncidentStatus.RESOLVED, IncidentStatus.DISMISSED}),
    IncidentStatus.RESOLVED: frozenset(),
    IncidentStatus.DISMISSED: frozenset(),
}
DISPUTABLE = (BookingStatus.PAID, BookingStatus.IN_PROGRESS, BookingStatus.COMPLETED)


class IncidentNotFound(NotFoundError):
    code = "incident_not_found"


class IncidentConflict(ConflictError):
    code = "incident_conflict"


class IncidentRuleViolation(CareKinError):
    status_code = 422
    code = "incident_rule_violation"


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(
    db: AsyncSession,
    actor: Actor | None,
    action: str,
    incident_id: uuid.UUID,
    metadata: dict[str, object] | None = None,
) -> None:
    write_audit(
        db,
        action=action,
        actor_id=actor.user_id if actor else None,
        entity_type="incident",
        entity_id=incident_id,
        request=actor.request if actor else None,
        metadata=metadata,
    )


async def _caregiver_user(db: AsyncSession, booking: Booking) -> uuid.UUID | None:
    return await db.scalar(select(Caregiver.user_id).where(Caregiver.id == booking.caregiver_id))


async def _open_dispute(
    db: AsyncSession,
    actor: Actor | None,
    incident: Incident,
    booking_id: uuid.UUID,
    escrow_client: EscrowClient,
) -> bool:
    """Hold the booking's payment for `incident`. True if this started the dispute."""
    booking = await payments.lock_booking(db, booking_id)
    if booking.status is not BookingStatus.DISPUTED and booking.status not in DISPUTABLE:
        raise IncidentConflict(
            f"A {booking.status.value} booking has no payment held to dispute",
            code="nothing_to_dispute",
        )
    await payments.hold_for_dispute(db, booking.id, escrow_client)
    if booking.status is BookingStatus.DISPUTED:
        return False  # already on hold for an earlier dispute
    incident.booking_status_before = booking.status
    await bookings.mark_disputed(db, booking.id, actor)
    return True


async def _close_dispute_if_last(
    db: AsyncSession, actor: Actor | None, incident: Incident, escrow_client: EscrowClient
) -> bool:
    """After a dispute incident closes: if none is still open, end the hold. True if ended."""
    if not incident.is_dispute or incident.booking_id is None:
        return False
    booking = await payments.lock_booking(db, incident.booking_id)
    still_open = await db.scalar(
        select(Incident.id)
        .where(
            Incident.booking_id == booking.id,
            Incident.is_dispute.is_(True),
            Incident.status.in_(OPEN_STATUSES),
            Incident.id != incident.id,
        )
        .limit(1)
    )
    if still_open is not None:
        return False
    await payments.end_dispute_hold(db, booking.id, escrow_client)
    if booking.status is not BookingStatus.DISPUTED:
        return True  # e.g. ops refunded first: the booking is cancelled, nothing to restore
    restore_to = await db.scalar(
        select(Incident.booking_status_before)
        .where(Incident.booking_id == booking.id, Incident.booking_status_before.is_not(None))
        .order_by(Incident.created_at.desc())
        .limit(1)
    )
    await bookings.end_dispute(db, booking.id, restore_to or BookingStatus.COMPLETED, actor)
    return True


async def _notify_dispute(
    db: AsyncSession, notifier: Notifier, booking_id: uuid.UUID, opened: bool
) -> None:
    booking = await db.get(Booking, booking_id)
    if booking is None:
        return
    when = bookings.when(booking.starts_at)
    body = texts.dispute_opened(when) if opened else texts.dispute_closed(when)
    await bookings.notify(db, notifier, lambda: bookings.safety_phones(db, booking), body)
    await bookings.notify(db, notifier, lambda: bookings.caregiver_phones(db, booking), body)


# ---------------------------------------------------------------- create
async def create_incident(
    db: AsyncSession,
    actor: Actor,
    data: IncidentCreate,
    *,
    escrow_client: EscrowClient,
    notifier: Notifier = NO_NOTICES,
) -> Incident:
    """Report an incident (TS-2); the response clock starts now."""
    against = data.against_user_id
    if data.booking_id is not None:
        access = await bookings.get_booking_access(db, actor, data.booking_id)
        booking = access.booking
        # Holding money is for those with a stake in it: the patient or a guardian, family
        # who may fund care, the caregiver, or ops. Anyone with access can still report.
        if (
            data.is_dispute
            and access.role == "patient_side"
            and not access.can_manage
            and not await payments.can_fund(db, booking.patient_id, actor.user_id)
        ):
            raise IncidentRuleViolation(
                "Only the patient, a guardian or family who funds care can open a dispute",
                code="dispute_not_allowed",
            )
        caregiver_user = await _caregiver_user(db, booking)
        parties = {booking.booked_by, caregiver_user}
        if against is None:
            if access.role == "caregiver":
                against = booking.booked_by
            elif access.role == "patient_side":
                against = caregiver_user
        elif against not in parties:
            raise IncidentRuleViolation(
                "against_user_id must be someone on this booking", code="against_not_on_booking"
            )
    elif against is not None and await db.get(User, against) is None:
        raise IncidentRuleViolation("against_user_id is not a CareKin user")
    if against == actor.user_id:
        against = None  # a report about oneself names nobody

    now = _now()
    incident = Incident(
        booking_id=data.booking_id,
        reported_by=actor.user_id,
        against_user_id=against,
        category=data.category,
        severity=data.severity,
        is_dispute=data.is_dispute,
        status=IncidentStatus.OPEN,
        description=data.description,
        response_due_at=response_due(data.severity, now),
    )
    db.add(incident)
    started = False
    if data.is_dispute:
        assert data.booking_id is not None
        started = await _open_dispute(db, actor, incident, data.booking_id, escrow_client)
    _audit(
        db,
        actor,
        "incident.create",
        incident.id,
        {
            "booking_id": str(data.booking_id) if data.booking_id else None,
            "category": data.category.value,
            "severity": data.severity.value,
            "is_dispute": data.is_dispute,
        },
    )
    await payments.commit(db)
    await db.refresh(incident)
    if started and data.booking_id is not None:
        await _notify_dispute(db, notifier, data.booking_id, opened=True)
    return incident


# ---------------------------------------------------------------- read
async def get_incident(db: AsyncSession, actor: Actor, incident_id: uuid.UUID) -> Incident:
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFound("Incident not found")
    if actor.role is UserRole.OPS or actor.user_id in (
        incident.reported_by,
        incident.against_user_id,
    ):
        return incident
    raise IncidentNotFound("Incident not found")


async def list_incidents(
    db: AsyncSession,
    actor: Actor,
    *,
    status: IncidentStatus | None = None,
    booking_id: uuid.UUID | None = None,
    disputes_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Incident]:
    """Ops: the queue, most urgent first. Everyone else: the incidents they reported."""
    query = select(Incident)
    if actor.role is not UserRole.OPS:
        query = query.where(Incident.reported_by == actor.user_id)
    if status is not None:
        query = query.where(Incident.status == status)
    if booking_id is not None:
        query = query.where(Incident.booking_id == booking_id)
    if disputes_only:
        query = query.where(Incident.is_dispute.is_(True))
    query = (
        query.order_by(Incident.response_due_at, Incident.created_at).limit(limit).offset(offset)
    )
    return list(await db.scalars(query))


# ---------------------------------------------------------------- update
async def update_incident(
    db: AsyncSession,
    actor: Actor,
    incident_id: uuid.UUID,
    data: IncidentUpdate,
    *,
    escrow_client: EscrowClient,
    notifier: Notifier = NO_NOTICES,
) -> Incident:
    if actor.role is not UserRole.OPS:
        raise IncidentNotFound("Incident not found")
    found = await db.get(Incident, incident_id)
    if found is None:
        raise IncidentNotFound("Incident not found")
    # Booking first, then the incident: the same lock order as payments.
    if found.booking_id is not None:
        await payments.lock_booking(db, found.booking_id)
    incident = await db.get(Incident, incident_id, with_for_update=True, populate_existing=True)
    assert incident is not None
    if incident.status not in OPEN_STATUSES:
        raise IncidentConflict("This incident is closed", code="incident_closed")

    fields = data.model_fields_set
    changes: dict[str, object] = {}
    now = _now()
    started = ended = False

    if "assigned_to" in fields and data.assigned_to != incident.assigned_to:
        if data.assigned_to is not None:
            assignee = await db.get(User, data.assigned_to)
            if assignee is None or assignee.role is not UserRole.OPS or not assignee.is_active:
                raise IncidentRuleViolation("Incidents can only be assigned to an active ops user")
        incident.assigned_to = data.assigned_to
        changes["assigned_to"] = str(data.assigned_to) if data.assigned_to else None

    if data.severity is not None and data.severity is not incident.severity:
        incident.severity = data.severity
        if incident.first_response_at is None:
            incident.response_due_at = response_due(data.severity, incident.created_at)
        changes["severity"] = data.severity.value

    if "is_dispute" in fields:
        if data.is_dispute is not True:
            raise IncidentRuleViolation(
                "To end a dispute, resolve or dismiss the incident", code="dispute_flag_one_way"
            )
        if not incident.is_dispute:
            if incident.booking_id is None:
                raise IncidentRuleViolation("Only an incident about a booking can be a dispute")
            incident.is_dispute = True
            started = await _open_dispute(db, actor, incident, incident.booking_id, escrow_client)
            changes["is_dispute"] = True

    if "resolution" in fields:
        incident.resolution = data.resolution

    if data.status is not None and data.status is not incident.status:
        if data.status not in TRANSITIONS[incident.status]:
            raise IncidentConflict(
                f"An incident can't go from {incident.status.value} to {data.status.value}",
                code="invalid_transition",
            )
        if data.status in (IncidentStatus.RESOLVED, IncidentStatus.DISMISSED):
            if not incident.resolution:
                raise IncidentRuleViolation(
                    "Say how it was resolved (resolution) before closing an incident",
                    code="resolution_required",
                )
            incident.resolved_at = now
        incident.status = data.status
        changes["status"] = data.status.value
        if data.status not in OPEN_STATUSES:
            ended = await _close_dispute_if_last(db, actor, incident, escrow_client)

    if incident.first_response_at is None and ("status" in changes or "assigned_to" in changes):
        incident.first_response_at = now
    if not changes and "resolution" not in fields:
        return incident
    _audit(db, actor, "incident.update", incident.id, changes or {"resolution": "updated"})
    await payments.commit(db)
    await db.refresh(incident)
    if incident.booking_id is not None and (started or ended):
        await _notify_dispute(db, notifier, incident.booking_id, opened=started)
    return incident
