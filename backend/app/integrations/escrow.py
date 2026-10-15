"""Licensed escrow partner client. CareKin never holds customer funds directly.

The partner is open decision Topic 3. Until it is chosen, only the mock exists.

Lifecycle (one escrow per booking, Day 11). Statuses are the ERD's `escrow_status`:

    pending --payment lands--> held --release--> released
       |                         |--refund--> refunded
       |                         \\--open_dispute--> on_hold_dispute
       \\--refund (never paid)--> refunded
                                     on_hold_dispute --close_dispute--> held
                                     on_hold_dispute --refund--> refunded

While a dispute is open funds can't be released (PRD TS-5). Every call is idempotent
by `reference` / `escrow_ref`: repeating a create, release or refund that already
happened returns the current state instead of failing, so callers can retry safely.
Amounts are integers in minor units (kobo for NGN).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from app.config import get_settings
from app.integrations.base import IntegrationError, not_configured


class EscrowStatus(StrEnum):
    """Same values as the ERD's `escrow_status` enum."""

    PENDING = "pending"
    HELD = "held"
    ON_HOLD_DISPUTE = "on_hold_dispute"
    RELEASED = "released"
    REFUNDED = "refunded"


class EscrowStateError(IntegrationError):
    """The requested move isn't allowed from the escrow's current status."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(provider, message, retryable=False)


@dataclass(frozen=True, slots=True)
class EscrowRecord:
    escrow_ref: str
    reference: str  # ours, e.g. the booking id
    amount_minor: int
    currency: str
    status: EscrowStatus
    updated_at: datetime


class EscrowClient(Protocol):
    async def create(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        payer_ref: str,
        payee_ref: str,
        description: str,
    ) -> EscrowRecord: ...

    async def get(self, escrow_ref: str) -> EscrowRecord: ...

    async def release(self, escrow_ref: str) -> EscrowRecord:
        """Pay the caregiver. Only from `held`."""
        ...

    async def refund(self, escrow_ref: str, *, reason: str) -> EscrowRecord:
        """Return funds to the payer (or close an escrow that was never paid)."""
        ...

    async def open_dispute(self, escrow_ref: str, *, reason: str) -> EscrowRecord: ...

    async def close_dispute(self, escrow_ref: str) -> EscrowRecord: ...


_ALLOWED: dict[str, dict[EscrowStatus, EscrowStatus]] = {
    # action: {from status: to status}
    "fund": {EscrowStatus.PENDING: EscrowStatus.HELD},
    "release": {EscrowStatus.HELD: EscrowStatus.RELEASED},
    "refund": {
        EscrowStatus.PENDING: EscrowStatus.REFUNDED,
        EscrowStatus.HELD: EscrowStatus.REFUNDED,
        EscrowStatus.ON_HOLD_DISPUTE: EscrowStatus.REFUNDED,
    },
    "open_dispute": {EscrowStatus.HELD: EscrowStatus.ON_HOLD_DISPUTE},
    "close_dispute": {EscrowStatus.ON_HOLD_DISPUTE: EscrowStatus.HELD},
}
# Repeating an action that already happened is a no-op, not an error.
_ALREADY_DONE: dict[str, set[EscrowStatus]] = {
    "fund": {EscrowStatus.HELD},
    "release": {EscrowStatus.RELEASED},
    "refund": {EscrowStatus.REFUNDED},
    "open_dispute": {EscrowStatus.ON_HOLD_DISPUTE},
    "close_dispute": set(),
}


class MockEscrowClient:
    """In-memory partner for development and tests. State lives in this process."""

    provider = "mock"

    def __init__(self) -> None:
        self._records: dict[str, EscrowRecord] = {}

    async def create(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        payer_ref: str,
        payee_ref: str,
        description: str,
    ) -> EscrowRecord:
        if amount_minor <= 0:
            raise EscrowStateError(self.provider, "amount must be positive")
        escrow_ref = f"mock-escrow-{reference}"
        existing = self._records.get(escrow_ref)
        if existing is not None:
            if (existing.amount_minor, existing.currency) != (amount_minor, currency.upper()):
                raise EscrowStateError(self.provider, "reference reused with a different amount")
            return existing
        record = EscrowRecord(
            escrow_ref=escrow_ref,
            reference=reference,
            amount_minor=amount_minor,
            currency=currency.upper(),
            status=EscrowStatus.PENDING,
            updated_at=datetime.now(UTC),
        )
        self._records[escrow_ref] = record
        return record

    async def get(self, escrow_ref: str) -> EscrowRecord:
        try:
            return self._records[escrow_ref]
        except KeyError:
            raise IntegrationError(self.provider, "unknown escrow", retryable=False) from None

    def _move(self, escrow_ref: str, action: str) -> EscrowRecord:
        record = self._records.get(escrow_ref)
        if record is None:
            raise IntegrationError(self.provider, "unknown escrow", retryable=False)
        if record.status in _ALREADY_DONE[action]:
            return record
        target = _ALLOWED[action].get(record.status)
        if target is None:
            raise EscrowStateError(
                self.provider, f"can't {action} an escrow that is {record.status}"
            )
        record = replace(record, status=target, updated_at=datetime.now(UTC))
        self._records[escrow_ref] = record
        return record

    def simulate_funding(self, escrow_ref: str) -> EscrowRecord:
        """Development/test hook: what the partner does when the payment lands."""
        return self._move(escrow_ref, "fund")

    async def release(self, escrow_ref: str) -> EscrowRecord:
        return self._move(escrow_ref, "release")

    async def refund(self, escrow_ref: str, *, reason: str) -> EscrowRecord:
        return self._move(escrow_ref, "refund")

    async def open_dispute(self, escrow_ref: str, *, reason: str) -> EscrowRecord:
        return self._move(escrow_ref, "open_dispute")

    async def close_dispute(self, escrow_ref: str) -> EscrowRecord:
        return self._move(escrow_ref, "close_dispute")


_mock_client = MockEscrowClient()


def get_escrow_client() -> EscrowClient:
    """FastAPI dependency."""
    if get_settings().USE_MOCK_INTEGRATIONS:
        return _mock_client
    raise not_configured(
        "escrow", "No licensed escrow partner is chosen yet (open decision Topic 3)."
    )
