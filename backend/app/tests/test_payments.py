"""Day 11 money paths: escrow hold, webhooks, verification, release, refund, settlement.

Every path that moves money is here, with the ledger checked after it: each txn_group
balances, nothing is posted twice, and money that can't go into escrow is flagged for
a human instead of disappearing.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import get_settings
from app.core.audit import AuditLog
from app.domains.bookings.models import BookingStatus, CheckIn, Shift, ShiftStatus
from app.domains.payments import service as payments
from app.domains.payments.models import (
    EntryDirection,
    EscrowStatus,
    EscrowTransaction,
    LedgerAccount,
    LedgerEntry,
    Payment,
    PaymentStatus,
    WebhookEvent,
)
from app.domains.trust.models import Incident
from app.integrations.payments import PaymentStatus as GatewayStatus
from app.tests.crisis_factories import auth
from app.tests.payments_fixtures import (  # noqa: F401 - fixtures, used by name
    TOTAL,
    World,
    assert_groups_balance,
    balances,
    booking_row,
    complete_booking,
    encryption_key_fixture,
    escrow_row,
    hold,
    ledger,
    make_booking,
    mocks_fixture,
    pay,
    payments_for,
    paystack_event,
    paystack_headers,
    stripe_event,
    stripe_headers,
    world_fixture,
)


async def _audit_actions(db, entity_id: uuid.UUID | None = None) -> list[str]:
    query = select(AuditLog.action).order_by(AuditLog.id)
    if entity_id is not None:
        query = query.where(AuditLog.entity_id == entity_id)
    return list(await db.scalars(query))


async def _settle(api, world: World):
    response = await api.post("/payments/ops/settle", headers=world.ops_h)
    assert response.status_code == 200, response.text
    return response.json()


# ================================================================== hold
async def test_hold_opens_escrow_and_returns_checkout(api, db, world, mocks):
    response = await hold(api, world.booking_id, world.patient)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reused"] is False
    assert body["escrow_status"] == "pending"
    assert body["authorization_url"].startswith("https://checkout.mock.carekin.invalid/")
    payment = body["payment"]
    assert payment["status"] == "initiated"
    assert payment["amount_minor"] == TOTAL and payment["currency"] == "NGN"
    assert payment["reference"].startswith("CK-")

    escrow = await escrow_row(db, world.booking_id)
    assert escrow is not None and escrow.status is EscrowStatus.PENDING
    assert escrow.amount_minor == TOTAL and escrow.partner == "mock"
    assert escrow.partner_status is EscrowStatus.PENDING
    # Nothing has moved yet, so nothing is on the ledger.
    assert await ledger(db) == []
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.ACCEPTED
    assert "payment.initiate" in await _audit_actions(db)
    # The payer has no email, so Paystack gets a stable placeholder on our domain.
    assert mocks.paystack.amounts[payment["reference"]] == (TOTAL, "NGN")


async def test_hold_with_same_idempotency_key_returns_same_payment(api, db, world, mocks):
    first = await hold(api, world.booking_id, world.patient, key="tap-12345678")
    second = await hold(api, world.booking_id, world.patient, key="tap-12345678")
    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["reused"] is True
    assert first.json()["payment"]["id"] == second.json()["payment"]["id"]
    assert len(await payments_for(db, world.booking_id)) == 1
    assert len(mocks.paystack.initialized) == 1


async def test_hold_reuses_open_checkout_without_a_key(api, db, world, mocks):
    first = await hold(api, world.booking_id, world.patient)
    second = await hold(api, world.booking_id, world.patient)
    assert second.status_code == 200
    assert second.json()["payment"]["id"] == first.json()["payment"]["id"]
    assert second.json()["authorization_url"] == first.json()["authorization_url"]
    assert len(await payments_for(db, world.booking_id)) == 1


async def test_idempotency_key_reused_for_another_gateway_is_refused(api, world):
    await hold(api, world.booking_id, world.patient, key="same-key-123")
    response = await hold(
        api, world.booking_id, world.patient, gateway="stripe", key="same-key-123"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"


@pytest.mark.parametrize("key", ["short", "has spaces in it", "semi;colon;key", "x" * 201])
async def test_bad_idempotency_keys_are_rejected(api, world, key):
    response = await hold(api, world.booking_id, world.patient, key=key)
    assert response.status_code == 422


async def test_concurrent_holds_open_one_payment(api, db, world, mocks):
    with_key = await asyncio.gather(
        *(hold(api, world.booking_id, world.patient, key="double-tap-1") for _ in range(5))
    )
    assert {r.status_code for r in with_key} <= {200, 201}
    assert len({r.json()["payment"]["id"] for r in with_key}) == 1
    without_key = await asyncio.gather(
        *(hold(api, world.booking_id, world.patient) for _ in range(5))
    )
    assert len({r.json()["payment"]["id"] for r in without_key}) == 1
    assert len(await payments_for(db, world.booking_id)) == 1
    assert len(mocks.paystack.initialized) == 1


async def test_who_may_pay(api, db, world):
    caregiver = await hold(api, world.booking_id, world.carer)
    assert caregiver.status_code == 403 and caregiver.json()["error"]["code"] == "payment_forbidden"
    ops = await hold(api, world.booking_id, world.ops_h)
    assert ops.status_code == 403
    stranger = await hold(api, world.booking_id, auth(world.stranger))
    assert stranger.status_code == 404  # the booking's existence isn't leaked
    relative = await hold(api, world.booking_id, auth(world.relative))
    assert relative.status_code == 403
    assert relative.json()["error"]["code"] == "funding_not_allowed"
    family = await hold(api, world.booking_id, auth(world.family), gateway="stripe")
    assert family.status_code == 201, family.text
    assert family.json()["payment"]["payer_id"] == str(world.family.id)


async def test_guardian_of_a_minor_can_pay(api, db, mocks):
    from app.core.permissions import UserRole
    from app.tests.crisis_factories import add_guardian, make_patient, make_user
    from app.tests.payments_fixtures import make_caregiver

    guardian = await make_user(db, UserRole.GUARDIAN, "Mother")
    child = await make_patient(db, user=None, created_by=guardian, name="Child")
    await add_guardian(db, child, guardian, primary=True)
    _, caregiver = await make_caregiver(db)
    booking = await make_booking(
        db, patient_id=child.id, caregiver=caregiver, booked_by=guardian.id
    )
    response = await hold(api, booking.id, auth(guardian))
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (BookingStatus.REQUESTED, "booking_not_payable"),
        (BookingStatus.PENDING_GUARDIAN, "booking_not_payable"),
        (BookingStatus.DECLINED, "booking_not_payable"),
        (BookingStatus.CANCELLED, "booking_not_payable"),
        (BookingStatus.PAID, "booking_already_paid"),
        (BookingStatus.IN_PROGRESS, "booking_already_paid"),
        (BookingStatus.COMPLETED, "booking_already_paid"),
        (BookingStatus.DISPUTED, "booking_already_paid"),
    ],
)
async def test_only_an_accepted_booking_can_be_paid(api, db, world, status, code):
    await db.execute(
        text("UPDATE bookings SET status = :s WHERE id = :id"),
        {"s": status.value, "id": world.booking_id},
    )
    await db.commit()
    response = await hold(api, world.booking_id, world.patient)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == code


async def test_payment_window(api, db, world):
    await db.execute(
        text("UPDATE bookings SET accepted_at = now() - interval '25 hours' WHERE id = :id"),
        {"id": world.booking_id},
    )
    await db.commit()
    late = await hold(api, world.booking_id, world.patient)
    assert late.status_code == 422 and late.json()["error"]["code"] == "payment_window_closed"


async def test_cannot_pay_after_the_start(api, db, world):
    await db.execute(
        text(
            "UPDATE bookings SET starts_at = now() - interval '1 hour', "
            "ends_at = now() + interval '2 hours' WHERE id = :id"
        ),
        {"id": world.booking_id},
    )
    await db.commit()
    response = await hold(api, world.booking_id, world.patient)
    assert response.status_code == 422


async def test_nothing_to_pay(api, db, world):
    from app.tests.payments_fixtures import make_caregiver

    _, free = await make_caregiver(db, rate=0)
    booking = await make_booking(
        db, patient_id=world.patient_id, caregiver=free, booked_by=world.patient_user.id, total=0
    )
    response = await hold(api, booking.id, world.patient)
    assert response.status_code == 422 and response.json()["error"]["code"] == "nothing_to_pay"


async def test_gateway_outage_on_hold_saves_nothing_and_retry_works(api, db, world, mocks):
    mocks.paystack.unavailable = True
    response = await hold(api, world.booking_id, world.patient)
    assert response.status_code == 503
    assert await payments_for(db, world.booking_id) == []
    assert await escrow_row(db, world.booking_id) is None
    mocks.paystack.unavailable = False
    retry = await hold(api, world.booking_id, world.patient)
    assert retry.status_code == 201
    # The partner escrow made on the first try is reused, not duplicated.
    assert len(mocks.escrow._records) == 1


async def test_partner_outage_on_hold_saves_nothing(api, db, world, mocks):
    mocks.escrow.unavailable = True
    response = await hold(api, world.booking_id, world.patient)
    assert response.status_code == 503
    assert await payments_for(db, world.booking_id) == []
    assert mocks.paystack.initialized == {}


async def test_hold_needs_login(api, world):
    response = await api.post(f"/bookings/{world.booking_id}/escrow/hold")
    assert response.status_code == 401


# ================================================================== Paystack webhook
async def test_paystack_webhook_funds_escrow_with_balanced_ledger(api, db, world, sms):
    checkout = await pay(api, world)
    booking = await booking_row(db, world.booking_id)
    assert booking.status is BookingStatus.PAID
    escrow = await escrow_row(db, world.booking_id)
    assert escrow.status is EscrowStatus.HELD and escrow.held_at is not None
    assert escrow.partner_status is EscrowStatus.HELD
    (payment,) = await payments_for(db, world.booking_id)
    assert payment.status is PaymentStatus.SUCCEEDED and payment.paid_at is not None
    assert escrow.funded_by_payment_id == payment.id
    assert str(payment.id) == checkout["payment"]["id"]

    entries = await ledger(db)
    assert [(e.memo, e.account.value, e.direction.value) for e in entries] == [
        ("payment.captured", "gateway_clearing", "debit"),
        ("payment.captured", "family_funds", "credit"),
        ("escrow.funded", "family_funds", "debit"),
        ("escrow.funded", "escrow", "credit"),
    ]
    assert all(e.amount_minor == TOTAL for e in entries)
    assert_groups_balance(entries)
    assert await balances(db) == {"gateway_clearing": TOTAL, "escrow": -TOTAL}
    actions = await _audit_actions(db)
    assert {"payment.succeed", "escrow.hold", "booking.paid"} <= set(actions)
    # Both sides were told; the texts carry no amounts or names.
    assert any("held safely in escrow" in m.body for m in sms.outbox)


async def test_webhook_payload_is_stored_redacted(api, db, world):
    await pay(api, world)
    (event,) = list(await db.scalars(select(WebhookEvent)))
    stored = json.dumps(event.payload)
    assert "payer@example.com" not in stored and "4081" not in stored and "+234" not in stored
    assert event.processed_at is not None and event.attempts == 1
    assert event.payload["data"]["amount"] == TOTAL


async def test_replayed_webhook_does_not_double_credit(api, db, world):
    checkout = await pay(api, world)
    body = paystack_event(checkout["payment"]["reference"])
    for _ in range(3):
        again = await api.post(
            "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
        )
        assert again.status_code == 200 and again.json() == {"status": "duplicate"}
    assert len(await ledger(db)) == 4
    assert await balances(db) == {"gateway_clearing": TOTAL, "escrow": -TOTAL}


async def test_concurrent_webhook_deliveries_apply_once(api, db, world):
    response = await hold(api, world.booking_id, world.patient)
    reference = response.json()["payment"]["reference"]
    body = paystack_event(reference)
    results = await asyncio.gather(
        *(
            api.post("/payments/webhooks/paystack", content=body, headers=paystack_headers(body))
            for _ in range(6)
        )
    )
    statuses = sorted(r.json()["status"] for r in results)
    assert statuses == ["duplicate"] * 5 + ["processed"]
    assert len(await ledger(db)) == 4
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.PAID


async def test_a_new_event_for_an_applied_payment_posts_nothing(api, db, world):
    """A different delivery (new event id) for a payment already applied is harmless."""
    checkout = await pay(api, world)
    body = paystack_event(checkout["payment"]["reference"], txn_id=424242)
    again = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert again.json() == {"status": "processed"}
    assert len(await ledger(db)) == 4


@pytest.mark.parametrize(
    "make_headers",
    [
        lambda body: {},
        lambda body: {"x-paystack-signature": ""},
        lambda body: {"x-paystack-signature": "0" * 128},
        lambda body: paystack_headers(body, secret="sk_live_someone_else"),
        lambda body: paystack_headers(body + b" "),
    ],
    ids=["missing", "empty", "zeros", "wrong-secret", "tampered-body"],
)
async def test_unsigned_or_forged_webhooks_are_refused(api, db, world, make_headers):
    response = await hold(api, world.booking_id, world.patient)
    body = paystack_event(response.json()["payment"]["reference"])
    forged = await api.post("/payments/webhooks/paystack", content=body, headers=make_headers(body))
    assert forged.status_code == 401
    assert forged.json()["error"]["code"] == "webhook_signature_invalid"
    assert list(await db.scalars(select(WebhookEvent))) == []
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.ACCEPTED


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2]",
        b'{"event": "charge.success"}',
        b'{"event": "charge.success", "data": {}}',
    ],
    ids=["not-json", "array", "no-data", "no-ids"],
)
async def test_malformed_but_signed_webhooks_are_400(api, db, body):
    response = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "webhook_malformed"


async def test_oversized_webhook_is_refused(api):
    body = b'{"event": "x", "data": {"id": 1, "pad": "' + b"a" * (300 * 1024) + b'"}}'
    response = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert response.status_code == 400


async def test_webhook_for_unknown_reference_is_acknowledged_and_ignored(api, db, world):
    body = paystack_event("CK-NOT-OURS-123")
    response = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert response.status_code == 200 and response.json() == {"status": "ignored"}
    (event,) = list(await db.scalars(select(WebhookEvent)))
    assert event.processed_at is not None and event.last_error == "no matching payment"
    assert await ledger(db) == []


async def test_irrelevant_event_is_ignored(api, db, world):
    body = json.dumps(
        {"event": "transfer.success", "data": {"id": 77, "reference": "TRF-1"}}
    ).encode()
    response = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert response.json() == {"status": "ignored"}


async def test_webhook_body_is_never_trusted(api, db, world, mocks):
    """The body says success; the gateway's verify API says it failed. Verify wins."""
    response = await hold(api, world.booking_id, world.patient)
    reference = response.json()["payment"]["reference"]
    mocks.paystack.set_outcome(reference, GatewayStatus.FAILED)
    body = paystack_event(reference)
    hook = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert hook.json() == {"status": "processed"}
    (payment,) = await payments_for(db, world.booking_id)
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "declined_or_expired"
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.ACCEPTED
    assert await ledger(db) == []
    # The family can start again with a fresh checkout.
    retry = await hold(api, world.booking_id, world.patient)
    assert retry.status_code == 201 and retry.json()["payment"]["id"] != str(payment.id)


