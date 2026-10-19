"""payments business logic: escrowed booking payments (BK-2, BK-5, BK-6).

CareKin never holds money. The payer pays through a gateway (Paystack locally, Stripe
from abroad); the money funds an escrow at the licensed partner; the escrow is released
to the caregiver or refunded to the payer. Our tables mirror that and post every
movement to a double-entry ledger.

Money flow and ledger postings (each line set is one balanced txn_group):

    payment.captured   DR gateway_clearing   CR family_funds       gateway confirmed it
    escrow.funded      DR family_funds       CR escrow             partner holds it
    escrow.released    DR escrow             CR caregiver_payable  (+ CR platform_revenue)
    escrow.refunded    DR escrow             CR family_funds
                       DR family_funds       CR gateway_clearing   back to the payer

Money that arrives but can't go into escrow (a second payment for a booking already
paid, a payment after the booking was cancelled, a wrong amount) is captured into
family_funds and flagged: payments.needs_review is set and an incident is opened so a
human refunds it. Nothing is ever refunded or released by an agent (PRD section 6).

Who may do what:
- hold:     the patient, a guardian, or linked family whose link allows funding
            (FamilyLink.can_fund). Caregivers and ops can't pay.
- verify:   the payer, anyone on the patient side, or ops.
- release:  a human (the patient or a guardian when the booking is completed, or ops),
            or the settlement rule. Refused while a dispute is open (TS-5).
- refund:   ops, or the settlement rule for cancelled bookings.

Lock order, everywhere: booking row, then escrow row, then payment row. Partner and
gateway calls are idempotent (by booking id and payment reference), so if our commit
fails after the partner acted, a retry finds the partner already done and catches up.

Webhooks are never trusted: the signature is checked, the delivery is stored once per
(gateway, event_id), and the payment is confirmed by calling the gateway's verify API.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import case, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.audit import write_audit
from app.core.exceptions import (
    BadRequestError,
    CareKinError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.core.permissions import UserRole
from app.domains.auth.models import User
from app.domains.bookings import service as bookings
from app.domains.bookings.models import (
    Booking,
    BookingStatus,
    CheckIn,
    CheckInKind,
    Shift,
    ShiftStatus,
)
from app.domains.bookings.notifications import NO_NOTICES, Notifier
from app.domains.patients.models import FamilyLink
from app.domains.payments import notifications as texts
from app.domains.payments.models import (
    EntryDirection,
    EscrowStatus,
    EscrowTransaction,
    Gateway,
    LedgerAccount,
    LedgerEntry,
    Payment,
    PaymentStatus,
    SettlementMethod,
    WebhookEvent,
)
from app.domains.payments.schemas import (
    BookingMoneyRead,
    CheckoutRead,
    EscrowHoldRequest,
    EscrowRead,
    PaymentRead,
    PaymentVerifyRead,
)
from app.domains.trust.models import OPEN_STATUSES, Incident, IncidentCategory, Severity
from app.domains.trust.rules import open_system_incident
from app.integrations.base import IntegrationError
from app.integrations.escrow import EscrowClient, EscrowRecord, EscrowStateError
from app.integrations.payments import PaymentClient, get_gateway_client

logger = logging.getLogger(__name__)

Actor = bookings.Actor

# Rule-based release waits this long after the last check-out, so the family can
# raise a dispute first (TS-5).
RELEASE_HOLD_PERIOD = timedelta(hours=24)
# The sweep asks the gateway about payments still waiting after this long (a webhook
# may have been lost), and gives up on them after ABANDON_AFTER.
RECONCILE_AFTER = timedelta(minutes=10)
ABANDON_AFTER = timedelta(hours=48)
# Webhook bodies are small JSON; anything bigger is refused before parsing.
MAX_WEBHOOK_BYTES = 256 * 1024
# Payment failed before a checkout existed: no gateway will ever report on it.
NO_CHECKOUT = "checkout_not_created"


# ---------------------------------------------------------------- errors
class PaymentNotFound(NotFoundError):
    code = "payment_not_found"


class PaymentAccessDenied(ForbiddenError):
    code = "payment_forbidden"


class EscrowConflict(ConflictError):
    """The escrow or booking isn't in a state that allows this."""

    code = "escrow_conflict"


class PaymentRuleViolation(CareKinError):
    status_code = 422
    code = "payment_rule_violation"


class WebhookSignatureInvalid(CareKinError):
    status_code = 401
    code = "webhook_signature_invalid"
    message = "The webhook signature is missing or wrong."


class WebhookMalformed(BadRequestError):
    code = "webhook_malformed"


class LedgerError(RuntimeError):
    """A posting that doesn't balance. Always a bug; never shown to users as a 4xx."""


# ---------------------------------------------------------------- gateway registry
class Gateways:
    """Hands out one payment client per gateway, created on first use, so a missing
    Stripe configuration only matters to Stripe payments. Overridden in tests."""

    def __init__(self, clients: dict[Gateway, PaymentClient] | None = None) -> None:
        self._clients: dict[Gateway, PaymentClient] = dict(clients or {})

    def get(self, gateway: Gateway) -> PaymentClient:
        if gateway not in self._clients:
            self._clients[gateway] = get_gateway_client(gateway)
        return self._clients[gateway]


def get_gateways() -> Gateways:
    """FastAPI dependency."""
    return Gateways()


# ---------------------------------------------------------------- helpers
def _now() -> datetime:
    return datetime.now(UTC)


