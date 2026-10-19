"""payments SQLAlchemy models: Payment, EscrowTransaction, LedgerEntry, WebhookEvent.

Schema follows docs/architecture/ERD.md section 2.6. CareKin never holds funds:
`escrow_transactions` mirrors the licensed partner's state, and every money movement
posts balanced debit and credit rows to `ledger_entries` (BK-6).

Database-level guarantees (also created by migration 0009):
- `ledger_entries` is append-only: a trigger refuses UPDATE and DELETE.
- Every `txn_group` balances: a deferred constraint trigger checks, at commit, that
  debits equal credits per currency. A bug that posts half an entry fails the commit.
- An escrow is released or refunded once, by a rule or a named human (never an agent).

Columns not in the ERD, and why:
- payments.reference: our own reference, sent to the gateway (Paystack keys by it).
  gateway_ref is the gateway's id (Stripe Checkout Session id, Paystack transaction id).
- payments.authorization_url: lets a retried "hold" hand back the same checkout.
- payments.needs_review / review_reason: money arrived that couldn't go into escrow
  (late, duplicate, wrong amount). Ops refunds it; an incident is opened too.
- escrow_transactions.partner_status: the partner's last known state. It can lag ours
  when the partner was down (a dispute must hold funds even then); the settlement
  sweep brings it back in line.
- escrow_transactions.funded_by_payment_id, refunded_at, refund_method, refunded_by,
  refund_reason: who funded and who refunded, like released_at / released_by.
- webhook_events.attempts / last_error: a failed delivery is retried by the gateway.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DDL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.integrations.escrow import EscrowStatus
from app.integrations.payments import Gateway, PaymentStatus

__all__ = [
    "EntryDirection",
    "EscrowStatus",
    "EscrowTransaction",
    "Gateway",
    "LedgerAccount",
    "LedgerEntry",
    "Payment",
    "PaymentStatus",
    "SettlementMethod",
    "WebhookEvent",
]


class SettlementMethod(StrEnum):
    """ERD `release_method`: a rule or a human. There is deliberately no agent value (BK-2)."""

    RULE = "rule"
    HUMAN = "human"


class LedgerAccount(StrEnum):
    FAMILY_FUNDS = "family_funds"  # money received from the family, not yet in escrow
    ESCROW = "escrow"  # held by the licensed partner
    CAREGIVER_PAYABLE = "caregiver_payable"  # released, owed to the caregiver
    PLATFORM_REVENUE = "platform_revenue"  # CareKin's commission
    GATEWAY_CLEARING = "gateway_clearing"  # in transit through Paystack or Stripe


class EntryDirection(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=_enum_values)


# Shared by more than one column, so each Postgres type is declared once.
GATEWAY_ENUM = _pg_enum(Gateway, "gateway")
ESCROW_STATUS_ENUM = _pg_enum(EscrowStatus, "escrow_status")
SETTLEMENT_METHOD_ENUM = _pg_enum(SettlementMethod, "release_method")


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="amount_positive"),)

    booking_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"), index=True
    )
    # Patient, guardian or family abroad.
    payer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    gateway: Mapped[Gateway] = mapped_column(GATEWAY_ENUM)
    reference: Mapped[str] = mapped_column(String(100), unique=True)
    gateway_ref: Mapped[str | None] = mapped_column(String(255), unique=True)
    # A hash of payer, booking and the client's Idempotency-Key (or a random value).
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[PaymentStatus] = mapped_column(
        _pg_enum(PaymentStatus, "payment_status"), index=True
    )
    authorization_url: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None]
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    review_reason: Mapped[str | None] = mapped_column(String(255))


class EscrowTransaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "escrow_transactions"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        # Released means released by a rule or a named human, with a time.
        CheckConstraint(
            "(status = 'released') = (released_at IS NOT NULL AND release_method IS NOT NULL)",
            name="released_fields",
        ),
        CheckConstraint(
            "release_method IS NULL OR (release_method = 'human') = (released_by IS NOT NULL)",
            name="release_human_named",
        ),
        CheckConstraint(
            "(status = 'refunded') = (refunded_at IS NOT NULL AND refund_method IS NOT NULL)",
            name="refunded_fields",
        ),
        CheckConstraint(
            "refund_method IS NULL OR (refund_method = 'human') = (refunded_by IS NOT NULL)",
            name="refund_human_named",
        ),
        # Funds are held exactly when a payment funded the escrow.
        CheckConstraint(
            "status = 'pending' OR held_at IS NOT NULL OR status = 'refunded'",
            name="held_has_time",
        ),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"), unique=True
    )
    partner: Mapped[str] = mapped_column(String(64))  # licensed partner, or "mock"
    partner_ref: Mapped[str] = mapped_column(String(255))
    status: Mapped[EscrowStatus] = mapped_column(ESCROW_STATUS_ENUM, index=True)
    partner_status: Mapped[EscrowStatus] = mapped_column(ESCROW_STATUS_ENUM)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    funded_by_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), unique=True
    )
    held_at: Mapped[datetime | None]
    released_at: Mapped[datetime | None]
    release_method: Mapped[SettlementMethod | None] = mapped_column(SETTLEMENT_METHOD_ENUM)
    released_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    refunded_at: Mapped[datetime | None]
    refund_method: Mapped[SettlementMethod | None] = mapped_column(SETTLEMENT_METHOD_ENUM)
    refunded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    refund_reason: Mapped[str | None] = mapped_column(String(500))


class LedgerEntry(Base):
    """One side of a double-entry posting. Append-only; rows in a txn_group balance."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        Index("ix_ledger_entries_account_currency", "account", "currency"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    txn_group: Mapped[uuid.UUID] = mapped_column(index=True)
    account: Mapped[LedgerAccount] = mapped_column(_pg_enum(LedgerAccount, "ledger_account"))
    direction: Mapped[EntryDirection] = mapped_column(_pg_enum(EntryDirection, "entry_direction"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    # What the posting is, e.g. payment.captured, escrow.funded, escrow.released.
    memo: Mapped[str] = mapped_column(String(64))
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"), index=True
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), index=True
    )
    escrow_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("escrow_transactions.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class WebhookEvent(UUIDPrimaryKeyMixin, Base):
    """Every webhook delivery we accepted. (gateway, event_id) is unique, so a replay is
    recognised and never applied twice."""

    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("gateway", "event_id"),)

    gateway: Mapped[Gateway] = mapped_column(GATEWAY_ENUM)
    event_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)  # redacted: ids, status, amount
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), index=True
    )
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
    processed_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(500))


