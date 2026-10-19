"""Builders and fixtures for the Day 11 money tests (payments, escrow, incidents).

Each test gets fresh mocks for Paystack, Stripe and the escrow partner, wired into the
app, plus a small world: an adult patient, an approved caregiver with an hourly rate,
an ops user, a family member abroad who may fund care, a linked relative who may not,
a stranger, and an accepted booking ready to pay.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import encryption
from app.core.permissions import UserRole
from app.domains.auth.models import User
from app.domains.bookings.models import (
    Booking,
    BookingStatus,
    BookingType,
    CheckIn,
    CheckInKind,
    Shift,
    ShiftStatus,
)
from app.domains.bookings.notifications import Notifier
from app.domains.bookings.router import get_notifier
from app.domains.caregivers.models import Caregiver, CaregiverStatus, CaregiverTier
from app.domains.payments import service as payments
from app.domains.payments.models import (
    EscrowTransaction,
    Gateway,
    LedgerAccount,
    LedgerEntry,
    Payment,
)
from app.integrations.escrow import MockEscrowClient, get_escrow_client
from app.integrations.payments import (
    MOCK_STRIPE_WEBHOOK_SECRET,
    MOCK_WEBHOOK_SECRET,
    MockPaymentClient,
    MockStripeClient,
    paystack_signature,
    stripe_signature,
)
from app.integrations.sms import MockSMSClient
from app.main import app
from app.tests.crisis_factories import (
    ENUGU,
    add_family_link,
    add_guardian,
    auth,
    make_patient,
    make_user,
    point,
)

RATE = 200_000  # NGN 2,000.00 an hour, in kobo
HOURS = 3
TOTAL = RATE * HOURS


@pytest.fixture(autouse=True, name="_encryption_key")
def encryption_key_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Incident descriptions are encrypted; tests use a throwaway key (as test_crisis does)."""
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_fernet", lambda: key)


@dataclass
class Mocks:
    paystack: MockPaymentClient
    stripe: MockStripeClient
    escrow: MockEscrowClient


@pytest.fixture(name="mocks")
def mocks_fixture(sms: MockSMSClient) -> Iterator[Mocks]:
    """Fresh gateway and escrow mocks, used by every route that moves money. Booking and
    payment notices go to the test's SMS outbox."""
    m = Mocks(MockPaymentClient(), MockStripeClient(), MockEscrowClient())
    app.dependency_overrides[payments.get_gateways] = lambda: payments.Gateways(
        {Gateway.PAYSTACK: m.paystack, Gateway.STRIPE: m.stripe}
    )
    app.dependency_overrides[get_escrow_client] = lambda: m.escrow
    app.dependency_overrides[get_notifier] = lambda: Notifier(sms)
    yield m
    for dependency in (payments.get_gateways, get_escrow_client, get_notifier):
        app.dependency_overrides.pop(dependency, None)


@dataclass
class World:
    patient_user: User
    patient_id: uuid.UUID
    caregiver_user: User
    caregiver: Caregiver
    ops: User
    family: User  # FamilyLink with can_fund
    relative: User  # FamilyLink without can_fund
    stranger: User
    booking_id: uuid.UUID

    @property
    def patient(self) -> dict[str, str]:
        return auth(self.patient_user)

    @property
    def carer(self) -> dict[str, str]:
        return auth(self.caregiver_user)

    @property
    def ops_h(self) -> dict[str, str]:
        return auth(self.ops)


async def make_caregiver(db: AsyncSession, rate: int = RATE) -> tuple[User, Caregiver]:
    user = await make_user(db, UserRole.CAREGIVER, "Ngozi Eze")
    caregiver = Caregiver(
        user_id=user.id,
        tier=CaregiverTier.TRAINED_CARER,
        status=CaregiverStatus.APPROVED,
        hourly_rate_minor=rate,
        approved_at=datetime.now(UTC),
    )
    db.add(caregiver)
    await db.commit()
    return user, caregiver