def _audit(
    db: AsyncSession,
    actor: Actor | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    metadata: dict[str, object] | None = None,
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


async def commit(db: AsyncSession) -> None:
    """Commit. A ledger that doesn't balance is a bug (500), not a user conflict."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        text = str(exc.orig)
        if "ledger" in text:
            logger.error("Ledger guarantee failed: %s", text)
            raise LedgerError(text) from exc
        raise EscrowConflict("The change conflicts with existing data") from exc


def post_entries(
    db: AsyncSession,
    memo: str,
    currency: str,
    lines: Sequence[tuple[LedgerAccount, EntryDirection, int]],
    *,
    booking_id: uuid.UUID | None,
    payment_id: uuid.UUID | None = None,
    escrow_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Add one balanced posting. Checked here, and again by the database at commit."""
    debits = sum(amount for _, side, amount in lines if side is EntryDirection.DEBIT)
    credits = sum(amount for _, side, amount in lines if side is EntryDirection.CREDIT)
    if not lines or debits != credits or any(amount <= 0 for _, _, amount in lines):
        raise LedgerError(f"Unbalanced or empty posting {memo}: {debits} != {credits}")
    group = uuid.uuid4()
    for account, side, amount in lines:
        db.add(
            LedgerEntry(
                txn_group=group,
                account=account,
                direction=side,
                amount_minor=amount,
                currency=currency,
                memo=memo,
                booking_id=booking_id,
                payment_id=payment_id,
                escrow_id=escrow_id,
            )
        )
    return group


async def lock_booking(db: AsyncSession, booking_id: uuid.UUID) -> Booking:
    booking = await db.get(Booking, booking_id, with_for_update=True, populate_existing=True)
    if booking is None:
        raise bookings.BookingNotFound("Booking not found")
    return booking


async def _escrow_for(
    db: AsyncSession, booking_id: uuid.UUID, *, lock: bool = False
) -> EscrowTransaction | None:
    query = select(EscrowTransaction).where(EscrowTransaction.booking_id == booking_id)
    if lock:
        query = query.with_for_update()
    return await db.scalar(query.execution_options(populate_existing=True))


async def has_open_dispute(db: AsyncSession, booking_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            select(
                exists().where(
                    Incident.booking_id == booking_id,
                    Incident.is_dispute.is_(True),
                    Incident.status.in_(OPEN_STATUSES),
                )
            )
        )
    )


