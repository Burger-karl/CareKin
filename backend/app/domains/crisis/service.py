"""crisis business logic (PRD 7.2, CR-1 to CR-5).

Flow when the button is pressed:
1. The event and one queued alert per contact are written in one transaction
   (an outbox: if the process dies right after, the alerts are still on record).
2. The API responds at once with the event and the nearest verified facilities.
3. Alerts are sent by SMS in the background, in the same process, so they never
   depend on Redis or a Celery worker being up. Celery Beat (crisis.sweep_alerts)
   retries anything that failed or got stuck; POST /crisis/alert retries on demand.

Access reuses the patients domain's rules (patients.service.get_access): the patient
and their guardians can press, follow and close; family links can follow if the
patient shares their location; ops can follow and close any event.

Nothing here gives treatment advice. Alert texts name only the patient's first
name, never their condition, because SMS previews show on lock screens.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import Request
from geoalchemy2 import WKTElement
from sqlalchemy import select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.audit import write_audit
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.permissions import UserRole
from app.core.phone import mask_phone
from app.domains.auth.models import ConsentType, User
from app.domains.auth.service import has_consent
from app.domains.crisis import facilities
from app.domains.crisis.facilities import NearbyResult
from app.domains.crisis.models import (
    AlertStatus,
    CrisisAlert,
    CrisisEvent,
    CrisisStatus,
    LocationSource,
)
from app.domains.crisis.schemas import CrisisResolveIn, CrisisTriggerIn
from app.domains.passport.models import PassportEntry, PassportEntryType
from app.domains.patients.models import AlertChannel, EmergencyContact, Guardian, Patient
from app.domains.patients.service import get_access
from app.integrations.sms import SMSClient, SMSDeliveryError

logger = logging.getLogger(__name__)

MAX_DEVICE_CLOCK_AHEAD = timedelta(minutes=1)
MAX_OFFLINE_DELAY = timedelta(hours=24)
# The retry sweep ignores alerts older than this: a day-old crisis SMS helps nobody.
SWEEP_MAX_ALERT_AGE = timedelta(hours=6)

Purpose = Literal["trigger", "follow", "manage"]


def _now() -> datetime:
    return datetime.now(UTC)


def _first_name(full_name: str) -> str:
    parts = full_name.split()
    return parts[0] if parts else "Your contact"


# ------------------------------------------------------------------ access
async def patient_for(
    db: AsyncSession, user: User, patient_id: uuid.UUID, purpose: Purpose
) -> Patient:
    """The patient, if `user` may `purpose` their crisis events.

    trigger  the patient or a guardian
    follow   also ops, and family links whose share scope includes location
    manage   (resolve, resend alerts) the patient, a guardian or ops
    Anyone unrelated gets 404 from get_access, so a patient's existence isn't revealed.
    """
    if user.role is UserRole.OPS and purpose != "trigger":
        patient = await db.get(Patient, patient_id)
        if patient is None:
            raise NotFoundError("Patient not found", code="patient_not_found")
        return patient
    access = await get_access(db, patient_id, user.id)
    if access.role in ("self", "guardian"):
        return access.patient
    if purpose == "follow" and access.sees_location:
        return access.patient
    raise ForbiddenError("Only the patient or a guardian can do this.", code="crisis_access_denied")


async def load_event(
    db: AsyncSession, event_id: uuid.UUID, *, for_update: bool = False
) -> CrisisEvent:
    stmt = (
        select(CrisisEvent)
        .where(CrisisEvent.id == event_id)
        .options(selectinload(CrisisEvent.alerts))
        .execution_options(populate_existing=True)
    )
    if for_update:
        stmt = stmt.with_for_update(of=CrisisEvent)
    event = await db.scalar(stmt)
    if event is None:
        raise NotFoundError("Crisis event not found", code="crisis_not_found")
    return event


async def event_for(
    db: AsyncSession,
    user: User,
    event_id: uuid.UUID,
    purpose: Purpose,
    *,
    for_update: bool = False,
) -> CrisisEvent:
    event = await load_event(db, event_id, for_update=for_update)
    try:
        await patient_for(db, user, event.patient_id, purpose)
    except NotFoundError:
        raise NotFoundError("Crisis event not found", code="crisis_not_found") from None
    return event


# ------------------------------------------------------------------ trigger
@dataclass
class TriggerResult:
    event_id: uuid.UUID
    created: bool


def _accept_device_time(device_time: datetime | None, now: datetime) -> datetime:
    """Use the time the button was pressed offline, unless the device clock is implausible."""
    if device_time is None:
        return now
    if device_time > now + MAX_DEVICE_CLOCK_AHEAD or device_time < now - MAX_OFFLINE_DELAY:
        logger.warning("Ignoring implausible device time for a crisis trigger")
        return now
    return device_time


async def trigger_crisis(
    db: AsyncSession, user: User, data: CrisisTriggerIn, request: Request | None = None
) -> TriggerResult:
    settings = get_settings()
    patient = await patient_for(db, user, data.patient_id, "trigger")
    # Plain values: a rollback expires loaded objects, and reloading one would be implicit IO.
    patient_id, user_id, user_phone = patient.id, user.id, user.phone

    # Same press sent again (retry, offline replay): return the original event.
    existing = await db.scalar(
        select(CrisisEvent).where(CrisisEvent.client_event_id == data.client_event_id)
    )
    if existing is not None:
        if existing.patient_id != patient_id:
            raise ConflictError(
                "This client_event_id belongs to another crisis", code="client_event_id_reused"
            )
        return TriggerResult(existing.id, created=False)

    # A second press during an active crisis must not alert everyone twice.
    now = _now()
    if settings.CRISIS_DUPLICATE_WINDOW_SECONDS:
        active = await db.scalar(
            select(CrisisEvent)
            .where(
                CrisisEvent.patient_id == patient_id,
                CrisisEvent.status == CrisisStatus.ACTIVE,
                CrisisEvent.triggered_at
                >= now - timedelta(seconds=settings.CRISIS_DUPLICATE_WINDOW_SECONDS),
            )
            .order_by(CrisisEvent.triggered_at.desc())
            .limit(1)
        )
        if active is not None:
            return TriggerResult(active.id, created=False)

    event = CrisisEvent(
        patient_id=patient_id,
        triggered_by=user_id,
        client_event_id=data.client_event_id,
        triggered_at=_accept_device_time(data.triggered_at, now),
        status=CrisisStatus.ACTIVE,
    )
    if data.location is not None:
        loc = data.location
        event.location = WKTElement(f"POINT({loc.longitude!r} {loc.latitude!r})", srid=4326)
        event.location_source = LocationSource.DEVICE
        event.accuracy_m = loc.accuracy_m
        event.location_captured_at = loc.captured_at
    elif patient.home_location is not None:
        # Copied inside the database: no Python geometry library needed.
        event.location = (
            select(Patient.home_location).where(Patient.id == patient_id).scalar_subquery()
        )
        event.location_source = LocationSource.HOME
    else:
        event.location_source = LocationSource.NONE
    db.add(event)
    event_id = event.id

    alerts = await _queue_alerts(db, event_id, patient_id, exclude_phones={user_phone})
    write_audit(
        db,
        action="crisis.trigger",
        actor_id=user_id,
        entity_type="crisis_event",
        entity_id=event_id,
        request=request,
        metadata={"location_source": event.location_source.value, "alerts_queued": len(alerts)},
    )
    try:
        await db.commit()
    except IntegrityError:
        # Two copies of the same press arrived at once; the other one won.
        await db.rollback()
        winner = await db.scalar(
            select(CrisisEvent).where(CrisisEvent.client_event_id == data.client_event_id)
        )
        if winner is not None and winner.patient_id == patient_id:
            return TriggerResult(winner.id, created=False)
        raise
    logger.info("Crisis %s triggered, %d alerts queued", event_id, len(alerts))
    return TriggerResult(event_id, created=True)


async def _queue_alerts(
    db: AsyncSession, event_id: uuid.UUID, patient_id: uuid.UUID, *, exclude_phones: set[str]
) -> list[CrisisAlert]:
    """One queued SMS alert per guardian and emergency contact, deduplicated by phone.

    Guardians come first (primary first), then contacts by priority. Whoever pressed
    the button, and anyone already alerted, is skipped. Guardians who turned off
    crisis alerts, and deactivated accounts, are skipped too.
    """
    guardians = await db.execute(
        select(Guardian.id, User.full_name, User.phone)
        .join(User, User.id == Guardian.user_id)
        .where(
            Guardian.patient_id == patient_id,
            Guardian.receives_crisis_alerts.is_(True),
            User.is_active.is_(True),
        )
        .order_by(Guardian.is_primary.desc(), Guardian.created_at)
    )
    contacts = await db.execute(
        select(EmergencyContact.id, EmergencyContact.name, EmergencyContact.phone)
        .where(EmergencyContact.patient_id == patient_id)
        .order_by(EmergencyContact.priority, EmergencyContact.name)
    )
    seen = set(exclude_phones)
    alerts: list[CrisisAlert] = []
    recipients = [("guardian", *row) for row in guardians] + [("contact", *row) for row in contacts]
    for kind, recipient_id, name, phone in recipients:
        if phone in seen:
            continue
        seen.add(phone)
        alert = CrisisAlert(
            crisis_event_id=event_id,
            guardian_id=recipient_id if kind == "guardian" else None,
            emergency_contact_id=recipient_id if kind == "contact" else None,
            recipient_name=name,
            phone=phone,
            # SMS for every crisis alert: it reaches basic phones without data.
            channel=AlertChannel.SMS,
            status=AlertStatus.QUEUED,
            attempts=0,
        )
        db.add(alert)
        alerts.append(alert)
    return alerts


# ------------------------------------------------------------------ facilities
async def nearby_facilities(db: AsyncSession, event: CrisisEvent) -> NearbyResult:
    """Verified facilities that can receive a patient in crisis, nearest first (CR-2).

    Only verified listings (or verified-but-due-for-recheck) are shown: in an
    emergency an unverified claim of "24h haematology" is worse than no answer.
    Empty with `available=False` until the facilities domain exists (see facilities.py).
    """
    return await facilities.nearby(db, event.latitude, event.longitude)


# ------------------------------------------------------------------ resolve
async def resolve_crisis(
    db: AsyncSession,
    user: User,
    event_id: uuid.UUID,
    data: CrisisResolveIn,
    request: Request | None = None,
) -> uuid.UUID | None:
    """Close the crisis. Returns the new passport entry id, if one was written (CR-4)."""
    event = await event_for(db, user, event_id, "manage", for_update=True)
    if event.status is not CrisisStatus.ACTIVE:
        raise ConflictError("This crisis is already closed.", code="crisis_closed")

    facility_name = None
    if data.outcome_facility_id is not None:
        facility_name = await facilities.facility_name(db, data.outcome_facility_id)
        if facility_name is None:
            raise NotFoundError("Facility not found", code="facility_not_found")

    entry_id = None
    if data.add_to_passport:
        # ON-2: same rule as the patients domain: the person writing health data has consented.
        if not await has_consent(db, user.id, ConsentType.HEALTH_DATA):
            raise ForbiddenError(
                "Consent is required before this information can be stored.",
                code="consent_required",
                details={"consent_type": ConsentType.HEALTH_DATA.value, "next": "/auth/consent"},
            )
        summary = "Crisis button pressed."
        if facility_name is not None:
            summary += f" Went to {facility_name}."
        entry = PassportEntry(
            patient_id=event.patient_id,
            entry_type=PassportEntryType.CRISIS,
            occurred_at=event.triggered_at,
            summary=summary,
            details=data.note,
            facility_id=data.outcome_facility_id,
            crisis_event_id=event.id,
            created_by=user.id,
        )
        db.add(entry)
        entry_id = entry.id
        write_audit(
            db,
            action="passport.entry.create",
            actor_id=user.id,
            entity_type="passport_entry",
            entity_id=entry.id,
            request=request,
            metadata={"source": "crisis_resolve"},
        )

    event.status = CrisisStatus.CANCELLED if data.false_alarm else CrisisStatus.RESOLVED
    event.resolved_at = _now()
    event.resolved_by = user.id
    event.outcome_facility_id = data.outcome_facility_id
    write_audit(
        db,
        action="crisis.cancel" if data.false_alarm else "crisis.resolve",
        actor_id=user.id,
        entity_type="crisis_event",
        entity_id=event.id,
        request=request,
        metadata={"added_to_passport": entry_id is not None},
    )
    await db.commit()
    return entry_id


# ------------------------------------------------------------------ manual retry
@dataclass
class RetryResult:
    new_recipients: int
    queued: int


async def retry_alerts(
    db: AsyncSession, user: User, event_id: uuid.UUID, request: Request | None = None
) -> RetryResult:
    """Re-queue failed alerts and alert contacts added since the crisis began."""
    event = await event_for(db, user, event_id, "manage", for_update=True)
    if event.status is not CrisisStatus.ACTIVE:
        raise ConflictError("This crisis is already closed.", code="crisis_closed")
    already = {a.phone for a in event.alerts}
    if event.triggered_by is not None:
        presser = await db.get(User, event.triggered_by)
        if presser is not None:
            already.add(presser.phone)
    new_alerts = await _queue_alerts(db, event.id, event.patient_id, exclude_phones=already)
    for alert in event.alerts:
        if alert.status is AlertStatus.FAILED:
            alert.status = AlertStatus.QUEUED
    queued = sum(a.status is AlertStatus.QUEUED for a in event.alerts) + len(new_alerts)
    write_audit(
        db,
        action="crisis.alert.retry",
        actor_id=user.id,
        entity_type="crisis_event",
        entity_id=event.id,
        request=request,
        metadata={"new_recipients": len(new_alerts), "queued": queued},
    )
    await db.commit()
    return RetryResult(new_recipients=len(new_alerts), queued=queued)


# ------------------------------------------------------------------ delivery
def build_alert_message(patient: Patient, event: CrisisEvent) -> str:
    """The SMS text. First name only and no condition: previews show on lock screens."""
    settings = get_settings()
    when = event.triggered_at.astimezone(ZoneInfo(settings.TIMEZONE)).strftime("%H:%M")
    if event.latitude is not None and event.longitude is not None:
        link = f"https://maps.google.com/?q={event.latitude:.5f},{event.longitude:.5f}"
        where = (
            f"Location: {link}"
            if event.location_source is LocationSource.DEVICE
            else f"No live location. Home: {link}"
        )
    else:
        where = "Location unavailable."
    return (
        f"CareKin ALERT: {_first_name(patient.full_name)} pressed the crisis button at {when}. "
        f"{where} Please call them now. Emergency: {settings.EMERGENCY_NUMBER}"
    )


@dataclass
class DeliveryReport:
    sent: int = 0
    failed: int = 0
    skipped: int = 0


async def deliver_pending_alerts(
    session_factory: async_sessionmaker[AsyncSession],
    sms: SMSClient,
    *,
    event_id: uuid.UUID | None = None,
    ignore_max_attempts: bool = False,
    min_idle: timedelta | None = None,
    max_age: timedelta | None = None,
    concurrency: int = 3,
) -> DeliveryReport:
    """Send queued and failed alerts. Safe to run from several processes at once.

    Each alert is claimed with SELECT ... FOR UPDATE SKIP LOCKED and committed on its
    own, so parallel runs (API background task, Beat sweep, manual retry) never send
    the same alert concurrently, and progress survives a crash part-way through.
    Delivery is at-least-once: if the process dies between sending and committing,
    that one SMS may be sent again, which is better than not at all.
    """
    settings = get_settings()
    report = DeliveryReport()
    attempted: set[uuid.UUID] = set()

    def claim_query():
        now = _now()
        conditions = [CrisisAlert.status.in_([AlertStatus.QUEUED, AlertStatus.FAILED])]
        if attempted:
            conditions.append(CrisisAlert.id.not_in(attempted))
        if not ignore_max_attempts:
            conditions.append(CrisisAlert.attempts < settings.CRISIS_ALERT_MAX_ATTEMPTS)
        if event_id is not None:
            conditions.append(CrisisAlert.crisis_event_id == event_id)
        if min_idle is not None:
            conditions.append(CrisisAlert.updated_at <= now - min_idle)
        if max_age is not None:
            conditions.append(CrisisAlert.created_at >= now - max_age)
        return (
            select(CrisisAlert)
            .where(true(), *conditions)
            .order_by(CrisisAlert.created_at, CrisisAlert.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    async def worker() -> None:
        while True:
            async with session_factory() as db:
                alert = await db.scalar(claim_query())
                if alert is None:
                    return
                if alert.id in attempted:
                    # Another worker in this run tried it after our query was built
                    # (it failed, so it still matched). Once per run is enough.
                    await db.rollback()
                    continue
                attempted.add(alert.id)
                event = await db.get(CrisisEvent, alert.crisis_event_id)
                if event.status is not CrisisStatus.ACTIVE:
                    alert.status = AlertStatus.SKIPPED
                    alert.last_error = "Crisis closed before this alert was sent"
                    await db.commit()
                    report.skipped += 1
                    continue
                patient = await db.get(Patient, event.patient_id)
                alert.attempts += 1
                try:
                    sent = await sms.send(alert.phone, build_alert_message(patient, event))
                except SMSDeliveryError as exc:
                    alert.status = AlertStatus.FAILED
                    alert.last_error = str(exc)[:300]
                    report.failed += 1
                    logger.warning(
                        "Crisis alert %s to %s failed: %s", alert.id, mask_phone(alert.phone), exc
                    )
                except Exception as exc:  # one bad alert must not stop the others
                    alert.status = AlertStatus.FAILED
                    alert.last_error = f"Unexpected error: {type(exc).__name__}"
                    report.failed += 1
                    logger.exception("Unexpected error sending crisis alert %s", alert.id)
                else:
                    alert.status = AlertStatus.SENT
                    alert.sent_at = sent.sent_at
                    alert.provider_ref = sent.provider_ref
                    alert.last_error = None
                    report.sent += 1
                await db.commit()

    await asyncio.gather(*(worker() for _ in range(max(1, concurrency))))
    if report.sent or report.failed or report.skipped:
        logger.info(
            "Crisis alert delivery: %d sent, %d failed, %d skipped",
            report.sent,
            report.failed,
            report.skipped,
        )
    return report


async def sweep_pending_alerts(
    session_factory: async_sessionmaker[AsyncSession], sms: SMSClient
) -> DeliveryReport:
    """Retry alerts that failed or were never picked up. Run by Celery Beat."""
    settings = get_settings()
    return await deliver_pending_alerts(
        session_factory,
        sms,
        min_idle=timedelta(seconds=settings.CRISIS_ALERT_RETRY_AFTER_SECONDS),
        max_age=SWEEP_MAX_ALERT_AGE,
    )


# ------------------------------------------------------------------ output
def location_out(event: CrisisEvent) -> dict[str, Any]:
    settings = get_settings()
    stale = bool(
        event.location_source is LocationSource.DEVICE
        and event.location_captured_at is not None
        and (event.triggered_at - event.location_captured_at).total_seconds()
        > settings.CRISIS_LOCATION_STALE_SECONDS
    )
    return {
        "source": event.location_source,
        "latitude": event.latitude,
        "longitude": event.longitude,
        "accuracy_m": event.accuracy_m,
        "captured_at": event.location_captured_at,
        "stale": stale,
    }


def alert_out(alert: CrisisAlert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "recipient_type": alert.recipient_type,
        "recipient_name": alert.recipient_name,
        "phone": mask_phone(alert.phone),
        "channel": alert.channel,
        "status": alert.status,
        "attempts": alert.attempts,
        "sent_at": alert.sent_at,
    }


def event_out(event: CrisisEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "patient_id": event.patient_id,
        "client_event_id": event.client_event_id,
        "status": event.status,
        "triggered_at": event.triggered_at,
        "resolved_at": event.resolved_at,
        "outcome_facility_id": event.outcome_facility_id,
        "location": location_out(event),
        "alerts": [alert_out(a) for a in event.alerts],
        "no_contacts": not event.alerts,
    }


def facilities_out(event: CrisisEvent, nearby: NearbyResult) -> dict[str, Any]:
    return {
        "crisis_event_id": event.id,
        "location_source": event.location_source,
        "radius_km": nearby.radius_km,
        "widened_search": nearby.widened,
        "facilities_available": nearby.available,
        "facilities": nearby.facilities,
    }
