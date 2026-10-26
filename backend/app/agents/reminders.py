"""Reminders for the care companion (Day 15, PRD section 8): "hydration and medication
reminders, appointment reminders" and daily check-ins. Reminders only, never advice.

- The patient or a guardian sets a reminder for the patient; it goes to whoever set it
  (a guardian gets their child's reminders, an adult patient their own). Each person
  sees and changes only their own reminders.
- The wording is fixed per kind (REMINDER_TEXTS): nobody, the model included, can make
  a reminder say something else. Medication reminders never name a medicine or dose.
- Times are Lagos local time. `next_due_at` (UTC) is worked out when a reminder is saved
  and after each send, so Beat finds due reminders with one indexed query.
- Delivery (`send_due_reminders`, Beat every minute) claims due reminders and moves them
  on before sending: a reminder is sent at most once, never twice. One that is too late
  (worker down) is skipped, and one whose recipient lost access to the patient stops.
- WhatsApp reminders use Meta-approved templates (WHATSAPP_TEMPLATES): free text only
  reaches someone who messaged us in the last 24 hours. A failed send isn't retried; a
  missed water reminder is not worth a second message an hour late.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import schemas as s
from app.agents.models import Reminder, ReminderKind
from app.config import get_settings
from app.core.audit import write_audit
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.phone import mask_phone
from app.domains.auth.models import User
from app.domains.bookings.service import LAGOS
from app.domains.patients.models import AlertChannel, Guardian, Patient
from app.domains.patients.service import PatientAccessDenied, get_access
from app.integrations.base import IntegrationError
from app.integrations.sms import SMSClient, SMSDeliveryError
from app.integrations.whatsapp import WhatsAppClient

logger = logging.getLogger(__name__)

MAX_ACTIVE_REMINDERS = 10  # per person per patient: reminders, not spam

# Approved wording. {name} is the patient's first name.
REMINDER_TEXTS: dict[ReminderKind, str] = {
    ReminderKind.HYDRATION: "CareKin reminder for {name}: time for a drink of water.",
    ReminderKind.MEDICATION: (
        "CareKin reminder for {name}: time for the medicine the doctor prescribed."
    ),
    ReminderKind.APPOINTMENT: (
        "CareKin reminder for {name}: hospital appointment today. Take the health passport along."
    ),
    ReminderKind.CHECK_IN: (
        "Hi from CareKin. How is {name} today? Reply here any time. "
        "In an emergency, press the Crisis button or call {emergency}."
    ),
}
# The same wording, approved by Meta as templates with the first name as {{1}}.
WHATSAPP_TEMPLATES: dict[ReminderKind, str] = {
    kind: f"carekin_reminder_{kind.value}" for kind in ReminderKind
}
# How the companion and the fallback replies name each kind.
KIND_WORDS: dict[ReminderKind, str] = {
    ReminderKind.HYDRATION: "water",
    ReminderKind.MEDICATION: "medicine",
    ReminderKind.APPOINTMENT: "appointment",
    ReminderKind.CHECK_IN: "daily check-in",
}
ALL_DAYS = list(range(7))


class ReminderNotFound(NotFoundError):
    code = "reminder_not_found"


# ---------------------------------------------------------------- schedule
def next_due(
    local_time: time, days: list[int], on_date: date | None, after: datetime
) -> datetime | None:
    """The first time strictly after `after` the reminder fires, in UTC. None if never."""
    if on_date is not None:
        due = datetime.combine(on_date, local_time, LAGOS)
        return due.astimezone(UTC) if due > after else None
    today = after.astimezone(LAGOS).date()
    for offset in range(8):  # today, the next six days, and today next week
        day = today + timedelta(days=offset)
        if day.weekday() in days:
            due = datetime.combine(day, local_time, LAGOS)
            if due > after:
                return due.astimezone(UTC)
    return None


def first_name(patient: Patient) -> str:
    return patient.full_name.split()[0] if patient.full_name.strip() else "you"


def reminder_text(kind: ReminderKind, name: str) -> str:
    return REMINDER_TEXTS[kind].format(name=name, emergency=get_settings().EMERGENCY_NUMBER)


def describe(reminder: s.ReminderRead) -> str:
    """Plain words, e.g. "water at 08:00 every day", for replies built without the model."""
    when = reminder.local_time.strftime("%H:%M")
    if reminder.on_date is not None:
        schedule = f"on {reminder.on_date.strftime('%a %d %b')}"
    elif len(reminder.days) == 7:
        schedule = "every day"
    else:
        schedule = "on " + ", ".join(day.title() for day in reminder.days)
    return f"{KIND_WORDS[reminder.kind]} at {when} {schedule}"


def to_read(reminder: Reminder, patient: Patient) -> s.ReminderRead:
    return s.ReminderRead(
        id=reminder.id,
        patient_id=reminder.patient_id,
        kind=reminder.kind,
        local_time=reminder.local_time,
        days=[s.WEEKDAYS[day] for day in sorted(reminder.days_of_week)],
        on_date=reminder.on_date,
        channel=reminder.channel.value,
        active=reminder.active,
        next_due_at=reminder.next_due_at,
        last_sent_at=reminder.last_sent_at,
        text=reminder_text(reminder.kind, first_name(patient)),
        created_at=reminder.created_at,
    )


# ---------------------------------------------------------------- service
async def _patient(db: AsyncSession, user: User, patient_id: uuid.UUID) -> Patient:
    access = await get_access(db, patient_id, user.id)  # 404 without access
    if not access.can_edit:
        raise PatientAccessDenied("Only the patient or a guardian can set reminders")
    return access.patient


def _schedule(reminder: Reminder, now: datetime) -> None:
    """Work out next_due_at; 400 if an active reminder would never fire."""
    if not reminder.active:
        reminder.next_due_at = None
        return
    reminder.next_due_at = next_due(
        reminder.local_time, reminder.days_of_week, reminder.on_date, now
    )
    if reminder.next_due_at is None:
        raise BadRequestError("That date and time has already passed", code="reminder_in_past")


async def _check_limit(db: AsyncSession, user: User, patient_id: uuid.UUID) -> None:
    active = await db.scalar(
        select(func.count())
        .select_from(Reminder)
        .where(
            Reminder.patient_id == patient_id,
            Reminder.created_by == user.id,
            Reminder.active.is_(True),
        )
    )
    if (active or 0) >= MAX_ACTIVE_REMINDERS:
        raise ConflictError(
            f"You already have {MAX_ACTIVE_REMINDERS} reminders for this patient. "
            "Switch one off first.",
            code="too_many_reminders",
        )


async def create_reminder(
    db: AsyncSession,
    user: User,
    patient_id: uuid.UUID,
    data: s.ReminderCreate,
    *,
    request: Request | None = None,
) -> s.ReminderRead:
    patient = await _patient(db, user, patient_id)
    await _check_limit(db, user, patient.id)
    days = [] if data.on_date else [s.WEEKDAYS.index(d) for d in data.days or s.WEEKDAYS]
    reminder = Reminder(
        patient_id=patient.id,
        kind=data.kind,
        local_time=data.local_time,
        days_of_week=days,
        on_date=data.on_date,
        channel=AlertChannel(data.channel),
        active=True,
        created_by=user.id,
    )
    _schedule(reminder, datetime.now(UTC))
    db.add(reminder)
    await db.flush()
    write_audit(
        db,
        action="reminder.create",
        actor_id=user.id,
        entity_type="reminder",
        entity_id=reminder.id,
        request=request,
        metadata={"patient_id": str(patient.id), "kind": data.kind.value},
    )
    await db.commit()
    await db.refresh(reminder)
    return to_read(reminder, patient)


async def list_reminders(
    db: AsyncSession, user: User, patient_id: uuid.UUID
) -> list[s.ReminderRead]:
    """The caller's own reminders for this patient, active first."""
    patient = await _patient(db, user, patient_id)
    rows = await db.scalars(
        select(Reminder)
        .where(Reminder.patient_id == patient.id, Reminder.created_by == user.id)
        .order_by(Reminder.active.desc(), Reminder.local_time, Reminder.created_at)
    )
    return [to_read(row, patient) for row in rows]


