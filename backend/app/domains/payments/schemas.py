"""payments Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domains.payments.models import (
    EntryDirection,
    EscrowStatus,
    Gateway,
    LedgerAccount,
    PaymentStatus,
    SettlementMethod,
)

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class EscrowHoldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gateway: Gateway = Field(
        default=Gateway.PAYSTACK,
        description="paystack for local payments; stripe for family paying from abroad.",
    )


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    booking_id: uuid.UUID
    payer_id: uuid.UUID
    gateway: Gateway
    reference: str
    amount_minor: int
    currency: str
    status: PaymentStatus
    paid_at: datetime | None
    failure_reason: str | None
    needs_review: bool
    created_at: datetime


class CheckoutRead(BaseModel):
    """What the app needs to send the payer to the gateway."""

    payment: PaymentRead
    authorization_url: str | None = Field(
        description="Open this to pay. Null once the payment is no longer waiting."
    )
    escrow_status: EscrowStatus
    reused: bool = Field(description="True if an earlier checkout for this payer was handed back.")


class EscrowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    booking_id: uuid.UUID
    partner: str
    status: EscrowStatus
    amount_minor: int
    currency: str
    held_at: datetime | None
    released_at: datetime | None
    release_method: SettlementMethod | None
    released_by: uuid.UUID | None
    refunded_at: datetime | None
    refund_method: SettlementMethod | None
    refunded_by: uuid.UUID | None
    refund_reason: str | None


class BookingMoneyRead(BaseModel):
    """GET /bookings/{id}/escrow: the escrow and the payments made toward it."""

    booking_id: uuid.UUID
    booking_status: str
    escrow: EscrowRead | None
    payments: list[PaymentRead] = Field(
        description="Empty for the caregiver, who sees the escrow but not who paid."
    )


class PaymentVerifyRead(BaseModel):
    outcome: Literal[
        "pending", "failed", "funded", "needs_review", "already_processed", "funding_retry"
    ]
    payment: PaymentRead
    escrow_status: EscrowStatus | None


class RefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class LedgerEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    txn_group: uuid.UUID
    account: LedgerAccount
    direction: EntryDirection
    amount_minor: int
    currency: str
    memo: str
    payment_id: uuid.UUID | None
    escrow_id: uuid.UUID | None
    created_at: datetime


class WebhookAck(BaseModel):
    status: Literal["processed", "duplicate", "ignored"]


class SettlementRead(BaseModel):
    released: int = Field(description="Escrows released by rule after the hold period.")
    refunded: int = Field(description="Escrows of cancelled bookings returned to the payer.")
    closed_unfunded: int = Field(description="Never-funded escrows of cancelled bookings closed.")
    payments_reconciled: int = Field(description="Waiting payments checked with the gateway.")
    payments_abandoned: int = Field(description="Payments never completed within 48 hours.")
    partner_synced: int = Field(description="Escrows whose partner state was brought in line.")
    errors: int = Field(description="Items that failed and will be retried next run.")