async def make_booking(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    caregiver: Caregiver,
    booked_by: uuid.UUID,
    status: BookingStatus = BookingStatus.ACCEPTED,
    starts_in: timedelta = timedelta(days=1),
    hours: int = HOURS,
    shifts: int = 1,
    total: int | None = None,
) -> Booking:
    starts = datetime.now(UTC).replace(microsecond=0) + starts_in
    ends = starts + timedelta(hours=hours * shifts)
    booking = Booking(
        patient_id=patient_id,
        caregiver_id=caregiver.id,
        booked_by=booked_by,
        booking_type=BookingType.HOURLY if shifts == 1 else BookingType.DAILY,
        status=status,
        starts_at=starts,
        ends_at=ends,
        service_location=point(*ENUGU),
        service_address="12 Ogui Road, Enugu",
        rate_minor=caregiver.hourly_rate_minor or 0,
        total_minor=TOTAL * shifts if total is None else total,
        currency="NGN",
        accepted_at=datetime.now(UTC) if status is not BookingStatus.REQUESTED else None,
    )
    db.add(booking)
    for i in range(shifts):
        start = starts + timedelta(hours=hours * i)
        db.add(
            Shift(
                booking_id=booking.id,
                scheduled_start=start,
                scheduled_end=start + timedelta(hours=hours),
            )
        )
    await db.commit()
    return booking


@pytest.fixture(name="world")
async def world_fixture(db: AsyncSession, mocks: Mocks) -> World:
    patient_user = await make_user(db, UserRole.PATIENT, "Chidi Okafor")
    patient = await make_patient(db, user=patient_user, created_by=patient_user)
    caregiver_user, caregiver = await make_caregiver(db)
    ops = await make_user(db, UserRole.OPS, "Ops Desk")
    family = await make_user(db, UserRole.FAMILY_ABROAD, "Ada in London")
    link = await add_family_link(db, patient, family, {"location": False})
    link.can_fund = True
    await db.commit()
    relative = await make_user(db, UserRole.FAMILY_ABROAD, "Cousin")
    await add_family_link(db, patient, relative, {})
    stranger = await make_user(db, UserRole.PATIENT, "Stranger")
    booking = await make_booking(
        db, patient_id=patient.id, caregiver=caregiver, booked_by=patient_user.id
    )
    return World(
        patient_user=patient_user,
        patient_id=patient.id,
        caregiver_user=caregiver_user,
        caregiver=caregiver,
        ops=ops,
        family=family,
        relative=relative,
        stranger=stranger,
        booking_id=booking.id,
    )


# ---------------------------------------------------------------- webhooks
def paystack_event(
    reference: str, *, event: str = "charge.success", txn_id: int | str = 9001, **data: Any
) -> bytes:
    body = {
        "event": event,
        "data": {
            "id": txn_id,
            "reference": reference,
            "status": "success",
            "amount": TOTAL,
            "currency": "NGN",
            "customer": {"email": "payer@example.com", "phone": "+2348031234567"},
            "authorization": {"last4": "4081", "bin": "408408"},
            **data,
        },
    }
    return json.dumps(body).encode()


def paystack_headers(body: bytes, secret: str = MOCK_WEBHOOK_SECRET) -> dict[str, str]:
    return {"x-paystack-signature": paystack_signature(secret, body)}


def stripe_event(
    reference: str,
    session_id: str,
    *,
    event_type: str = "checkout.session.completed",
    event_id: str | None = None,
) -> bytes:
    body = {
        "id": event_id or f"evt_{uuid.uuid4().hex[:24]}",
        "type": event_type,
        "data": {
            "object": {
                "id": session_id,
                "client_reference_id": reference,
                "payment_status": "paid",
                "status": "complete",
                "amount_total": TOTAL,
                "currency": "ngn",
                "customer_details": {"email": "payer@example.com"},
            }
        },
    }
    return json.dumps(body).encode()


def stripe_headers(
    body: bytes, secret: str = MOCK_STRIPE_WEBHOOK_SECRET, timestamp: int | None = None
) -> dict[str, str]:
    return {"Stripe-Signature": stripe_signature(secret, body, timestamp)}


# ---------------------------------------------------------------- flows
async def hold(
    api: httpx.AsyncClient,
    booking_id: uuid.UUID,
    headers: dict[str, str],
    gateway: str = "paystack",
    key: str | None = None,
) -> httpx.Response:
    extra = {"Idempotency-Key": key} if key else {}
    return await api.post(
        f"/bookings/{booking_id}/escrow/hold",
        json={"gateway": gateway},
        headers={**headers, **extra},
    )


