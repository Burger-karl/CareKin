"""Payment gateways: Paystack for naira, Stripe for foreign currency (family abroad).

Payments fund the licensed escrow partner; CareKin never holds the money (ground rule).
The checkout flow (Day 11):

    checkout = await payments.initialize(reference=..., amount_minor=..., ...)
    -> send the user to checkout.authorization_url
    -> Paystack calls our webhook; check verify_webhook(), then verify(reference)
       (never trust the webhook body or the redirect alone)

Amounts are integers in minor units (kobo). Paystack needs an email address for every
transaction; pass the user's, or a stable placeholder on our own domain if they have none.

Mock rules: every initialized payment verifies as `succeeded`, except references that
contain "fail" (verify as `failed`). Webhooks are signed exactly like Paystack's, with
MOCK_WEBHOOK_SECRET, so the webhook route is tested on the real code path.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from app.config import Settings, get_settings
from app.integrations.base import IntegrationError, not_configured, request_json

MOCK_WEBHOOK_SECRET = "mock-paystack-secret"
_REFERENCE = re.compile(r"^[A-Za-z0-9.=-]{1,100}$")  # Paystack's allowed characters


class PaymentStatus(StrEnum):
    """Same values as the ERD's `payment_status` enum."""

    INITIATED = "initiated"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"


@dataclass(frozen=True, slots=True)
class Checkout:
    provider: str
    reference: str
    authorization_url: str
    access_code: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentVerification:
    provider: str
    reference: str
    status: PaymentStatus
    amount_minor: int
    currency: str
    paid_at: datetime | None = None
    provider_ref: str | None = None


class PaymentClient(Protocol):
    provider: str

    async def initialize(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        email: str,
        callback_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Checkout: ...

    async def verify(self, reference: str) -> PaymentVerification:
        """Ask the gateway what happened. The only source of truth for a payment."""
        ...

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        """True if the webhook really came from the gateway."""
        ...


def _check(reference: str, amount_minor: int) -> None:
    if not _REFERENCE.match(reference):
        raise ValueError("Payment references may use letters, digits, '-', '.' and '=' only")
    if amount_minor <= 0:
        raise ValueError("Payment amount must be positive")


def paystack_signature(secret: str, body: bytes) -> str:
    """HMAC-SHA512 of the raw body, as Paystack sends in x-paystack-signature."""
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


def _signature_matches(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(paystack_signature(secret, body), signature)


# Paystack transaction statuses -> ours. Anything else ("ongoing", "pending",
# "abandoned": the payer may still come back) is still in progress.
_PAYSTACK_STATUS = {
    "success": PaymentStatus.SUCCEEDED,
    "failed": PaymentStatus.FAILED,
    "reversed": PaymentStatus.REFUNDED,
}


class PaystackClient:
    """Paystack Transactions API."""

    provider = "paystack"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        if settings.PAYSTACK_SECRET_KEY is None:
            raise IntegrationError(self.provider, "PAYSTACK_SECRET_KEY is not set", retryable=False)
        self._secret = settings.PAYSTACK_SECRET_KEY.get_secret_value()
        self._base = settings.PAYSTACK_BASE_URL.rstrip("/")
        self._timeout = settings.INTEGRATION_TIMEOUT_SECONDS
        self._transport = transport

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        data = await request_json(
            self.provider,
            method,
            self._base + path,
            timeout=self._timeout,
            transport=self._transport,
            headers={"Authorization": f"Bearer {self._secret}"},
            **kwargs,
        )
        if data.get("status") is not True or not isinstance(data.get("data"), dict):
            raise IntegrationError(
                self.provider, data.get("message") or "request refused", retryable=False
            )
        return data["data"]

    async def initialize(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        email: str,
        callback_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Checkout:
        _check(reference, amount_minor)
        payload: dict[str, Any] = {
            "email": email,
            "amount": amount_minor,
            "currency": currency.upper(),
            "reference": reference,
        }
        if callback_url:
            payload["callback_url"] = callback_url
        if metadata:
            payload["metadata"] = metadata
        data = await self._call("POST", "/transaction/initialize", json=payload)
        if not data.get("authorization_url"):
            raise IntegrationError(self.provider, "no authorization_url in the response")
        return Checkout(
            provider=self.provider,
            reference=data.get("reference") or reference,
            authorization_url=data["authorization_url"],
            access_code=data.get("access_code"),
        )

    async def verify(self, reference: str) -> PaymentVerification:
        if not _REFERENCE.match(reference):
            raise ValueError("Invalid payment reference")
        data = await self._call("GET", f"/transaction/verify/{quote(reference, safe='')}")
        paid_at = data.get("paid_at") or data.get("paidAt")
        return PaymentVerification(
            provider=self.provider,
            reference=data.get("reference") or reference,
            status=_PAYSTACK_STATUS.get(str(data.get("status")), PaymentStatus.INITIATED),
            amount_minor=int(data.get("amount") or 0),
            currency=str(data.get("currency") or "NGN"),
            paid_at=datetime.fromisoformat(paid_at) if paid_at else None,
            provider_ref=str(data["id"]) if data.get("id") is not None else None,
        )

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        return _signature_matches(self._secret, body, signature)


@dataclass
class MockPaymentClient:
    provider: str = "mock"
    initialized: dict[str, Checkout] = field(default_factory=dict)
    amounts: dict[str, tuple[int, str]] = field(default_factory=dict)

    async def initialize(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        email: str,
        callback_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Checkout:
        _check(reference, amount_minor)
        checkout = Checkout(
            provider=self.provider,
            reference=reference,
            authorization_url=f"https://checkout.mock.carekin.invalid/{reference}",
            access_code=f"mock-{reference}",
        )
        self.initialized[reference] = checkout
        self.amounts[reference] = (amount_minor, currency.upper())
        return checkout

    async def verify(self, reference: str) -> PaymentVerification:
        if reference not in self.amounts:
            raise IntegrationError(self.provider, "unknown reference", retryable=False)
        amount, currency = self.amounts[reference]
        failed = "fail" in reference
        return PaymentVerification(
            provider=self.provider,
            reference=reference,
            status=PaymentStatus.FAILED if failed else PaymentStatus.SUCCEEDED,
            amount_minor=amount,
            currency=currency,
            paid_at=None if failed else datetime.now(UTC),
            provider_ref=f"mock-txn-{reference}",
        )

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        return _signature_matches(MOCK_WEBHOOK_SECRET, body, signature)


_mock_client = MockPaymentClient()


def get_payment_client(currency: str = "NGN") -> PaymentClient:
    """The gateway for a currency: Paystack for NGN, Stripe (not built yet) for others."""
    settings = get_settings()
    if settings.USE_MOCK_INTEGRATIONS:
        return _mock_client
    if currency.upper() == "NGN":
        if settings.PAYSTACK_SECRET_KEY is None:
            raise not_configured("payments", "Paystack needs PAYSTACK_SECRET_KEY.")
        return PaystackClient(settings)
    raise not_configured(
        "payments", "Foreign-currency payments (Stripe) are not built yet (Day 11)."
    )