async def test_gateway_down_during_webhook_returns_503_and_retry_applies(api, db, world, mocks):
    response = await hold(api, world.booking_id, world.patient)
    body = paystack_event(response.json()["payment"]["reference"])
    mocks.paystack.unavailable = True
    first = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert first.status_code == 503
    (event,) = list(await db.scalars(select(WebhookEvent)))
    assert event.processed_at is None and event.attempts == 1 and event.last_error
    assert await ledger(db) == []
    mocks.paystack.unavailable = False
    second = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert second.json() == {"status": "processed"}
    await db.refresh(event)
    assert event.processed_at is not None and event.attempts == 2
    assert len(await ledger(db)) == 4


@pytest.mark.parametrize(
    ("amount", "currency"),
    [(TOTAL - 1, "NGN"), (TOTAL + 50_000, "NGN"), (TOTAL, "USD"), (0, "NGN")],
    ids=["short", "over", "wrong-currency", "zero"],
)
async def test_wrong_amount_is_captured_but_flagged_not_funded(
    api, db, world, mocks, amount, currency
):
    response = await hold(api, world.booking_id, world.patient)
    reference = response.json()["payment"]["reference"]
    mocks.paystack.set_outcome(
        reference, GatewayStatus.SUCCEEDED, amount_minor=amount, currency=currency
    )
    body = paystack_event(reference)
    hook = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert hook.status_code == 200
    (payment,) = await payments_for(db, world.booking_id)
    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.needs_review and payment.review_reason == "amount_mismatch"
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.ACCEPTED
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.PENDING
    entries = await ledger(db)
    assert_groups_balance(entries)
    # Whatever really arrived is on the ledger (owed back to the family); nothing else.
    assert sum(e.amount_minor for e in entries if e.direction is EntryDirection.DEBIT) == amount
    (incident,) = list(await db.scalars(select(Incident)))
    assert incident.category.value == "payment" and incident.reported_by is None
    assert reference in incident.description