async def can_fund(db: AsyncSession, patient_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Family abroad pays only if the patient's family link allows it (FamilyLink.can_fund)."""
    allowed = await db.scalar(
        select(FamilyLink.can_fund).where(
            FamilyLink.patient_id == patient_id, FamilyLink.user_id == user_id
        )
    )
    return bool(allowed)


def _idempotency_hash(payer_id: uuid.UUID, booking_id: uuid.UUID, key: str) -> str:
    return hashlib.sha256(f"{payer_id}:{booking_id}:{key}".encode()).hexdigest()


def _return_url(booking_id: uuid.UUID) -> str:
    base = get_settings().PAYMENT_RETURN_URL
    return f"{base}{'&' if '?' in base else '?'}booking={booking_id}"


def payment_read(payment: Payment) -> PaymentRead:
    return PaymentRead.model_validate(payment)


def escrow_read(escrow: EscrowTransaction) -> EscrowRead:
    return EscrowRead.model_validate(escrow)


def _checkout_read(
    payment: Payment, escrow: EscrowTransaction | None, reused: bool
) -> CheckoutRead:
    waiting = payment.status is PaymentStatus.INITIATED
    return CheckoutRead(
        payment=payment_read(payment),
        authorization_url=payment.authorization_url if waiting else None,
        escrow_status=escrow.status if escrow else EscrowStatus.PENDING,
        reused=reused,
    )


def _apply_partner(escrow: EscrowTransaction, record: EscrowRecord) -> None:
    escrow.partner_status = record.status


async def sync_partner(escrow: EscrowTransaction, escrow_client: EscrowClient) -> bool:
    """Bring the partner in line with our state after an outage. True if a call was made.

    Our state wins: a dispute must hold money even when the partner was unreachable when
    it was opened, and closing one must free it.
    """
    if escrow.partner_status is escrow.status:
        return False
    if escrow.status is EscrowStatus.ON_HOLD_DISPUTE:
        record = await escrow_client.open_dispute(escrow.partner_ref, reason="dispute open")
    elif (
        escrow.status is EscrowStatus.HELD and escrow.partner_status is EscrowStatus.ON_HOLD_DISPUTE
    ):
        record = await escrow_client.close_dispute(escrow.partner_ref)
    else:
        record = await escrow_client.get(escrow.partner_ref)
        if record.status is not escrow.status:
            logger.error(
                "Escrow %s is %s here but %s at the partner; needs a human",
                escrow.id,
                escrow.status.value,
                record.status.value,
            )
    _apply_partner(escrow, record)
    return True


def _flag_for_review(db: AsyncSession, payment: Payment, reason: str, description: str) -> None:
    """Money we hold a record of but couldn't place in escrow: a human must refund it."""
    payment.needs_review = True
    payment.review_reason = reason
    open_system_incident(
        db,
        booking_id=payment.booking_id,
        category=IncidentCategory.PAYMENT,
        severity=Severity.HIGH,
        description=f"Payment {payment.reference} needs review ({reason}). {description}",
    )
    _audit(
        db,
        None,
        "payment.needs_review",
        "payment",
        payment.id,
        {"reason": reason, "booking_id": str(payment.booking_id)},
    )


# ---------------------------------------------------------------- notices
async def _notify_paid(db: AsyncSession, notifier: Notifier, booking: Booking) -> None:
    when = bookings.when(booking.starts_at)
    await bookings.notify(
        db, notifier, lambda: bookings.caregiver_phones(db, booking), texts.paid_to_caregiver(when)
    )
    await bookings.notify(
        db, notifier, lambda: bookings.booker_phones(db, booking), texts.paid_to_family(when)
    )


# ---------------------------------------------------------------- hold
async def hold(
    db: AsyncSession,
    actor: Actor,
    booking_id: uuid.UUID,
    data: EscrowHoldRequest,
    *,
    idempotency_key: str | None,
    gateways: Gateways,
    escrow_client: EscrowClient,
) -> tuple[CheckoutRead, bool]:
    """Open the escrow and a gateway checkout for an accepted booking (BK-2, BK-5).

    Returns (checkout, created). A repeated call (same Idempotency-Key, or the same
    payer and gateway while a checkout is still open) hands back the same checkout, so
    a double tap can't charge twice.
    """
    access = await bookings.get_booking_access(db, actor, booking_id)
    if access.role != "patient_side":
        raise PaymentAccessDenied("Only the patient, a guardian or linked family can pay for care")
    if not access.can_manage and not await can_fund(db, access.booking.patient_id, actor.user_id):
        raise PaymentAccessDenied(
            "The patient hasn't allowed you to fund their care", code="funding_not_allowed"
        )
    booking = await lock_booking(db, booking_id)
    escrow = await _escrow_for(db, booking.id, lock=True)

    key_hash = (
        _idempotency_hash(actor.user_id, booking.id, idempotency_key) if idempotency_key else None
    )
    if key_hash is not None:
        earlier = await db.scalar(select(Payment).where(Payment.idempotency_key == key_hash))
        if earlier is not None:
            if earlier.gateway is not data.gateway:
                raise EscrowConflict(
                    "This Idempotency-Key was already used for a different request",
                    code="idempotency_key_reused",
                )
            return _checkout_read(earlier, escrow, reused=True), False

    if booking.status in (
        BookingStatus.PAID,
        BookingStatus.IN_PROGRESS,
        BookingStatus.COMPLETED,
        BookingStatus.DISPUTED,
    ):
        raise EscrowConflict("This booking is already paid", code="booking_already_paid")
    if booking.status is not BookingStatus.ACCEPTED:
        raise EscrowConflict(
            f"This booking is {booking.status.value}; only an accepted booking can be paid",
            code="booking_not_payable",
            details={"status": booking.status.value},
        )
    now = _now()
    window_closed = booking.starts_at <= now or (
        booking.accepted_at is not None and booking.accepted_at + bookings.PAYMENT_WINDOW <= now
    )
    if window_closed:
        raise PaymentRuleViolation(
            "The time to pay for this booking has passed", code="payment_window_closed"
        )
    if booking.total_minor <= 0:
        raise PaymentRuleViolation("This booking has nothing to pay", code="nothing_to_pay")
    if escrow is not None and escrow.status is not EscrowStatus.PENDING:
        raise EscrowConflict(
            f"The escrow for this booking is {escrow.status.value}", code="escrow_not_pending"
        )
    if escrow is not None and (escrow.amount_minor, escrow.currency) != (
        booking.total_minor,
        booking.currency,
    ):
        raise EscrowConflict("The booking amount changed after the escrow was opened")

    open_checkout = await db.scalar(
        select(Payment)
        .where(
            Payment.booking_id == booking.id,
            Payment.payer_id == actor.user_id,
            Payment.gateway == data.gateway,
            Payment.status == PaymentStatus.INITIATED,
            Payment.authorization_url.is_not(None),
            Payment.amount_minor == booking.total_minor,
        )
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    if open_checkout is not None:
        return _checkout_read(open_checkout, escrow, reused=True), False

    # The partner escrow first: keyed by booking id, so a retry reuses it.
    record = await escrow_client.create(
        reference=str(booking.id),
        amount_minor=booking.total_minor,
        currency=booking.currency,
        payer_ref=str(actor.user_id),
        payee_ref=str(booking.caregiver_id),
        description="CareKin care booking",
    )
    if escrow is None:
        escrow = EscrowTransaction(
            booking_id=booking.id,
            partner=getattr(escrow_client, "provider", "unknown"),
            partner_ref=record.escrow_ref,
            status=EscrowStatus.PENDING,
            partner_status=record.status,
            amount_minor=booking.total_minor,
            currency=booking.currency,
        )
        db.add(escrow)
        _audit(db, actor, "escrow.open", "escrow", escrow.id, {"booking_id": str(booking.id)})
    else:
        _apply_partner(escrow, record)

    payer = await db.get(User, actor.user_id)
    assert payer is not None
    email = payer.email or f"{payer.id.hex}@{get_settings().PAYMENT_EMAIL_DOMAIN}"
    reference = f"CK-{uuid.uuid4().hex.upper()}"
    checkout = await gateways.get(data.gateway).initialize(
        reference=reference,
        amount_minor=booking.total_minor,
        currency=booking.currency,
        email=email,
        callback_url=_return_url(booking.id),
        metadata={"booking_id": str(booking.id)},
    )
    payment = Payment(
        booking_id=booking.id,
        payer_id=actor.user_id,
        gateway=data.gateway,
        reference=reference,
        gateway_ref=checkout.provider_ref,
        idempotency_key=key_hash or hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        amount_minor=booking.total_minor,
        currency=booking.currency,
        status=PaymentStatus.INITIATED,
        authorization_url=checkout.authorization_url,
    )
    db.add(payment)
    _audit(
        db,
        actor,
        "payment.initiate",
        "payment",
        payment.id,
        {
            "booking_id": str(booking.id),
            "gateway": data.gateway.value,
            "amount_minor": booking.total_minor,
            "currency": booking.currency,
        },
    )
    await commit(db)
    await db.refresh(payment)
    return _checkout_read(payment, escrow, reused=False), True


# ---------------------------------------------------------------- confirm
Outcome = Literal[
    "pending", "failed", "funded", "needs_review", "already_processed", "funding_retry"
]


@dataclass
class ConfirmResult:
    outcome: Outcome
    payment: Payment
    escrow: EscrowTransaction | None
    booking: Booking


async def confirm_payment(
    db: AsyncSession,
    payment_id: uuid.UUID,
    *,
    client: PaymentClient,
    escrow_client: EscrowClient,
    from_webhook: bool = False,
) -> ConfirmResult:
    """Find out from the gateway what happened to a payment and move the money on.

    Idempotent: a payment already applied returns "already_processed" and posts nothing.
    The caller commits. Raises IntegrationError if the gateway can't be asked; a partner
    outage while funding returns "funding_retry" with the capture kept, so the money is
    on record and funding is retried later.
    """
    found = await db.get(Payment, payment_id)
    if found is None:
        raise PaymentNotFound("Payment not found")
    booking = await lock_booking(db, found.booking_id)
    escrow = await _escrow_for(db, booking.id, lock=True)
    payment = await db.get(Payment, payment_id, with_for_update=True, populate_existing=True)
    assert payment is not None

    def result(outcome: Outcome) -> ConfirmResult:
        return ConfirmResult(outcome, payment, escrow, booking)

    # A gateway that tells us about a payment we gave up on (or it declined) is asked
    # again: money can still arrive, and it must never be ignored.
    recheck_failed = (
        from_webhook
        and payment.status is PaymentStatus.FAILED
        and payment.failure_reason != NO_CHECKOUT
    )
    if payment.status is PaymentStatus.INITIATED or recheck_failed:
        verification = await client.verify(payment.reference, provider_ref=payment.gateway_ref)
        if verification.reference != payment.reference:
            raise IntegrationError(
                client.provider, "verification is for another payment", retryable=False
            )
        if verification.status is PaymentStatus.INITIATED:
            return result("pending")
        if verification.status in (PaymentStatus.FAILED, PaymentStatus.REFUNDED):
            if payment.status is PaymentStatus.INITIATED:
                payment.status = PaymentStatus.FAILED
                payment.failure_reason = (
                    "reversed_by_gateway"
                    if verification.status is PaymentStatus.REFUNDED
                    else "declined_or_expired"
                )
                _audit(
                    db,
                    None,
                    "payment.fail",
                    "payment",
                    payment.id,
                    {"reason": payment.failure_reason},
                )
            return result("failed")

        # Succeeded. The money is real from here on, so it goes on the ledger.
        payment.status = PaymentStatus.SUCCEEDED
        payment.failure_reason = None
        payment.paid_at = verification.paid_at or _now()
        if payment.gateway_ref is None and verification.provider_ref:
            payment.gateway_ref = verification.provider_ref
        amount, currency = verification.amount_minor, verification.currency.upper()
        if amount > 0:
            post_entries(
                db,
                "payment.captured",
                currency,
                [
                    (LedgerAccount.GATEWAY_CLEARING, EntryDirection.DEBIT, amount),
                    (LedgerAccount.FAMILY_FUNDS, EntryDirection.CREDIT, amount),
                ],
                booking_id=booking.id,
                payment_id=payment.id,
            )
        _audit(
            db,
            None,
            "payment.succeed",
            "payment",
            payment.id,
            {"amount_minor": amount, "currency": currency, "booking_id": str(booking.id)},
        )
        if (amount, currency) != (payment.amount_minor, payment.currency):
            _flag_for_review(
                db,
                payment,
                "amount_mismatch",
                f"The gateway reported {amount} {currency}; the booking needs "
                f"{payment.amount_minor} {payment.currency}.",
            )
            return result("needs_review")
    elif payment.status is PaymentStatus.SUCCEEDED:
        funded_by_this = escrow is not None and escrow.funded_by_payment_id == payment.id
        if payment.needs_review or funded_by_this:
            return result("already_processed")
        # Captured earlier but not placed in escrow yet (the partner was down): retry.
    else:
        return result("already_processed")

    # Place the money in escrow, if this booking can still take it.
    reason: str | None = None
    if escrow is None:
        reason = "no_escrow"
    elif escrow.funded_by_payment_id is not None:
        reason = "duplicate_payment"
    elif escrow.status is not EscrowStatus.PENDING:
        reason = "escrow_closed"
    elif booking.status is not BookingStatus.ACCEPTED:
        reason = "booking_not_payable"
    elif (escrow.amount_minor, escrow.currency) != (payment.amount_minor, payment.currency):
        reason = "amount_mismatch"
    if reason is not None:
        _flag_for_review(
            db,
            payment,
            reason,
            f"The booking is {booking.status.value} and its escrow is "
            f"{escrow.status.value if escrow else 'missing'}; refund the payer.",
        )
        return result("needs_review")
    assert escrow is not None

    try:
        record = await escrow_client.fund(escrow.partner_ref, payment_ref=payment.reference)
    except EscrowStateError as exc:
        _flag_for_review(db, payment, "partner_refused", f"The escrow partner refused: {exc}")
        return result("needs_review")
    except IntegrationError:
        logger.warning("Escrow partner unavailable while funding %s; will retry", escrow.id)
        return result("funding_retry")

    escrow.status = EscrowStatus.HELD
    _apply_partner(escrow, record)
    escrow.held_at = _now()
    escrow.funded_by_payment_id = payment.id
    post_entries(
        db,
        "escrow.funded",
        escrow.currency,
        [
            (LedgerAccount.FAMILY_FUNDS, EntryDirection.DEBIT, escrow.amount_minor),
            (LedgerAccount.ESCROW, EntryDirection.CREDIT, escrow.amount_minor),
        ],
        booking_id=booking.id,
        payment_id=payment.id,
        escrow_id=escrow.id,
    )
    await bookings.mark_paid(db, booking.id)
    _audit(
        db,
        None,
        "escrow.hold",
        "escrow",
        escrow.id,
        {"booking_id": str(booking.id), "payment_id": str(payment.id)},
    )
    return result("funded")


async def verify_payment(
    db: AsyncSession,
    actor: Actor,
    payment_id: uuid.UUID,
    *,
    gateways: Gateways,
    escrow_client: EscrowClient,
    notifier: Notifier = NO_NOTICES,
) -> PaymentVerifyRead:
    """The app calls this when the payer comes back from checkout, so it needn't wait
    for the webhook. Same code path as the webhook, so it can't double-credit."""
    payment = await db.get(Payment, payment_id)
    if payment is None:
        raise PaymentNotFound("Payment not found")
    if payment.payer_id != actor.user_id:
        try:
            access = await bookings.get_booking_access(db, actor, payment.booking_id)
        except bookings.BookingNotFound:
            raise PaymentNotFound("Payment not found") from None
        if access.role == "caregiver":
            raise PaymentNotFound("Payment not found")
    result = await confirm_payment(
        db, payment_id, client=gateways.get(payment.gateway), escrow_client=escrow_client
    )
    await commit(db)
    if result.outcome == "funded":
        await _notify_paid(db, notifier, result.booking)
    return PaymentVerifyRead(
        outcome=result.outcome,
        payment=payment_read(result.payment),
        escrow_status=result.escrow.status if result.escrow else None,
    )


# ---------------------------------------------------------------- webhooks
async def handle_webhook(
    db: AsyncSession,
    gateway: Gateway,
    body: bytes,
    signature: str | None,
    *,
    gateways: Gateways,
    escrow_client: EscrowClient,
    notifier: Notifier = NO_NOTICES,
) -> Literal["processed", "duplicate", "ignored"]:
    """BK-6: idempotent webhook into the ledger.

    401 for a bad signature and 400 for a body that isn't an event (the gateway won't
    fix either by retrying). 503 when the gateway or partner can't be reached, so the
    gateway retries. 200 otherwise, including replays and events we don't use.
    """
    if len(body) > MAX_WEBHOOK_BYTES:
        raise WebhookMalformed("Webhook body is too large")
    client = gateways.get(gateway)
    if not client.verify_webhook(body, signature):
        raise WebhookSignatureInvalid()
    try:
        notice = client.parse_webhook(body)
    except ValueError as exc:
        raise WebhookMalformed(str(exc)) from exc

    # 1. Store the delivery once. Its own transaction, so it's kept even if processing fails.
    await db.execute(
        pg_insert(WebhookEvent)
        .values(
            id=uuid.uuid4(),
            gateway=gateway,
            event_id=notice.event_id,
            event_type=notice.event_type,
            payload=notice.payload,
        )
        .on_conflict_do_nothing(index_elements=["gateway", "event_id"])
    )
    await db.commit()

    # 2. Process it, holding its row so a concurrent replay waits and then sees it done.
    event = await db.scalar(
        select(WebhookEvent)
        .where(WebhookEvent.gateway == gateway, WebhookEvent.event_id == notice.event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert event is not None
    if event.processed_at is not None:
        await db.rollback()
        return "duplicate"
    event.attempts += 1
    event_pk = event.id

    payment = None
    if notice.reference is not None:
        payment = await db.scalar(select(Payment).where(Payment.reference == notice.reference))
    if (
        payment is None
        or payment.gateway is not gateway
        or (
            notice.provider_ref is not None
            and payment.gateway is Gateway.STRIPE
            and payment.gateway_ref != notice.provider_ref
        )
    ):
        if notice.reference is not None:
            logger.warning("Webhook %s names no payment of ours; ignored", notice.event_id)
            event.last_error = "no matching payment"
        event.processed_at = _now()
        await db.commit()
        return "ignored"
    event.payment_id = payment.id

    try:
        result = await confirm_payment(
            db, payment.id, client=client, escrow_client=escrow_client, from_webhook=True
        )
    except (IntegrationError, ServiceUnavailableError) as exc:
        await db.rollback()
        await _record_webhook_error(db, event_pk, str(exc))
        raise
    if result.outcome == "funding_retry":
        event.last_error = "escrow partner unavailable; will retry"
        await commit(db)  # keep the capture; the gateway retries the delivery
        raise ServiceUnavailableError(
            "The escrow partner is unavailable. Retry later.", code="escrow_partner_unavailable"
        )
    event.processed_at = _now()
    event.last_error = None
    await commit(db)
    if result.outcome == "funded":
        await _notify_paid(db, notifier, result.booking)
    return "processed"


async def _record_webhook_error(db: AsyncSession, event_pk: uuid.UUID, message: str) -> None:
    await db.execute(
        update(WebhookEvent)
        .where(WebhookEvent.id == event_pk)
        .values(attempts=WebhookEvent.attempts + 1, last_error=message[:500])
    )
    await db.commit()


# ---------------------------------------------------------------- release and refund
async def _rule_release_ready(db: AsyncSession, booking: Booking, now: datetime) -> bool:
    """The settlement rule only releases a clean booking; anything else waits for a human.

    Clean: every shift completed with a check-in and a check-out, all inside the
    geofence, no open incident on the booking, and RELEASE_HOLD_PERIOD has passed since
    the last check-out reached the server (server clock, so a device can't backdate it).
    """
    shifts = list(await db.scalars(select(Shift).where(Shift.booking_id == booking.id)))
    if not shifts or any(s.status is not ShiftStatus.COMPLETED for s in shifts):
        return False
    check_ins = list(
        await db.scalars(
            select(CheckIn)
            .join(Shift, Shift.id == CheckIn.shift_id)
            .where(Shift.booking_id == booking.id)
        )
    )
    kinds = {(c.shift_id, c.kind) for c in check_ins}
    for shift in shifts:
        if (shift.id, CheckInKind.CHECK_IN) not in kinds or (
            shift.id,
            CheckInKind.CHECK_OUT,
        ) not in kinds:
            return False
    if any(not c.within_geofence for c in check_ins):
        return False
    open_incident = await db.scalar(
        select(
            exists().where(Incident.booking_id == booking.id, Incident.status.in_(OPEN_STATUSES))
        )
    )
    if open_incident:
        return False
    last_out = max(c.received_at for c in check_ins if c.kind is CheckInKind.CHECK_OUT)
    return last_out <= now - RELEASE_HOLD_PERIOD


async def release_escrow(
    db: AsyncSession,
    actor: Actor | None,
    booking_id: uuid.UUID,
    *,
    escrow_client: EscrowClient,
    method: SettlementMethod,
    notifier: Notifier = NO_NOTICES,
    now: datetime | None = None,
) -> EscrowTransaction:
    """Pay the caregiver (BK-2): a human, or the settlement rule. Never an agent.

    Refused while a dispute is open (TS-5). Releasing twice returns the released escrow.
    """
    if method is SettlementMethod.HUMAN:
        if actor is None:
            raise PaymentAccessDenied("A human release needs a person")
        access = await bookings.get_booking_access(db, actor, booking_id)
        if not (access.role == "ops" or access.can_manage):
            raise PaymentAccessDenied(
                "Only the patient, a guardian or CareKin operations can release payment"
            )
    elif actor is not None:
        raise PaymentAccessDenied("A rule-based release has no person")

    booking = await lock_booking(db, booking_id)
    escrow = await _escrow_for(db, booking.id, lock=True)
    if escrow is None:
        raise EscrowConflict("This booking hasn't been paid", code="escrow_not_funded")
    if escrow.status is EscrowStatus.RELEASED:
        return escrow
    if (
        escrow.status is EscrowStatus.ON_HOLD_DISPUTE
        or booking.status is BookingStatus.DISPUTED
        or await has_open_dispute(db, booking.id)
    ):
        raise EscrowConflict(
            "A dispute is open on this booking, so the payment stays on hold",
            code="dispute_open",
        )
    if escrow.status is not EscrowStatus.HELD:
        raise EscrowConflict(
            f"There is no payment held for this booking (escrow is {escrow.status.value})",
            code="escrow_not_held",
        )
    if booking.status is not BookingStatus.COMPLETED:
        raise EscrowConflict(
            "Payment is released once the booking is completed",
            code="booking_not_completed",
            details={"status": booking.status.value},
        )
    moment = now or _now()
    if method is SettlementMethod.RULE and not await _rule_release_ready(db, booking, moment):
        raise EscrowConflict("Not ready for rule-based release", code="release_not_ready")

    await sync_partner(escrow, escrow_client)
    record = await escrow_client.release(escrow.partner_ref)
    if record.status is not EscrowStatus.RELEASED:
        raise IntegrationError(escrow.partner, f"partner left the escrow {record.status.value}")

    amount = escrow.amount_minor
    fee = amount * get_settings().PLATFORM_COMMISSION_BPS // 10_000
    escrow.status = EscrowStatus.RELEASED
    _apply_partner(escrow, record)
    escrow.released_at = moment
    escrow.release_method = method
    escrow.released_by = actor.user_id if actor else None
    lines = [
        (LedgerAccount.ESCROW, EntryDirection.DEBIT, amount),
        (LedgerAccount.CAREGIVER_PAYABLE, EntryDirection.CREDIT, amount - fee),
    ]
    if fee:
        lines.append((LedgerAccount.PLATFORM_REVENUE, EntryDirection.CREDIT, fee))
    post_entries(
        db,
        "escrow.released",
        escrow.currency,
        lines,
        booking_id=booking.id,
        payment_id=escrow.funded_by_payment_id,
        escrow_id=escrow.id,
    )
    _audit(
        db,
        actor,
        "escrow.release",
        "escrow",
        escrow.id,
        {
            "booking_id": str(booking.id),
            "method": method.value,
            "amount_minor": amount,
            "fee_minor": fee,
        },
    )
    await commit(db)
    await bookings.notify(
        db,
        notifier,
        lambda: bookings.caregiver_phones(db, booking),
        texts.released(bookings.when(booking.starts_at)),
    )
    return escrow


async def refund_escrow(
    db: AsyncSession,
    actor: Actor | None,
    booking_id: uuid.UUID,
    reason: str,
    *,
    escrow_client: EscrowClient,
    method: SettlementMethod,
    notifier: Notifier = NO_NOTICES,
) -> EscrowTransaction:
    """Return the escrow to the payer: ops (a human), or the rule for cancelled bookings.

    Allowed while a dispute is open: that is how a dispute is settled in the family's
    favour. Refused once released. Refunding twice returns the refunded escrow.
    """
    if method is SettlementMethod.HUMAN:
        if actor is None or actor.role is not UserRole.OPS:
            raise PaymentAccessDenied("Only CareKin operations can refund a payment")
        await bookings.get_booking_access(db, actor, booking_id)
    elif actor is not None:
        raise PaymentAccessDenied("A rule-based refund has no person")

    booking = await lock_booking(db, booking_id)
    escrow = await _escrow_for(db, booking.id, lock=True)
    if escrow is None:
        raise EscrowConflict("This booking has no payment to refund", code="escrow_not_found")
    if escrow.status is EscrowStatus.REFUNDED:
        return escrow
    if escrow.status is EscrowStatus.RELEASED:
        raise EscrowConflict(
            "This payment was already released to the caregiver", code="escrow_already_released"
        )
    # Pending means nobody paid yet: the partner just closes it. A checkout still open
    # could be paid later; that payment is flagged for a refund, never put in this escrow.
    was_funded = escrow.status in (EscrowStatus.HELD, EscrowStatus.ON_HOLD_DISPUTE)
    record = await escrow_client.refund(escrow.partner_ref, reason=reason)
    if record.status is not EscrowStatus.REFUNDED:
        raise IntegrationError(escrow.partner, f"partner left the escrow {record.status.value}")

    escrow.status = EscrowStatus.REFUNDED
    _apply_partner(escrow, record)
    escrow.refunded_at = _now()
    escrow.refund_method = method
    escrow.refunded_by = actor.user_id if actor else None
    escrow.refund_reason = reason[:500]
    if was_funded:
        amount = escrow.amount_minor
        post_entries(
            db,
            "escrow.refunded",
            escrow.currency,
            [
                (LedgerAccount.ESCROW, EntryDirection.DEBIT, amount),
                (LedgerAccount.FAMILY_FUNDS, EntryDirection.CREDIT, amount),
                (LedgerAccount.FAMILY_FUNDS, EntryDirection.DEBIT, amount),
                (LedgerAccount.GATEWAY_CLEARING, EntryDirection.CREDIT, amount),
            ],
            booking_id=booking.id,
            payment_id=escrow.funded_by_payment_id,
            escrow_id=escrow.id,
        )
        if escrow.funded_by_payment_id is not None:
            funded = await db.get(Payment, escrow.funded_by_payment_id, with_for_update=True)
            if funded is not None:
                funded.status = PaymentStatus.REFUNDED
    await bookings.mark_refunded(db, booking.id, actor)
    _audit(
        db,
        actor,
        "escrow.refund",
        "escrow",
        escrow.id,
        {
            "booking_id": str(booking.id),
            "method": method.value,
            "was_funded": was_funded,
            "amount_minor": escrow.amount_minor if was_funded else 0,
        },
    )
    await commit(db)
    if was_funded:
        await bookings.notify(
            db,
            notifier,
            lambda: bookings.booker_phones(db, booking),
            texts.refunded(bookings.when(booking.starts_at)),
        )
    return escrow


# ---------------------------------------------------------------- disputes (trust domain)
async def hold_for_dispute(
    db: AsyncSession, booking_id: uuid.UUID, escrow_client: EscrowClient
) -> EscrowTransaction:
    """A dispute was opened: hold the escrow (TS-5). The caller holds the booking lock
    and commits. Never fails because the partner is down: our state holds the money (every
    release checks it), and the settlement sweep tells the partner later."""
    escrow = await _escrow_for(db, booking_id, lock=True)
    if escrow is None or escrow.status not in (EscrowStatus.HELD, EscrowStatus.ON_HOLD_DISPUTE):
        if escrow is not None and escrow.status is EscrowStatus.RELEASED:
            raise EscrowConflict(
                "The payment was already released, so it can't be held. Report the "
                "incident without a dispute and CareKin will follow up.",
                code="escrow_already_released",
            )
        raise EscrowConflict(
            "There is no payment held for this booking to dispute", code="nothing_to_dispute"
        )
    if escrow.status is EscrowStatus.HELD:
        escrow.status = EscrowStatus.ON_HOLD_DISPUTE
        try:
            record = await escrow_client.open_dispute(escrow.partner_ref, reason="dispute opened")
            _apply_partner(escrow, record)
        except IntegrationError:
            logger.warning(
                "Partner unreachable opening a dispute on %s; sweep will sync", escrow.id
            )
        _audit(
            db, None, "escrow.dispute_hold", "escrow", escrow.id, {"booking_id": str(booking_id)}
        )
    return escrow


async def end_dispute_hold(
    db: AsyncSession, booking_id: uuid.UUID, escrow_client: EscrowClient
) -> EscrowTransaction | None:
    """The last dispute closed: the escrow is held normally again. Caller commits."""
    escrow = await _escrow_for(db, booking_id, lock=True)
    if escrow is None or escrow.status is not EscrowStatus.ON_HOLD_DISPUTE:
        return escrow
    escrow.status = EscrowStatus.HELD
    try:
        record = await escrow_client.close_dispute(escrow.partner_ref)
        _apply_partner(escrow, record)
    except IntegrationError:
        logger.warning("Partner unreachable closing a dispute on %s; sweep will sync", escrow.id)
    _audit(
        db,
        None,
        "escrow.dispute_release_hold",
        "escrow",
        escrow.id,
        {"booking_id": str(booking_id)},
    )
    return escrow


# ---------------------------------------------------------------- reads
async def booking_money(db: AsyncSession, actor: Actor, booking_id: uuid.UUID) -> BookingMoneyRead:
    access = await bookings.get_booking_access(db, actor, booking_id)
    escrow = await _escrow_for(db, booking_id)
    payments: list[Payment] = []
    if access.role != "caregiver":
        payments = list(
            await db.scalars(
                select(Payment).where(Payment.booking_id == booking_id).order_by(Payment.created_at)
            )
        )
    return BookingMoneyRead(
        booking_id=booking_id,
        booking_status=access.booking.status.value,
        escrow=escrow_read(escrow) if escrow else None,
        payments=[payment_read(p) for p in payments],
    )


async def booking_ledger(
    db: AsyncSession, actor: Actor, booking_id: uuid.UUID
) -> list[LedgerEntry]:
    if actor.role is not UserRole.OPS:
        raise PaymentAccessDenied("Only CareKin operations can read the ledger")
    await bookings.get_booking_access(db, actor, booking_id)
    return list(
        await db.scalars(
            select(LedgerEntry).where(LedgerEntry.booking_id == booking_id).order_by(LedgerEntry.id)
        )
    )


async def account_balances(
    db: AsyncSession, booking_id: uuid.UUID | None = None
) -> dict[tuple[LedgerAccount, str], int]:
    """Debits minus credits per account and currency. Used by tests and ops checks."""
    signed = func.sum(
        case(
            (LedgerEntry.direction == EntryDirection.DEBIT, LedgerEntry.amount_minor),
            else_=-LedgerEntry.amount_minor,
        )
    )
    query = select(LedgerEntry.account, LedgerEntry.currency, signed).group_by(
        LedgerEntry.account, LedgerEntry.currency
    )
    if booking_id is not None:
        query = query.where(LedgerEntry.booking_id == booking_id)
    return {(account, currency): int(total) for account, currency, total in await db.execute(query)}


# ---------------------------------------------------------------- settlement sweep
@dataclass
class SettlementResult:
    released: int = 0
    refunded: int = 0
    closed_unfunded: int = 0
    payments_reconciled: int = 0
    payments_abandoned: int = 0
    partner_synced: int = 0
    errors: int = 0
    _funded: list[uuid.UUID] = field(default_factory=list, repr=False)

    def as_dict(self) -> dict[str, int]:
        return {
            "released": self.released,
            "refunded": self.refunded,
            "closed_unfunded": self.closed_unfunded,
            "payments_reconciled": self.payments_reconciled,
            "payments_abandoned": self.payments_abandoned,
            "partner_synced": self.partner_synced,
            "errors": self.errors,
        }


async def settle_escrows(
    db: AsyncSession,
    *,
    gateways: Gateways,
    escrow_client: EscrowClient,
    notifier: Notifier = NO_NOTICES,
    now: datetime | None = None,
) -> SettlementResult:
    """The rule-based side of BK-2 and BK-6. Run every 10 minutes (Celery beat), or by ops.

    1. Reconcile payments: ask the gateway about checkouts still waiting (a lost webhook
       must not lose money), give up on ones older than 48 hours, and retry funding for
       captured payments the partner couldn't take.
    2. Sync the partner with escrows whose state changed while it was unreachable.
    3. Refund escrows of cancelled bookings (a no-show); close unfunded ones.
    4. Release clean, completed bookings RELEASE_HOLD_PERIOD after the last check-out.

    Each item is its own transaction: one failure is counted and retried next run.
    """
    now = now or _now()
    out = SettlementResult()

    async def attempt(
        label: str, item: uuid.UUID, coro_factory: Callable[[], Awaitable[None]]
    ) -> None:
        try:
            await coro_factory()
        except EscrowConflict:
            await db.rollback()  # not ready, or a human got there first: nothing to do
        except Exception:
            await db.rollback()
            out.errors += 1
            logger.warning("Settlement step %s failed for %s", label, item, exc_info=True)

    # 1a. Waiting payments.
    waiting = list(
        await db.scalars(
            select(Payment.id).where(
                Payment.status == PaymentStatus.INITIATED,
                Payment.created_at <= now - RECONCILE_AFTER,
            )
        )
    )
    for payment_id in waiting:

        async def reconcile(payment_id: uuid.UUID = payment_id) -> None:
            payment = await db.get(Payment, payment_id)
            assert payment is not None
            result = await confirm_payment(
                db, payment_id, client=gateways.get(payment.gateway), escrow_client=escrow_client
            )
            if result.outcome == "pending" and result.payment.created_at <= now - ABANDON_AFTER:
                result.payment.status = PaymentStatus.FAILED
                result.payment.failure_reason = "abandoned"
                _audit(db, None, "payment.abandon", "payment", payment_id)
                out.payments_abandoned += 1
            elif result.outcome != "pending":
                out.payments_reconciled += 1
            if result.outcome == "funded":
                out._funded.append(result.booking.id)
            await commit(db)

        await attempt("reconcile", payment_id, reconcile)

    # 1b. Captured but never placed in escrow (partner outage), not flagged.
    funded_ids = select(EscrowTransaction.funded_by_payment_id).where(
        EscrowTransaction.funded_by_payment_id.is_not(None)
    )
    stranded = list(
        await db.scalars(
            select(Payment.id).where(
                Payment.status == PaymentStatus.SUCCEEDED,
                Payment.needs_review.is_(False),
                Payment.id.not_in(funded_ids),
            )
        )
    )
    for payment_id in stranded:

        async def refund_or_fund(payment_id: uuid.UUID = payment_id) -> None:
            payment = await db.get(Payment, payment_id)
            assert payment is not None
            result = await confirm_payment(
                db, payment_id, client=gateways.get(payment.gateway), escrow_client=escrow_client
            )
            if result.outcome in ("funded", "needs_review"):
                out.payments_reconciled += 1
            if result.outcome == "funded":
                out._funded.append(result.booking.id)
            await commit(db)

        await attempt("fund", payment_id, refund_or_fund)

    # 2. Partner out of step.
    lagging = list(
        await db.scalars(
            select(EscrowTransaction.id).where(
                EscrowTransaction.status != EscrowTransaction.partner_status
            )
        )
    )
    for escrow_id in lagging:

        async def sync(escrow_id: uuid.UUID = escrow_id) -> None:
            escrow = await db.get(EscrowTransaction, escrow_id)
            assert escrow is not None
            await lock_booking(db, escrow.booking_id)
            escrow = await _escrow_for(db, escrow.booking_id, lock=True)
            assert escrow is not None
            if await sync_partner(escrow, escrow_client):
                out.partner_synced += 1
            await commit(db)

        await attempt("sync", escrow_id, sync)

    # 3. Cancelled bookings: refund what is held, close what was never funded.
    cancelled = list(
        await db.execute(
            select(EscrowTransaction.booking_id, EscrowTransaction.status)
            .join(Booking, Booking.id == EscrowTransaction.booking_id)
            .where(
                Booking.status.in_((BookingStatus.CANCELLED, BookingStatus.DECLINED)),
                EscrowTransaction.status.in_((EscrowStatus.PENDING, EscrowStatus.HELD)),
            )
        )
    )
    for booking_id, status in cancelled:
        funded = status is EscrowStatus.HELD

        async def refund(booking_id: uuid.UUID = booking_id, funded: bool = funded) -> None:
            await refund_escrow(
                db,
                None,
                booking_id,
                "Booking cancelled: caregiver did not attend"
                if funded
                else "Booking cancelled before payment",
                escrow_client=escrow_client,
                method=SettlementMethod.RULE,
                notifier=notifier,
            )
            if funded:
                out.refunded += 1
            else:
                out.closed_unfunded += 1

        await attempt("refund", booking_id, refund)

    # 4. Completed bookings, released by rule once clean and past the hold period.
    completed = list(
        await db.scalars(
            select(EscrowTransaction.booking_id)
            .join(Booking, Booking.id == EscrowTransaction.booking_id)
            .where(
                Booking.status == BookingStatus.COMPLETED,
                EscrowTransaction.status == EscrowStatus.HELD,
            )
        )
    )
    for booking_id in completed:

        async def release(booking_id: uuid.UUID = booking_id) -> None:
            await release_escrow(
                db,
                None,
                booking_id,
                escrow_client=escrow_client,
                method=SettlementMethod.RULE,
                notifier=notifier,
                now=now,
            )
            out.released += 1

        await attempt("release", booking_id, release)

    for booking_id in out._funded:
        booking = await db.get(Booking, booking_id)
        if booking is not None:
            await _notify_paid(db, notifier, booking)
    return out
