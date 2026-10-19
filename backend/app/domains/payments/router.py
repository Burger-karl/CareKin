"""payments routes: escrow hold, release and refund on a booking; payment verification;
Paystack and Stripe webhooks; the ops settlement run (Day 11, BK-2, BK-5, BK-6).

The escrow routes live under /bookings/{id}/escrow/... as the endpoint plan names
them, but in this router, so the bookings router is untouched.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status

from app.core.permissions import UserRole, require_roles
from app.database import DbSession
from app.domains.auth.dependencies import CurrentUser
from app.domains.bookings.notifications import Notifier
from app.domains.bookings.router import get_notifier
from app.domains.payments import service
from app.domains.payments.models import Gateway, SettlementMethod
from app.domains.payments.schemas import (
    BookingMoneyRead,
    CheckoutRead,
    EscrowHoldRequest,
    EscrowRead,
    LedgerEntryRead,
    PaymentVerifyRead,
    RefundRequest,
    SettlementRead,
    WebhookAck,
)
from app.integrations.escrow import EscrowClient, get_escrow_client

router = APIRouter(tags=["payments"])


def get_actor(request: Request, user: CurrentUser) -> service.Actor:
    return service.Actor(user_id=user.id, role=user.role, request=request)


Actor = Annotated[service.Actor, Depends(get_actor)]
Notices = Annotated[Notifier, Depends(get_notifier)]
Gateways = Annotated[service.Gateways, Depends(service.get_gateways)]
Escrow = Annotated[EscrowClient, Depends(get_escrow_client)]
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
        pattern=r"^[A-Za-z0-9_.:-]+$",
        description="Send the same key when retrying, so a retry can't open a second payment.",
    ),
]


# ---------------------------------------------------------------- escrow on a booking
@router.post(
    "/bookings/{booking_id}/escrow/hold",
    response_model=CheckoutRead,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "An earlier checkout for the same request was returned."}},
)
async def hold_escrow(
    booking_id: uuid.UUID,
    db: DbSession,
    actor: Actor,
    gateways: Gateways,
    escrow: Escrow,
    response: Response,
    data: EscrowHoldRequest | None = None,
    idempotency_key: IdempotencyKey = None,
):
    """Start paying for an accepted booking: Paystack locally, Stripe for family abroad.

    Opens the escrow at the licensed partner and returns the gateway checkout link. The
    booking becomes `paid` once the gateway confirms (webhook or POST /payments/{id}/verify).
    """
    checkout, created = await service.hold(
        db,
        actor,
        booking_id,
        data or EscrowHoldRequest(),
        idempotency_key=idempotency_key,
        gateways=gateways,
        escrow_client=escrow,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return checkout


@router.get("/bookings/{booking_id}/escrow", response_model=BookingMoneyRead)
async def get_escrow(booking_id: uuid.UUID, db: DbSession, actor: Actor):
    """Escrow status and the payments made for a booking."""
    return await service.booking_money(db, actor, booking_id)


@router.post("/bookings/{booking_id}/escrow/release", response_model=EscrowRead)
async def release_escrow(
    booking_id: uuid.UUID, db: DbSession, actor: Actor, escrow: Escrow, notifier: Notices
):
    """Release the payment to the caregiver: the patient or a guardian confirming the care,
    or ops. Only for a completed booking; refused while a dispute is open (TS-5)."""
    released = await service.release_escrow(
        db,
        actor,
        booking_id,
        escrow_client=escrow,
        method=SettlementMethod.HUMAN,
        notifier=notifier,
    )
    return service.escrow_read(released)


@router.post(
    "/bookings/{booking_id}/escrow/refund",
    response_model=EscrowRead,
    dependencies=[require_roles(UserRole.OPS)],
)
async def refund_escrow(
    booking_id: uuid.UUID,
    data: RefundRequest,
    db: DbSession,
    actor: Actor,
    escrow: Escrow,
    notifier: Notices,
):
    """Ops: return the payment to the payer (e.g. a dispute settled for the family)."""
    refunded = await service.refund_escrow(
        db,
        actor,
        booking_id,
        data.reason,
        escrow_client=escrow,
        method=SettlementMethod.HUMAN,
        notifier=notifier,
    )
    return service.escrow_read(refunded)


@router.get(
    "/bookings/{booking_id}/ledger",
    response_model=list[LedgerEntryRead],
    dependencies=[require_roles(UserRole.OPS)],
)
async def get_ledger(booking_id: uuid.UUID, db: DbSession, actor: Actor):
    """Ops: every ledger posting for a booking, in order."""
    return await service.booking_ledger(db, actor, booking_id)


# ---------------------------------------------------------------- payments
@router.post("/payments/{payment_id}/verify", response_model=PaymentVerifyRead)
async def verify_payment(
    payment_id: uuid.UUID,
    db: DbSession,
    actor: Actor,
    gateways: Gateways,
    escrow: Escrow,
    notifier: Notices,
):
    """Ask the gateway now, e.g. when the payer returns from checkout. Safe to repeat."""
    return await service.verify_payment(
        db, actor, payment_id, gateways=gateways, escrow_client=escrow, notifier=notifier
    )


@router.post(
    "/payments/ops/settle",
    response_model=SettlementRead,
    dependencies=[require_roles(UserRole.OPS)],
)
async def run_settlement(db: DbSession, gateways: Gateways, escrow: Escrow, notifier: Notices):
    """Ops: run the settlement sweep now (Celery beat runs it every 10 minutes)."""
    result = await service.settle_escrows(
        db, gateways=gateways, escrow_client=escrow, notifier=notifier
    )
    return result.as_dict()


# ---------------------------------------------------------------- webhooks (no login)
@router.post("/payments/webhooks/paystack", response_model=WebhookAck)
async def paystack_webhook(
    request: Request,
    db: DbSession,
    gateways: Gateways,
    escrow: Escrow,
    notifier: Notices,
    x_paystack_signature: Annotated[str | None, Header()] = None,
):
    """Paystack events, signed with HMAC-SHA512 (x-paystack-signature)."""
    outcome = await service.handle_webhook(
        db,
        Gateway.PAYSTACK,
        await request.body(),
        x_paystack_signature,
        gateways=gateways,
        escrow_client=escrow,
        notifier=notifier,
    )
    return WebhookAck(status=outcome)


@router.post("/payments/webhooks/stripe", response_model=WebhookAck)
async def stripe_webhook(
    request: Request,
    db: DbSession,
    gateways: Gateways,
    escrow: Escrow,
    notifier: Notices,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
):
    """Stripe events, signed with the Stripe-Signature scheme (HMAC-SHA256, 5 min tolerance)."""
    outcome = await service.handle_webhook(
        db,
        Gateway.STRIPE,
        await request.body(),
        stripe_signature,
        gateways=gateways,
        escrow_client=escrow,
        notifier=notifier,
    )
    return WebhookAck(status=outcome)