async def test_partner_down_while_funding_keeps_capture_and_retries(api, db, world, mocks):
    response = await hold(api, world.booking_id, world.patient)
    reference = response.json()["payment"]["reference"]
    body = paystack_event(reference)
    mocks.escrow.unavailable = True
    first = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert first.status_code == 503
    assert first.json()["error"]["code"] == "escrow_partner_unavailable"
    (payment,) = await payments_for(db, world.booking_id)
    assert payment.status is PaymentStatus.SUCCEEDED and not payment.needs_review
    assert [e.memo for e in await ledger(db)] == ["payment.captured"] * 2
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.ACCEPTED
    mocks.escrow.unavailable = False
    second = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert second.json() == {"status": "processed"}
    memos = [e.memo for e in await ledger(db)]
    assert memos.count("payment.captured") == 2 and memos.count("escrow.funded") == 2
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.PAID


async def test_payment_after_the_booking_expired_is_flagged_for_refund(api, db, world, mocks):
    response = await hold(api, world.booking_id, world.patient)
    reference = response.json()["payment"]["reference"]
    await db.execute(
        text("UPDATE bookings SET status = 'cancelled' WHERE id = :id"), {"id": world.booking_id}
    )
    await db.commit()
    body = paystack_event(reference)
    hook = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert hook.status_code == 200
    (payment,) = await payments_for(db, world.booking_id)
    assert payment.needs_review and payment.review_reason == "booking_not_payable"
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.PENDING
    assert await balances(db) == {"gateway_clearing": TOTAL, "family_funds": -TOTAL}
    assert len(list(await db.scalars(select(Incident)))) == 1