async def update_reminder(
    db: AsyncSession,
    user: User,
    patient_id: uuid.UUID,
    reminder_id: uuid.UUID,
    data: s.ReminderUpdate,
    *,
    request: Request | None = None,
) -> s.ReminderRead:
    patient = await _patient(db, user, patient_id)
    reminder = await db.scalar(
        select(Reminder)
        .where(
            Reminder.id == reminder_id,
            Reminder.patient_id == patient.id,
            Reminder.created_by == user.id,
        )
        .with_for_update()
    )
    if reminder is None:
        raise ReminderNotFound("Reminder not found")
    changed = sorted(data.model_fields_set)
    if data.active and not reminder.active:
        await _check_limit(db, user, patient.id)
    if data.active is not None:
        reminder.active = data.active
    if data.local_time is not None:
        reminder.local_time = data.local_time
    if data.days is not None:
        reminder.days_of_week = [s.WEEKDAYS.index(d) for d in data.days]
        reminder.on_date = None
    if data.on_date is not None:
        reminder.on_date = data.on_date
        reminder.days_of_week = []
    if data.channel is not None:
        reminder.channel = AlertChannel(data.channel)
    _schedule(reminder, datetime.now(UTC))
    write_audit(
        db,
        action="reminder.update",
        actor_id=user.id,
        entity_type="reminder",
        entity_id=reminder.id,
        request=request,
        metadata={"fields": changed},
    )
    await db.commit()
    await db.refresh(reminder)
    return to_read(reminder, patient)