# ---------------------------------------------------------------- database guarantees
# The same statements run in migration 0009; these listeners give the test schema
# (built with create_all) the same protection. One statement each: asyncpg prepares
# statements and refuses several in one.
LEDGER_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION ledger_entries_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is append-only' USING ERRCODE = 'check_violation';
END;
$$
""",
    """
CREATE OR REPLACE FUNCTION ledger_entries_check_balance() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM ledger_entries
        WHERE txn_group = NEW.txn_group
        GROUP BY currency
        HAVING SUM(CASE WHEN direction = 'debit' THEN amount_minor ELSE -amount_minor END) <> 0
    ) THEN
        RAISE EXCEPTION 'ledger txn_group % does not balance', NEW.txn_group
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$
""",
    """
CREATE TRIGGER ledger_entries_no_update_or_delete
    BEFORE UPDATE OR DELETE ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION ledger_entries_append_only()
""",
    """
CREATE CONSTRAINT TRIGGER ledger_entries_balanced
    AFTER INSERT ON ledger_entries
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_entries_check_balance()
""",
)

LEDGER_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS ledger_entries_balanced ON ledger_entries",
    "DROP TRIGGER IF EXISTS ledger_entries_no_update_or_delete ON ledger_entries",
    "DROP FUNCTION IF EXISTS ledger_entries_check_balance()",
    "DROP FUNCTION IF EXISTS ledger_entries_append_only()",
)

for _statement in LEDGER_DDL:
    # DDL() formats with %, so a literal % (the RAISE placeholder) is doubled.
    event.listen(LedgerEntry.__table__, "after_create", DDL(_statement.replace("%", "%%")))
for _statement in LEDGER_DROP_DDL:
    event.listen(LedgerEntry.__table__, "before_drop", DDL(_statement))