async def test_second_payment_for_a_paid_booking_is_flagged_not_funded(api, db, world, mocks):
    patient = await hold(api, world.booking_id, world.patient)
    family = await hold(api, world.booking_id, auth(world.family), gateway="stripe")
    assert patient.status_code == family.status_code == 201
    body = paystack_event(patient.json()["payment"]["reference"])
    await api.post("/payments/webhooks/paystack", content=body, headers=paystack_headers(body))
    session = family.json()["authorization_url"].rsplit("/", 1)[1]
    body = stripe_event(family.json()["payment"]["reference"], session)
    hook = await api.post("/payments/webhooks/stripe", content=body, headers=stripe_headers(body))
    assert hook.status_code == 200
    first, second = await payments_for(db, world.booking_id)
    escrow = await escrow_row(db, world.booking_id)
    assert escrow.funded_by_payment_id == first.id
    assert second.status is PaymentStatus.SUCCEEDED
    assert second.needs_review and second.review_reason == "duplicate_payment"
    # Escrow holds one payment; the second sits in family_funds until ops refunds it.
    assert await balances(db) == {
        "gateway_clearing": 2 * TOTAL,
        "escrow": -TOTAL,
        "family_funds": -TOTAL,
    }
    assert_groups_balance(await ledger(db))


async def test_paystack_event_naming_a_stripe_payment_is_ignored(api, db, world):
    family = await hold(api, world.booking_id, auth(world.family), gateway="stripe")
    body = paystack_event(family.json()["payment"]["reference"])
    response = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert response.json() == {"status": "ignored"}
    assert await ledger(db) == []


# ================================================================== Stripe webhook
async def test_family_abroad_pays_with_stripe(api, db, world):
    family = await hold(api, world.booking_id, auth(world.family), gateway="stripe")
    checkout = family.json()
    session = checkout["authorization_url"].rsplit("/", 1)[1]
    assert session.startswith("cs_mock_")
    body = stripe_event(checkout["payment"]["reference"], session)
    hook = await api.post("/payments/webhooks/stripe", content=body, headers=stripe_headers(body))
    assert hook.json() == {"status": "processed"}
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.PAID
    (payment,) = await payments_for(db, world.booking_id)
    assert payment.gateway.value == "stripe" and payment.gateway_ref == session
    assert_groups_balance(await ledger(db))


@pytest.mark.parametrize(
    "make_headers",
    [
        lambda body: stripe_headers(body, timestamp=int(time.time()) - 600),
        lambda body: stripe_headers(body, timestamp=int(time.time()) + 600),
        lambda body: stripe_headers(body, secret="whsec_other"),
        lambda body: {"Stripe-Signature": "v1=abc"},
        lambda body: {"Stripe-Signature": f"t={int(time.time())}"},
        lambda body: {},
    ],
    ids=["too-old", "future", "wrong-secret", "no-timestamp", "no-signature", "missing"],
)
async def test_stripe_signature_rules(api, world, make_headers):
    family = await hold(api, world.booking_id, auth(world.family), gateway="stripe")
    checkout = family.json()
    body = stripe_event(checkout["payment"]["reference"], "cs_x")
    response = await api.post("/payments/webhooks/stripe", content=body, headers=make_headers(body))
    assert response.status_code == 401


async def test_stripe_replay_with_same_event_id_is_duplicate(api, db, world):
    family = await hold(api, world.booking_id, auth(world.family), gateway="stripe")
    checkout = family.json()
    session = checkout["authorization_url"].rsplit("/", 1)[1]
    body = stripe_event(checkout["payment"]["reference"], session, event_id="evt_fixed_1")
    first = await api.post("/payments/webhooks/stripe", content=body, headers=stripe_headers(body))
    second = await api.post("/payments/webhooks/stripe", content=body, headers=stripe_headers(body))
    assert first.json() == {"status": "processed"} and second.json() == {"status": "duplicate"}
    assert len(await ledger(db)) == 4


async def test_stripe_expired_session_fails_the_payment(api, db, world, mocks):
    family = await hold(api, world.booking_id, auth(world.family), gateway="stripe")
    checkout = family.json()
    reference = checkout["payment"]["reference"]
    session = checkout["authorization_url"].rsplit("/", 1)[1]
    mocks.stripe.set_outcome(reference, GatewayStatus.FAILED)
    body = stripe_event(reference, session, event_type="checkout.session.expired")
    await api.post("/payments/webhooks/stripe", content=body, headers=stripe_headers(body))
    (payment,) = await payments_for(db, world.booking_id)
    assert payment.status is PaymentStatus.FAILED
    assert await ledger(db) == []


async def test_stripe_event_with_wrong_session_is_ignored(api, db, world):
    family = await hold(api, world.booking_id, auth(world.family), gateway="stripe")
    body = stripe_event(family.json()["payment"]["reference"], "cs_someone_elses")
    response = await api.post(
        "/payments/webhooks/stripe", content=body, headers=stripe_headers(body)
    )
    assert response.json() == {"status": "ignored"}
    assert await ledger(db) == []