async def stop_whatsapp_reminders(db: AsyncSession, user: User) -> int:
    """Switch off every WhatsApp reminder this person receives (they replied STOP)."""
    rows = (
        await db.scalars(
            select(Reminder)
            .where(
                Reminder.created_by == user.id,
                Reminder.channel == AlertChannel.WHATSAPP,
                Reminder.active.is_(True),
            )
            .with_for_update()
        )
    ).all()
    for reminder in rows:
        reminder.active = False
        reminder.next_due_at = None
    if rows:
        write_audit(
            db,
            action="reminder.stop_whatsapp",
            actor_id=user.id,
            entity_type="user",
            entity_id=user.id,
            metadata={"reminders": len(rows)},
        )
    await db.commit()
    return len(rows)


# ---------------------------------------------------------------- delivery
@dataclass
class ReminderReport:
    sent: int = 0
    failed: int = 0
    skipped: int = 0  # too late to be useful
    stopped: int = 0  # the recipient no longer has access to the patient


@dataclass(frozen=True)
class _Send:
    reminder_id: uuid.UUID
    kind: ReminderKind
    channel: AlertChannel
    to: str
    name: str


async def _still_allowed(db: AsyncSession, reminder: Reminder) -> tuple[User, Patient] | None:
    """The recipient and patient, if the recipient may still act for the patient."""
    user = await db.get(User, reminder.created_by)
    patient = await db.get(Patient, reminder.patient_id)
    if user is None or patient is None or not user.is_active:
        return None
    if patient.user_id == user.id:
        return user, patient
    guardian = await db.scalar(
        select(Guardian.id).where(Guardian.patient_id == patient.id, Guardian.user_id == user.id)
    )
    return (user, patient) if guardian is not None else None


async def send_due_reminders(
    db: AsyncSession,
    *,
    whatsapp: WhatsAppClient | None,
    sms: SMSClient | None,
    now: datetime | None = None,
    limit: int = 200,
) -> ReminderReport:
    """Send every reminder that is due. Beat runs this every minute.

    A client is None when that channel isn't configured; its reminders count as failed.
    """
    now = now or datetime.now(UTC)
    grace = timedelta(minutes=get_settings().REMINDER_GRACE_MINUTES)
    report = ReminderReport()
    due = (
        await db.scalars(
            select(Reminder)
            .where(Reminder.active.is_(True), Reminder.next_due_at <= now)
            .order_by(Reminder.next_due_at)
            .limit(limit)
            .with_for_update(skip_locked=True)  # two workers never take the same reminder
        )
    ).all()

    # 1. Claim: move each reminder on before anything is sent.
    sends: list[_Send] = []
    for reminder in due:
        was_due = reminder.next_due_at
        allowed = await _still_allowed(db, reminder)
        if reminder.on_date is not None:
            reminder.active = False
            reminder.next_due_at = None
        else:
            reminder.next_due_at = next_due(reminder.local_time, reminder.days_of_week, None, now)
        if allowed is None:
            reminder.active = False
            reminder.next_due_at = None
            report.stopped += 1
        elif was_due is not None and now - was_due > grace:
            report.skipped += 1
        else:
            user, patient = allowed
            sends.append(
                _Send(reminder.id, reminder.kind, reminder.channel, user.phone, first_name(patient))
            )
    await db.commit()

    # 2. Send.
    for send in sends:
        try:
            if send.channel is AlertChannel.SMS and sms is not None:
                await sms.send(send.to, reminder_text(send.kind, send.name))
            elif send.channel is AlertChannel.WHATSAPP and whatsapp is not None:
                await whatsapp.send_template(
                    send.to, WHATSAPP_TEMPLATES[send.kind], parameters=[send.name]
                )
            else:
                raise IntegrationError(send.channel.value, "channel not configured")
        except (IntegrationError, SMSDeliveryError, ValueError) as exc:
            report.failed += 1
            logger.warning(
                "Reminder %s to %s not sent: %s", send.reminder_id, mask_phone(send.to), exc
            )
            continue
        reminder = await db.get(Reminder, send.reminder_id)
        if reminder is not None:
            reminder.last_sent_at = datetime.now(UTC)
        report.sent += 1
    await db.commit()
    return report