async def pay(api: httpx.AsyncClient, world: World, gateway: str = "paystack") -> dict[str, Any]:
    """Hold, then deliver the gateway's success webhook. Returns the checkout JSON."""
    response = await hold(api, world.booking_id, world.patient, gateway)
    assert response.status_code == 201, response.text
    checkout = response.json()
    reference = checkout["payment"]["reference"]
    if gateway == "paystack":
        body = paystack_event(reference)
        hook = await api.post(
            "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
        )
    else:
        session_id = checkout["authorization_url"].rsplit("/", 1)[1]
        body = stripe_event(reference, session_id)
        hook = await api.post(
            "/payments/webhooks/stripe", content=body, headers=stripe_headers(body)
        )
    assert hook.status_code == 200, hook.text
    assert hook.json() == {"status": "processed"}
    return checkout


async def complete_booking(
    db: AsyncSession,
    booking_id: uuid.UUID,
    *,
    checked_out_ago: timedelta = timedelta(hours=25),
    inside: bool = True,
) -> None:
    """Make a paid booking completed: every shift checked in and out (server time in the past)."""
    booking = await db.get(Booking, booking_id, populate_existing=True)
    assert booking is not None
    received = datetime.now(UTC) - checked_out_ago
    for shift in await db.scalars(select(Shift).where(Shift.booking_id == booking_id)):
        shift.status = ShiftStatus.COMPLETED
        shift.actual_start = shift.scheduled_start
        shift.actual_end = shift.scheduled_end
        for kind in (CheckInKind.CHECK_IN, CheckInKind.CHECK_OUT):
            db.add(
                CheckIn(
                    shift_id=shift.id,
                    kind=kind,
                    location=point(*ENUGU),
                    distance_m=10 if inside else 5000,
                    within_geofence=inside,
                    client_timestamp=received,
                    received_at=received,
                )
            )
    booking.status = BookingStatus.COMPLETED
    await db.commit()


async def escrow_row(db: AsyncSession, booking_id: uuid.UUID) -> EscrowTransaction | None:
    return await db.scalar(
        select(EscrowTransaction)
        .where(EscrowTransaction.booking_id == booking_id)
        .execution_options(populate_existing=True)
    )


async def booking_row(db: AsyncSession, booking_id: uuid.UUID) -> Booking:
    booking = await db.get(Booking, booking_id, populate_existing=True)
    assert booking is not None
    return booking


async def payments_for(db: AsyncSession, booking_id: uuid.UUID) -> list[Payment]:
    return list(
        await db.scalars(
            select(Payment)
            .where(Payment.booking_id == booking_id)
            .order_by(Payment.created_at)
            .execution_options(populate_existing=True)
        )
    )


async def ledger(db: AsyncSession, booking_id: uuid.UUID | None = None) -> list[LedgerEntry]:
    query = select(LedgerEntry).order_by(LedgerEntry.id)
    if booking_id is not None:
        query = query.where(LedgerEntry.booking_id == booking_id)
    return list(await db.scalars(query))


async def balances(db: AsyncSession, booking_id: uuid.UUID | None = None) -> dict[str, int]:
    """Signed balance (debit - credit) per account, NGN only, zero accounts dropped."""
    raw = await payments.account_balances(db, booking_id)
    return {acct.value: v for (acct, cur), v in raw.items() if cur == "NGN" and v != 0}


def assert_groups_balance(entries: list[LedgerEntry]) -> None:
    groups: dict[uuid.UUID, int] = {}
    for e in entries:
        sign = 1 if e.direction.value == "debit" else -1
        groups[e.txn_group] = groups.get(e.txn_group, 0) + sign * e.amount_minor
    assert all(v == 0 for v in groups.values()), groups


__all__ = [
    "HOURS",
    "RATE",
    "TOTAL",
    "LedgerAccount",
    "Mocks",
    "World",
    "add_guardian",
    "assert_groups_balance",
    "balances",
    "booking_row",
    "complete_booking",
    "encryption_key_fixture",
    "escrow_row",
    "hold",
    "ledger",
    "make_booking",
    "make_caregiver",
    "mocks_fixture",
    "pay",
    "payments_for",
    "paystack_event",
    "paystack_headers",
    "stripe_event",
    "stripe_headers",
    "world_fixture",
]