async def test_non_checkout_stripe_events_are_ignored(api, db):
    body = json.dumps(
        {"id": "evt_1", "type": "charge.refunded", "data": {"object": {"id": "ch_1"}}}
    ).encode()
    response = await api.post(
        "/payments/webhooks/stripe", content=body, headers=stripe_headers(body)
    )
    assert response.json() == {"status": "ignored"}


async def test_late_success_after_we_gave_up_is_not_lost(api, db, world, mocks):
    """A checkout we marked abandoned is paid after all: the webhook re-checks it."""
    response = await hold(api, world.booking_id, world.patient)
    reference = response.json()["payment"]["reference"]
    await db.execute(
        update(Payment)
        .where(Payment.reference == reference)
        .values(status=PaymentStatus.FAILED, failure_reason="abandoned")
    )
    await db.commit()
    body = paystack_event(reference)
    hook = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert hook.json() == {"status": "processed"}
    (payment,) = await payments_for(db, world.booking_id)
    assert payment.status is PaymentStatus.SUCCEEDED
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.PAID


# ================================================================== verify endpoint
async def test_verify_funds_without_waiting_for_the_webhook(api, db, world):
    response = await hold(api, world.booking_id, world.patient)
    payment_id = response.json()["payment"]["id"]
    verified = await api.post(f"/payments/{payment_id}/verify", headers=world.patient)
    assert verified.status_code == 200, verified.text
    assert verified.json()["outcome"] == "funded"
    assert verified.json()["escrow_status"] == "held"
    again = await api.post(f"/payments/{payment_id}/verify", headers=world.patient)
    assert again.json()["outcome"] == "already_processed"
    # The webhook arriving afterwards changes nothing.
    body = paystack_event(response.json()["payment"]["reference"])
    hook = await api.post(
        "/payments/webhooks/paystack", content=body, headers=paystack_headers(body)
    )
    assert hook.json() == {"status": "processed"}
    assert len(await ledger(db)) == 4


async def test_verify_while_still_pending(api, db, world, mocks):
    response = await hold(api, world.booking_id, world.patient)
    reference = response.json()["payment"]["reference"]
    mocks.paystack.set_outcome(reference, GatewayStatus.INITIATED)
    verified = await api.post(
        f"/payments/{response.json()['payment']['id']}/verify", headers=world.patient
    )
    assert verified.json()["outcome"] == "pending"
    assert verified.json()["payment"]["status"] == "initiated"
    assert await ledger(db) == []


async def test_concurrent_verify_and_webhook_apply_once(api, db, world):
    response = await hold(api, world.booking_id, world.patient)
    payment_id = response.json()["payment"]["id"]
    body = paystack_event(response.json()["payment"]["reference"])
    results = await asyncio.gather(
        api.post(f"/payments/{payment_id}/verify", headers=world.patient),
        api.post("/payments/webhooks/paystack", content=body, headers=paystack_headers(body)),
        api.post(f"/payments/{payment_id}/verify", headers=auth(world.family)),
    )
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert len(await ledger(db)) == 4
    assert_groups_balance(await ledger(db))


async def test_who_may_verify(api, world):
    response = await hold(api, world.booking_id, world.patient)
    payment_id = response.json()["payment"]["id"]
    assert (
        await api.post(f"/payments/{payment_id}/verify", headers=world.carer)
    ).status_code == 404
    assert (
        await api.post(f"/payments/{payment_id}/verify", headers=auth(world.stranger))
    ).status_code == 404
    assert (
        await api.post(f"/payments/{uuid.uuid4()}/verify", headers=world.patient)
    ).status_code == 404
    assert (
        await api.post(f"/payments/{payment_id}/verify", headers=world.ops_h)
    ).status_code == 200


# ================================================================== release
async def test_release_needs_a_completed_booking(api, db, world):
    await pay(api, world)
    response = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "booking_not_completed"


async def test_release_of_unpaid_booking(api, world):
    response = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    assert response.status_code == 409 and response.json()["error"]["code"] == "escrow_not_funded"
    await hold(api, world.booking_id, world.patient)
    pending = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    assert pending.status_code == 409 and pending.json()["error"]["code"] == "escrow_not_held"


async def test_patient_confirms_care_and_releases(api, db, world, sms, mocks):
    await pay(api, world)
    await complete_booking(db, world.booking_id, checked_out_ago=timedelta(minutes=5))
    response = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "released" and body["release_method"] == "human"
    assert body["released_by"] == str(world.patient_user.id)
    assert await balances(db) == {"gateway_clearing": TOTAL, "caregiver_payable": -TOTAL}
    assert_groups_balance(await ledger(db))
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.COMPLETED
    assert "escrow.release" in await _audit_actions(db)
    assert any("has been released to you" in m.body for m in sms.outbox)
    escrow = await escrow_row(db, world.booking_id)
    assert (await mocks.escrow.get(escrow.partner_ref)).status is EscrowStatus.RELEASED


async def test_release_takes_the_commission(api, db, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "PLATFORM_COMMISSION_BPS", 1250)  # 12.5%
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    response = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.ops_h)
    assert response.status_code == 200
    fee = TOTAL * 1250 // 10_000
    assert await balances(db) == {
        "gateway_clearing": TOTAL,
        "caregiver_payable": -(TOTAL - fee),
        "platform_revenue": -fee,
    }


async def test_releasing_twice_pays_once(api, db, world, mocks):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    results = await asyncio.gather(
        *(
            api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
            for _ in range(4)
        )
    )
    assert all(r.status_code == 200 for r in results)
    assert [e.memo for e in await ledger(db)].count("escrow.released") == 2  # one posting, 2 lines


