"""Simulate crisis button presses end to end, for demos and load checks (Days 3 and 9).

    python -m seed.crisis_simulator                       # 3 presses
    python -m seed.crisis_simulator --presses 20 --fail-every 4 --resolve

For each press it creates (or reuses) a demo patient with a guardian and two emergency
contacts, presses the button through the real service layer, lists the nearest verified
facilities and delivers the alerts. It also replays one press to show nobody is
alerted twice. Run `python -m seed.facilities --demo-verified` first to see facilities.

SMS always goes to an in-memory client created here, whatever SMS_PROVIDER says, so
the simulator can never text a real number. Demo phone numbers use +2347000xxxxxx.
Refused in production and staging.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from geoalchemy2 import WKTElement
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import UserRole
from app.database import SessionLocal
from app.integrations.sms import MockSMSClient, SentMessage, SMSDeliveryError
from seed._common import close, prepare, refuse_unsafe_environment

ENUGU = (6.4413, 7.4988)
DEMO_PREFIX = "+2347000"


@dataclass
class SimulatedSMS(MockSMSClient):
    """Records messages; every `fail_every`-th send fails, to exercise retries."""

    fail_every: int = 0
    attempts: int = 0

    async def send(self, to: str, body: str) -> SentMessage:
        self.attempts += 1
        if self.fail_every and self.attempts % self.fail_every == 0:
            raise SMSDeliveryError("Simulated SMS failure")
        return await super().send(to, body)


@dataclass
class PressResult:
    event_id: uuid.UUID
    created: bool
    trigger_ms: float
    alerts: int
    nearest: str | None
    nearest_km: float | None


@dataclass
class SimulationReport:
    presses: list[PressResult] = field(default_factory=list)
    sent: int = 0
    failed: int = 0
    replay_alerted_again: bool = False
    resolved: int = 0


def _demo_phone(n: int) -> str:
    return f"{DEMO_PREFIX}{n:06d}"


async def _user(db: AsyncSession, phone: str, name: str, role: UserRole):
    from app.domains.auth.models import User

    user = await db.scalar(select(User).where(User.phone == phone))
    if user is None:
        user = User(phone=phone, full_name=name, role=role, phone_verified_at=datetime.now(UTC))
        db.add(user)
        await db.flush()
    return user


async def demo_family(db: AsyncSession, index: int, rng: random.Random):
    """A demo patient (with a home location), a primary guardian and two contacts."""
    from app.domains.patients.models import EmergencyContact, Guardian, Patient

    base = 100 + index * 10
    patient_user = await _user(db, _demo_phone(base), f"Demo Patient {index}", UserRole.PATIENT)
    patient = await db.scalar(select(Patient).where(Patient.user_id == patient_user.id))
    if patient is None:
        lat = ENUGU[0] + rng.uniform(-0.05, 0.05)
        lng = ENUGU[1] + rng.uniform(-0.05, 0.05)
        patient = Patient(
            user_id=patient_user.id,
            full_name=f"Demo Patient {index}",
            date_of_birth=date(1998, 1, 1),
            created_by=patient_user.id,
            home_location=WKTElement(f"POINT({lng} {lat})", srid=4326),
        )
        db.add(patient)
        await db.flush()
        guardian = await _user(
            db, _demo_phone(base + 1), f"Demo Guardian {index}", UserRole.GUARDIAN
        )
        db.add(
            Guardian(
                patient_id=patient.id,
                user_id=guardian.id,
                relationship="parent",
                is_primary=True,
            )
        )
        for offset, name in ((2, "Demo Contact A"), (3, "Demo Contact B")):
            db.add(
                EmergencyContact(
                    patient_id=patient.id,
                    name=f"{name} {index}",
                    phone=_demo_phone(base + offset),
                    relationship="friend",
                    priority=offset - 1,
                )
            )
    await db.commit()
    return patient_user, patient


async def simulate(
    sessions: async_sessionmaker[AsyncSession],
    *,
    presses: int = 3,
    fail_every: int = 0,
    resolve: bool = False,
    seed: int = 42,
) -> SimulationReport:
    from app.domains.crisis import service
    from app.domains.crisis.schemas import CrisisResolveIn, CrisisTriggerIn

    rng = random.Random(seed)
    sms = SimulatedSMS(fail_every=fail_every)
    report = SimulationReport()
    first_body: CrisisTriggerIn | None = None
    first_user = None

    for index in range(presses):
        async with sessions() as db:
            user, patient = await demo_family(db, index, rng)
            body = CrisisTriggerIn(
                patient_id=patient.id,
                client_event_id=uuid.uuid4(),
                location={
                    "latitude": ENUGU[0] + rng.uniform(-0.08, 0.08),
                    "longitude": ENUGU[1] + rng.uniform(-0.08, 0.08),
                    "accuracy_m": rng.randint(5, 60),
                },
            )
            start = time.perf_counter()
            result = await service.trigger_crisis(db, user, body)
            event = await service.load_event(db, result.event_id)
            nearby = await service.nearby_facilities(db, event)
            elapsed_ms = (time.perf_counter() - start) * 1000
            nearest = nearby.facilities[0] if nearby.facilities else None
            report.presses.append(
                PressResult(
                    event_id=result.event_id,
                    created=result.created,
                    trigger_ms=elapsed_ms,
                    alerts=len(event.alerts),
                    nearest=nearest["name"] if nearest else None,
                    nearest_km=nearest["distance_m"] / 1000 if nearest else None,
                )
            )
            if first_body is None:
                first_body, first_user = body, user

        delivery = await service.deliver_pending_alerts(sessions, sms, event_id=result.event_id)
        report.sent += delivery.sent
        report.failed += delivery.failed

    # Replaying the first press (e.g. an offline retry) must not alert anyone again:
    # same event back, and no new alerts queued.
    if first_body is not None:
        async with sessions() as db:
            replay = await service.trigger_crisis(db, first_user, first_body)
            event = await service.load_event(db, replay.event_id)
        report.replay_alerted_again = (
            replay.created
            or replay.event_id != report.presses[0].event_id
            or len(event.alerts) != report.presses[0].alerts
        )

    if resolve:
        from app.domains.auth.models import User
        from app.domains.crisis.models import CrisisEvent

        for press in report.presses:
            async with sessions() as db:
                event = await db.get(CrisisEvent, press.event_id)
                user = await db.get(User, event.triggered_by)
                await service.resolve_crisis(
                    db, user, press.event_id, CrisisResolveIn(false_alarm=True)
                )
                report.resolved += 1
    return report


def print_report(report: SimulationReport) -> None:
    print(f"{'event':38} {'new':>4} {'ms':>7} {'alerts':>6}  nearest verified facility")
    for p in report.presses:
        where = f"{p.nearest} ({p.nearest_km:.1f} km)" if p.nearest else "none within range"
        print(
            f"{p.event_id!s:38} {'yes' if p.created else 'no':>4} {p.trigger_ms:7.1f} {p.alerts:6}  {where}"
        )
    slowest = max((p.trigger_ms for p in report.presses), default=0)
    print(
        f"\nSMS sent: {report.sent}, failed (left for the retry sweep): {report.failed}. "
        f"Slowest press: {slowest:.0f} ms. "
        f"Replay alerted anyone again: {'YES (bug)' if report.replay_alerted_again else 'no'}."
    )
    if report.resolved:
        print(f"Resolved as false alarms: {report.resolved}.")


async def main(argv: list[str] | None = None) -> SimulationReport:
    parser = argparse.ArgumentParser(description="Simulate crisis button presses.")
    parser.add_argument("--presses", type=int, default=3)
    parser.add_argument("--fail-every", type=int, default=0, help="fail every Nth SMS")
    parser.add_argument("--resolve", action="store_true", help="close each crisis afterwards")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    refuse_unsafe_environment()
    prepare()
    try:
        report = await simulate(
            SessionLocal,
            presses=args.presses,
            fail_every=args.fail_every,
            resolve=args.resolve,
            seed=args.seed,
        )
    finally:
        await close()
    print_report(report)
    return report


if __name__ == "__main__":
    asyncio.run(main())