async def test_who_may_release(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    url = f"/bookings/{world.booking_id}/escrow/release"
    assert (await api.post(url, headers=world.carer)).status_code == 403
    assert (await api.post(url, headers=auth(world.family))).status_code == 403
    assert (await api.post(url, headers=auth(world.relative))).status_code == 403
    assert (await api.post(url, headers=auth(world.stranger))).status_code == 404
    assert (await api.post(url, headers=world.ops_h)).status_code == 200


async def test_release_refused_while_dispute_open(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    dispute = await api.post(
        "/incidents",
        json={
            "booking_id": str(world.booking_id),
            "category": "conduct",
            "description": "The caregiver left two hours early.",
            "is_dispute": True,
        },
        headers=world.patient,
    )
    assert dispute.status_code == 201, dispute.text
    for headers in (world.patient, world.ops_h):
        response = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=headers)
        assert response.status_code == 409 and response.json()["error"]["code"] == "dispute_open"
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.ON_HOLD_DISPUTE
    assert "escrow.released" not in [e.memo for e in await ledger(db)]


async def test_release_refused_if_dispute_row_open_even_if_escrow_says_held(api, db, world):
    """Belt and braces: an open dispute incident blocks release on its own."""
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    from app.domains.trust.models import IncidentCategory, Severity
    from app.domains.trust.rules import open_system_incident

    incident = open_system_incident(
        db,
        booking_id=world.booking_id,
        category=IncidentCategory.PAYMENT,
        severity=Severity.HIGH,
        description="manual dispute row",
    )
    incident.is_dispute = True
    await db.commit()
    response = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.ops_h)
    assert response.status_code == 409 and response.json()["error"]["code"] == "dispute_open"


async def test_partner_down_on_release_changes_nothing(api, db, world, mocks):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    mocks.escrow.unavailable = True
    response = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    assert response.status_code == 503
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.HELD
    assert "escrow.released" not in [e.memo for e in await ledger(db)]
    mocks.escrow.unavailable = False
    assert (
        await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    ).status_code == 200


async def test_partner_released_but_our_commit_failed_catches_up(api, db, world, mocks):
    """The partner released, then we crashed before saving: the retry finds it released."""
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    escrow = await escrow_row(db, world.booking_id)
    await mocks.escrow.release(escrow.partner_ref)  # partner ahead of us
    response = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    assert response.status_code == 200
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.RELEASED
    assert [e.memo for e in await ledger(db)].count("escrow.released") == 2


# ================================================================== refund
async def test_ops_refund_returns_money_and_cancels_booking(api, db, world, sms):
    await pay(api, world)
    response = await api.post(
        f"/bookings/{world.booking_id}/escrow/refund",
        json={"reason": "Family cancelled; caregiver agreed."},
        headers=world.ops_h,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "refunded" and body["refund_method"] == "human"
    assert body["refunded_by"] == str(world.ops.id)
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.CANCELLED
    shifts = list(await db.scalars(select(Shift).where(Shift.booking_id == world.booking_id)))
    assert all(s.status is ShiftStatus.CANCELLED for s in shifts)
    (payment,) = await payments_for(db, world.booking_id)
    assert payment.status is PaymentStatus.REFUNDED
    assert await balances(db) == {}  # every account back to zero
    assert_groups_balance(await ledger(db))
    assert any("being refunded" in m.body for m in sms.outbox)


async def test_refund_is_ops_only_and_needs_a_reason(api, world):
    await pay(api, world)
    url = f"/bookings/{world.booking_id}/escrow/refund"
    patient = await api.post(url, json={"reason": "please"}, headers=world.patient)
    assert patient.status_code == 403
    assert (await api.post(url, json={}, headers=world.ops_h)).status_code == 422
    assert (await api.post(url, json={"reason": "  "}, headers=world.ops_h)).status_code == 422


async def test_refund_after_release_is_refused(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    response = await api.post(
        f"/bookings/{world.booking_id}/escrow/refund",
        json={"reason": "too late"},
        headers=world.ops_h,
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "escrow_already_released"
    )


async def test_refund_twice_returns_once(api, db, world):
    await pay(api, world)
    url = f"/bookings/{world.booking_id}/escrow/refund"
    results = await asyncio.gather(
        *(api.post(url, json={"reason": "duplicate taps"}, headers=world.ops_h) for _ in range(3))
    )
    assert all(r.status_code == 200 for r in results)
    assert [e.memo for e in await ledger(db)].count("escrow.refunded") == 4  # one posting
    assert await balances(db) == {}


async def test_release_after_refund_is_refused(api, db, world):
    await pay(api, world)
    await api.post(
        f"/bookings/{world.booking_id}/escrow/refund",
        json={"reason": "cancelled"},
        headers=world.ops_h,
    )
    response = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.ops_h)
    assert response.status_code == 409


async def test_completed_booking_refunded_by_ops_stays_completed(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    response = await api.post(
        f"/bookings/{world.booking_id}/escrow/refund",
        json={"reason": "goodwill refund"},
        headers=world.ops_h,
    )
    assert response.status_code == 200
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.COMPLETED


# ================================================================== settlement sweep
async def test_rule_releases_a_clean_booking_after_the_hold_period(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id, checked_out_ago=timedelta(hours=25))
    result = await _settle(api, world)
    assert result["released"] == 1 and result["errors"] == 0
    escrow = await escrow_row(db, world.booking_id)
    assert escrow.status is EscrowStatus.RELEASED
    assert escrow.release_method.value == "rule" and escrow.released_by is None
    again = await _settle(api, world)
    assert again["released"] == 0
    assert [e.memo for e in await ledger(db)].count("escrow.released") == 2


async def test_rule_waits_for_the_hold_period(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id, checked_out_ago=timedelta(hours=2))
    assert (await _settle(api, world))["released"] == 0
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.HELD


async def test_rule_never_releases_a_flagged_check_in(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id, inside=False)
    assert (await _settle(api, world))["released"] == 0


async def test_rule_never_releases_without_a_check_out(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    await db.execute(text("DELETE FROM check_ins WHERE kind = 'check_out'"))
    await db.commit()
    assert (await _settle(api, world))["released"] == 0


async def test_rule_never_releases_with_a_missed_shift(api, db, world):
    from app.domains.caregivers.models import Caregiver  # noqa: F401

    caregiver = world.caregiver
    booking = await make_booking(
        db,
        patient_id=world.patient_id,
        caregiver=caregiver,
        booked_by=world.patient_user.id,
        starts_in=timedelta(days=5),
        shifts=2,
    )
    response = await hold(api, booking.id, world.patient)
    await api.post(f"/payments/{response.json()['payment']['id']}/verify", headers=world.patient)
    await complete_booking(db, booking.id)
    first = await db.scalar(
        select(Shift).where(Shift.booking_id == booking.id).order_by(Shift.scheduled_start)
    )
    first.status = ShiftStatus.MISSED
    await db.commit()
    result = await _settle(api, world)
    assert (await escrow_row(db, booking.id)).status is EscrowStatus.HELD
    assert result["errors"] == 0


async def test_rule_never_releases_with_an_open_incident(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    report = await api.post(
        "/incidents",
        json={
            "booking_id": str(world.booking_id),
            "category": "safety",
            "description": "Left the gas on overnight.",
        },
        headers=world.patient,
    )
    assert report.status_code == 201
    assert (await _settle(api, world))["released"] == 0
    # A human can still decide to release.
    assert (
        await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.ops_h)
    ).status_code == 200


async def test_rule_refunds_a_no_show(api, db, world):
    await pay(api, world)
    await db.execute(
        text("UPDATE bookings SET status = 'cancelled' WHERE id = :id"), {"id": world.booking_id}
    )
    await db.commit()
    result = await _settle(api, world)
    assert result["refunded"] == 1
    escrow = await escrow_row(db, world.booking_id)
    assert escrow.status is EscrowStatus.REFUNDED and escrow.refund_method.value == "rule"
    assert escrow.refunded_by is None
    assert await balances(db) == {}


async def test_rule_closes_an_unfunded_escrow_of_a_cancelled_booking(api, db, world, mocks):
    await hold(api, world.booking_id, world.patient)
    await db.execute(
        text("UPDATE bookings SET status = 'cancelled' WHERE id = :id"), {"id": world.booking_id}
    )
    await db.commit()
    result = await _settle(api, world)
    assert result["closed_unfunded"] == 1 and result["refunded"] == 0
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.REFUNDED
    assert await ledger(db) == []


async def test_sweep_recovers_a_lost_webhook(api, db, world):
    response = await hold(api, world.booking_id, world.patient)
    await db.execute(update(Payment).values(created_at=datetime.now(UTC) - timedelta(minutes=30)))
    await db.commit()
    result = await _settle(api, world)
    assert result["payments_reconciled"] == 1
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.PAID
    assert len(await ledger(db)) == 4
    assert response.status_code == 201


async def test_sweep_leaves_fresh_checkouts_alone(api, db, world):
    await hold(api, world.booking_id, world.patient)
    result = await _settle(api, world)
    assert result["payments_reconciled"] == 0
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.ACCEPTED


async def test_sweep_gives_up_on_abandoned_checkouts(api, db, world, mocks):
    response = await hold(api, world.booking_id, world.patient)
    mocks.paystack.set_outcome(response.json()["payment"]["reference"], GatewayStatus.INITIATED)
    await db.execute(update(Payment).values(created_at=datetime.now(UTC) - timedelta(hours=49)))
    await db.commit()
    result = await _settle(api, world)
    assert result["payments_abandoned"] == 1
    (payment,) = await payments_for(db, world.booking_id)
    assert payment.status is PaymentStatus.FAILED and payment.failure_reason == "abandoned"


async def test_sweep_retries_funding_after_partner_outage(api, db, world, mocks):
    response = await hold(api, world.booking_id, world.patient)
    body = paystack_event(response.json()["payment"]["reference"])
    mocks.escrow.unavailable = True
    await api.post("/payments/webhooks/paystack", content=body, headers=paystack_headers(body))
    mocks.escrow.unavailable = False
    result = await _settle(api, world)
    assert result["payments_reconciled"] == 1
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.PAID
    assert_groups_balance(await ledger(db))


async def test_sweep_counts_errors_and_carries_on(api, db, world, mocks):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    mocks.escrow.unavailable = True
    result = await _settle(api, world)
    assert result["errors"] == 1 and result["released"] == 0
    mocks.escrow.unavailable = False
    assert (await _settle(api, world))["released"] == 1


async def test_settle_is_ops_only(api, world):
    assert (await api.post("/payments/ops/settle", headers=world.patient)).status_code == 403


async def test_rule_release_ignores_device_backdating(api, db, world):
    """A check-out backdated on the phone doesn't shorten the hold: the server clock counts."""
    await pay(api, world)
    await complete_booking(db, world.booking_id, checked_out_ago=timedelta(hours=1))
    await db.execute(update(CheckIn).values(client_timestamp=datetime.now(UTC) - timedelta(days=3)))
    await db.commit()
    assert (await _settle(api, world))["released"] == 0


# ================================================================== reads
async def test_escrow_view_by_role(api, db, world):
    await pay(api, world)
    patient = await api.get(f"/bookings/{world.booking_id}/escrow", headers=world.patient)
    assert patient.status_code == 200
    assert patient.json()["escrow"]["status"] == "held" and len(patient.json()["payments"]) == 1
    carer = await api.get(f"/bookings/{world.booking_id}/escrow", headers=world.carer)
    assert carer.json()["escrow"]["amount_minor"] == TOTAL and carer.json()["payments"] == []
    stranger = await api.get(f"/bookings/{world.booking_id}/escrow", headers=auth(world.stranger))
    assert stranger.status_code == 404
    unpaid = await make_booking(
        db,
        patient_id=world.patient_id,
        caregiver=world.caregiver,
        booked_by=world.patient_user.id,
        starts_in=timedelta(days=9),
    )
    empty = await api.get(f"/bookings/{unpaid.id}/escrow", headers=world.patient)
    assert empty.json()["escrow"] is None and empty.json()["payments"] == []


async def test_ledger_is_ops_only(api, world):
    await pay(api, world)
    assert (
        await api.get(f"/bookings/{world.booking_id}/ledger", headers=world.patient)
    ).status_code == 403
    entries = (await api.get(f"/bookings/{world.booking_id}/ledger", headers=world.ops_h)).json()
    assert [e["memo"] for e in entries] == ["payment.captured"] * 2 + ["escrow.funded"] * 2


# ================================================================== ledger guarantees
async def test_database_refuses_an_unbalanced_posting(db, world):
    group = uuid.uuid4()
    db.add(
        LedgerEntry(
            txn_group=group,
            account=LedgerAccount.ESCROW,
            direction=EntryDirection.DEBIT,
            amount_minor=100,
            currency="NGN",
            memo="test",
            booking_id=world.booking_id,
        )
    )
    db.add(
        LedgerEntry(
            txn_group=group,
            account=LedgerAccount.FAMILY_FUNDS,
            direction=EntryDirection.CREDIT,
            amount_minor=99,
            currency="NGN",
            memo="test",
            booking_id=world.booking_id,
        )
    )
    with pytest.raises(IntegrityError, match="does not balance"):
        await db.commit()
    await db.rollback()


async def test_database_refuses_mixed_currencies_in_a_group(db, world):
    group = uuid.uuid4()
    db.add(
        LedgerEntry(
            txn_group=group,
            account=LedgerAccount.ESCROW,
            direction=EntryDirection.DEBIT,
            amount_minor=100,
            currency="NGN",
            memo="test",
        )
    )
    db.add(
        LedgerEntry(
            txn_group=group,
            account=LedgerAccount.ESCROW,
            direction=EntryDirection.CREDIT,
            amount_minor=100,
            currency="USD",
            memo="test",
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_ledger_is_append_only(api, db, world):
    await pay(api, world)
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("UPDATE ledger_entries SET amount_minor = 1"))
    await db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("DELETE FROM ledger_entries"))
    await db.rollback()
    assert len(await ledger(db)) == 4


@pytest.mark.parametrize("amount", [0, -5])
async def test_database_refuses_non_positive_amounts(db, amount):
    group = uuid.uuid4()
    db.add(
        LedgerEntry(
            txn_group=group,
            account=LedgerAccount.ESCROW,
            direction=EntryDirection.DEBIT,
            amount_minor=amount,
            currency="NGN",
            memo="test",
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


def test_post_entries_refuses_bad_postings():
    class _Sink:
        def add(self, _):
            raise AssertionError("nothing should be added")

    for lines in (
        [],
        [(LedgerAccount.ESCROW, EntryDirection.DEBIT, 5)],
        [
            (LedgerAccount.ESCROW, EntryDirection.DEBIT, 5),
            (LedgerAccount.FAMILY_FUNDS, EntryDirection.CREDIT, 4),
        ],
        [
            (LedgerAccount.ESCROW, EntryDirection.DEBIT, 0),
            (LedgerAccount.FAMILY_FUNDS, EntryDirection.CREDIT, 0),
        ],
    ):
        with pytest.raises(payments.LedgerError):
            payments.post_entries(_Sink(), "bad", "NGN", lines, booking_id=None)  # type: ignore[arg-type]


async def test_escrow_constraints_hold_in_the_database(api, db, world):
    await pay(api, world)
    with pytest.raises(IntegrityError):
        await db.execute(
            update(EscrowTransaction).values(status=EscrowStatus.RELEASED)  # no time, no method
        )
        await db.commit()
    await db.rollback()
    with pytest.raises(IntegrityError):
        await db.execute(
            update(EscrowTransaction).values(
                status=EscrowStatus.RELEASED, released_at=datetime.now(UTC), release_method="human"
            )  # human release without a named person
        )
        await db.commit()
    await db.rollback()


async def test_full_flow_leaves_only_the_caregiver_payable(api, db, world):
    """Pay, check in and out, release: every account nets to zero except what the gateway
    collected (gateway_clearing) and what the caregiver is owed (caregiver_payable)."""
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    await _settle(api, world)
    totals = await balances(db)
    assert totals == {"gateway_clearing": TOTAL, "caregiver_payable": -TOTAL}
    assert sum(totals.values()) == 0
    entries = await ledger(db)
    assert_groups_balance(entries)
    assert len({e.txn_group for e in entries}) == 3


async def test_audit_trail_covers_every_money_step(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    actions = await _audit_actions(db)
    for expected in (
        "escrow.open",
        "payment.initiate",
        "payment.succeed",
        "escrow.hold",
        "booking.paid",
        "escrow.release",
    ):
        assert expected in actions, (expected, actions)


async def test_hold_without_a_body_defaults_to_paystack(api, world):
    response = await api.post(f"/bookings/{world.booking_id}/escrow/hold", headers=world.patient)
    assert response.status_code == 201
    assert response.json()["payment"]["gateway"] == "paystack"


async def test_money_moves_only_by_rule_or_named_human(db, world, mocks):
    """BK-2: there is no path for an agent. A rule has no person; a human must be one."""
    from app.domains.payments.models import SettlementMethod

    someone = payments.Actor(user_id=world.ops.id, role=world.ops.role)
    with pytest.raises(payments.PaymentAccessDenied):
        await payments.release_escrow(
            db, someone, world.booking_id, escrow_client=mocks.escrow, method=SettlementMethod.RULE
        )
    with pytest.raises(payments.PaymentAccessDenied):
        await payments.release_escrow(
            db, None, world.booking_id, escrow_client=mocks.escrow, method=SettlementMethod.HUMAN
        )
    with pytest.raises(payments.PaymentAccessDenied):
        await payments.refund_escrow(
            db,
            someone,
            world.booking_id,
            "x",
            escrow_client=mocks.escrow,
            method=SettlementMethod.RULE,
        )
    with pytest.raises(payments.PaymentAccessDenied):
        await payments.refund_escrow(
            db,
            None,
            world.booking_id,
            "x",
            escrow_client=mocks.escrow,
            method=SettlementMethod.HUMAN,
        )
