"""Payment gateways: Paystack for naira, Stripe for family abroad (BK-5).

Payments fund the licensed escrow partner; CareKin never holds the money (ground rule).
The checkout flow (Day 11, app/domains/payments):

    checkout = await client.initialize(reference=..., amount_minor=..., ...)
    -> send the payer to checkout.authorization_url
    -> the gateway calls our webhook; check verify_webhook(), then verify(reference)
       (never trust the webhook body or the redirect alone)

Amounts are integers in minor units (kobo). Paystack needs an email address for every
transaction; pass the user's, or a stable placeholder if they have none.

References are ours (letters, digits, '-', '.', '='), unique per payment. Paystack keys
transactions by our reference. Stripe keys Checkout Sessions by its own id, so
`Checkout.provider_ref` carries the session id and `verify()` takes it back.

Mock rules (both gateways):
- every initialized payment verifies as `succeeded`, except references that contain
  "fail" (verify as `failed`);
- tests can force an outcome per reference with `set_outcome()`, or a whole-gateway
  outage with `unavailable = True`;
- webhooks are signed exactly like the real gateway's (MOCK_WEBHOOK_SECRET for the
  Paystack style, MOCK_STRIPE_WEBHOOK_SECRET for Stripe's), so the webhook routes run
  the real verification code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from app.config import Settings, get_settings
from app.integrations.base import IntegrationError, not_configured, request_json

MOCK_WEBHOOK_SECRET = "mock-paystack-secret"
MOCK_STRIPE_WEBHOOK_SECRET = "whsec_mock_stripe_secret"
STRIPE_BASE_URL = "https://api.stripe.com"
# Stripe's default: a signed webhook older (or newer) than this is refused as a replay.
STRIPE_SIGNATURE_TOLERANCE_SECONDS = 300
_REFERENCE = re.compile(r"^[A-Za-z0-9.=-]{1,100}$")  # Paystack's allowed characters
_STRIPE_ID = re.compile(r"^[A-Za-z0-9_]{1,255}$")


class Gateway(StrEnum):
    """Same values as the ERD's `gateway` enum."""

    PAYSTACK = "paystack"
    STRIPE = "stripe"


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
    # The gateway's own id for this checkout, when it has one (Stripe session id).
    provider_ref: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentVerification:
    provider: str
    reference: str
    status: PaymentStatus
    amount_minor: int
    currency: str
    paid_at: datetime | None = None
    provider_ref: str | None = None


@dataclass(frozen=True, slots=True)
class WebhookNotice:
    """What a webhook says, after its signature was checked. Still not trusted: the
    service always calls verify() before money moves.

    `payload` is a redacted copy (ids, status, amount): no email, phone or card data.
    `reference` is None for events that aren't about a checkout we created.
    """

    event_id: str
    event_type: str
    reference: str | None
    provider_ref: str | None
    payload: dict[str, Any]


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

    async def verify(
        self, reference: str, *, provider_ref: str | None = None
    ) -> PaymentVerification:
        """Ask the gateway what happened. The only source of truth for a payment."""
        ...

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        """True if the webhook really came from the gateway."""
        ...

    def parse_webhook(self, body: bytes) -> WebhookNotice:
        """Read a webhook body. Raises ValueError if it isn't one."""
        ...


def _check(reference: str, amount_minor: int) -> None:
    if not _REFERENCE.match(reference):
        raise ValueError("Payment references may use letters, digits, '-', '.' and '=' only")
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor <= 0:
        raise ValueError("Payment amount must be positive")


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Webhook body is not JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Webhook body is not a JSON object")  # noqa: TRY004 - callers catch ValueError
    return data


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


# ---------------------------------------------------------------- Paystack
def paystack_signature(secret: str, body: bytes) -> str:
    """HMAC-SHA512 of the raw body, as Paystack sends in x-paystack-signature."""
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


def _signature_matches(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(paystack_signature(secret, body), signature)


def parse_paystack_webhook(body: bytes) -> WebhookNotice:
    data = _json_object(body)
    event = data.get("event")
    inner = data.get("data")
    if not isinstance(event, str) or not event or not isinstance(inner, dict):
        raise ValueError("Not a Paystack event")
    reference = inner.get("reference")
    reference = reference if isinstance(reference, str) and _REFERENCE.match(reference) else None
    txn_id = inner.get("id")
    txn_id = str(txn_id) if isinstance(txn_id, int | str) and str(txn_id) else None
    # Paystack events carry no event id. One transaction gets one charge.success, so the
    # event name plus the transaction id (or our reference) identifies a delivery.
    key = txn_id or reference
    if key is None:
        raise ValueError("Paystack event has neither a transaction id nor a reference")
    return WebhookNotice(
        event_id=f"{event}:{key}"[:255],
        event_type=event[:100],
        reference=reference if event.startswith("charge.") else None,
        provider_ref=txn_id,
        payload={
            "event": event,
            "data": {
                "id": txn_id,
                "reference": reference,
                "status": inner.get("status") if isinstance(inner.get("status"), str) else None,
                "amount": _int_or_none(inner.get("amount")),
                "currency": inner.get("currency")
                if isinstance(inner.get("currency"), str)
                else None,
            },
        },
    )


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

    async def verify(
        self, reference: str, *, provider_ref: str | None = None
    ) -> PaymentVerification:
        if not _REFERENCE.match(reference):
            raise ValueError("Invalid payment reference")
        data = await self._call("GET", f"/transaction/verify/{quote(reference, safe='')}")
        paid_at = data.get("paid_at") or data.get("paidAt")
        amount = _int_or_none(data.get("amount"))
        return PaymentVerification(
            provider=self.provider,
            reference=data.get("reference") or reference,
            status=_PAYSTACK_STATUS.get(str(data.get("status")), PaymentStatus.INITIATED),
            amount_minor=amount if amount is not None else 0,
            currency=str(data.get("currency") or "NGN").upper(),
            paid_at=datetime.fromisoformat(paid_at) if paid_at else None,
            provider_ref=str(data["id"]) if data.get("id") is not None else None,
        )

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        return _signature_matches(self._secret, body, signature)

    def parse_webhook(self, body: bytes) -> WebhookNotice:
        return parse_paystack_webhook(body)


# ---------------------------------------------------------------- Stripe
def stripe_signature(secret: str, body: bytes, timestamp: int | None = None) -> str:
    """A Stripe-Signature header value for `body`: t=<unix time>,v1=<HMAC-SHA256>."""
    ts = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def stripe_signature_matches(
    secret: str,
    body: bytes,
    header: str | None,
    *,
    tolerance: int = STRIPE_SIGNATURE_TOLERANCE_SECONDS,
    now: float | None = None,
) -> bool:
    """Stripe's scheme: HMAC-SHA256 of "<t>.<raw body>", any v1 may match, t within tolerance."""
    if not header:
        return False
    timestamp: int | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1" and value:
            candidates.append(value)
    if timestamp is None or not candidates:
        return False
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in candidates)


STRIPE_CHECKOUT_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }
)


def parse_stripe_webhook(body: bytes) -> WebhookNotice:
    data = _json_object(body)
    event_id = data.get("id")
    event_type = data.get("type")
    if not isinstance(event_id, str) or not event_id or not isinstance(event_type, str):
        raise ValueError("Not a Stripe event")
    obj = (data.get("data") or {}).get("object") if isinstance(data.get("data"), dict) else None
    obj = obj if isinstance(obj, dict) else {}
    reference = obj.get("client_reference_id")
    if not isinstance(reference, str):
        metadata = obj.get("metadata")
        reference = metadata.get("reference") if isinstance(metadata, dict) else None
    reference = reference if isinstance(reference, str) and _REFERENCE.match(reference) else None
    session_id = obj.get("id") if isinstance(obj.get("id"), str) else None
    relevant = event_type in STRIPE_CHECKOUT_EVENTS
    return WebhookNotice(
        event_id=event_id[:255],
        event_type=event_type[:100],
        reference=reference if relevant else None,
        provider_ref=session_id if relevant else None,
        payload={
            "id": event_id,
            "type": event_type,
            "object": {
                "id": session_id,
                "client_reference_id": reference,
                "payment_status": obj.get("payment_status")
                if isinstance(obj.get("payment_status"), str)
                else None,
                "status": obj.get("status") if isinstance(obj.get("status"), str) else None,
                "amount_total": _int_or_none(obj.get("amount_total")),
                "currency": obj.get("currency") if isinstance(obj.get("currency"), str) else None,
            },
        },
    )


def _stripe_status(session: dict[str, Any]) -> PaymentStatus:
    if session.get("payment_status") == "paid":
        return PaymentStatus.SUCCEEDED
    if session.get("status") == "expired":
        return PaymentStatus.FAILED
    return PaymentStatus.INITIATED  # open, or complete but an async payment is pending


class StripeClient:
    """Stripe Checkout Sessions, for families paying from abroad by card."""

    provider = "stripe"

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = STRIPE_BASE_URL,
    ):
        if settings.STRIPE_SECRET_KEY is None or settings.STRIPE_WEBHOOK_SECRET is None:
            raise IntegrationError(
                self.provider,
                "STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET must both be set",
                retryable=False,
            )
        self._secret = settings.STRIPE_SECRET_KEY.get_secret_value()
        self._webhook_secret = settings.STRIPE_WEBHOOK_SECRET.get_secret_value()
        self._base = base_url.rstrip("/")
        self._timeout = settings.INTEGRATION_TIMEOUT_SECONDS
        self._transport = transport

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._secret}"}
        headers.update(kwargs.pop("headers", {}))
        return await request_json(
            self.provider,
            method,
            self._base + path,
            timeout=self._timeout,
            transport=self._transport,
            headers=headers,
            **kwargs,
        )

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
        if not callback_url:
            raise ValueError("Stripe Checkout needs a callback_url to return the payer to")
        form: dict[str, Any] = {
            "mode": "payment",
            "client_reference_id": reference,
            "success_url": callback_url,
            "cancel_url": callback_url,
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": currency.lower(),
            "line_items[0][price_data][unit_amount]": amount_minor,
            "line_items[0][price_data][product_data][name]": "CareKin care booking",
            "metadata[reference]": reference,
            "payment_intent_data[metadata][reference]": reference,
        }
        if email:
            form["customer_email"] = email
        for key, value in (metadata or {}).items():
            form[f"metadata[{key}]"] = str(value)
        # The same reference always returns the same session, so a retried call can't
        # open a second checkout for one payment.
        data = await self._call(
            "POST",
            "/v1/checkout/sessions",
            data=form,
            headers={"Idempotency-Key": f"carekin-{reference}"},
        )
        session_id, url = data.get("id"), data.get("url")
        if not isinstance(session_id, str) or not isinstance(url, str) or not url:
            raise IntegrationError(self.provider, "no checkout session in the response")
        return Checkout(
            provider=self.provider,
            reference=reference,
            authorization_url=url,
            provider_ref=session_id,
        )

    async def verify(
        self, reference: str, *, provider_ref: str | None = None
    ) -> PaymentVerification:
        if not provider_ref or not _STRIPE_ID.match(provider_ref):
            raise IntegrationError(
                self.provider, "no checkout session id for this payment", retryable=False
            )
        data = await self._call("GET", f"/v1/checkout/sessions/{provider_ref}")
        if data.get("client_reference_id") != reference:
            raise IntegrationError(
                self.provider, "checkout session belongs to another payment", retryable=False
            )
        amount = _int_or_none(data.get("amount_total"))
        status = _stripe_status(data)
        intent = data.get("payment_intent")
        return PaymentVerification(
            provider=self.provider,
            reference=reference,
            status=status,
            amount_minor=amount if amount is not None else 0,
            currency=str(data.get("currency") or "").upper(),
            paid_at=datetime.now(UTC) if status is PaymentStatus.SUCCEEDED else None,
            provider_ref=intent if isinstance(intent, str) else provider_ref,
        )

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        return stripe_signature_matches(self._webhook_secret, body, signature)

    def parse_webhook(self, body: bytes) -> WebhookNotice:
        return parse_stripe_webhook(body)


# ---------------------------------------------------------------- mocks
@dataclass
class _Outcome:
    status: PaymentStatus
    amount_minor: int | None = None
    currency: str | None = None


@dataclass
class MockPaymentClient:
    """Paystack-style mock: our reference is the key, webhooks are signed like Paystack's."""

    provider: str = "mock"
    initialized: dict[str, Checkout] = field(default_factory=dict)
    amounts: dict[str, tuple[int, str]] = field(default_factory=dict)
    outcomes: dict[str, _Outcome] = field(default_factory=dict)
    # Test hook: while True every call fails as a gateway outage would.
    unavailable: bool = False

    def _check_up(self) -> None:
        if self.unavailable:
            raise IntegrationError(self.provider, "gateway unavailable (simulated)")

    def set_outcome(
        self,
        reference: str,
        status: PaymentStatus,
        *,
        amount_minor: int | None = None,
        currency: str | None = None,
    ) -> None:
        """Test hook: what verify() reports for `reference` from now on."""
        self.outcomes[reference] = _Outcome(status, amount_minor, currency)

    def _checkout(self, reference: str) -> Checkout:
        return Checkout(
            provider=self.provider,
            reference=reference,
            authorization_url=f"https://checkout.mock.carekin.invalid/{reference}",
            access_code=f"mock-{reference}",
        )

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
        self._check_up()
        _check(reference, amount_minor)
        checkout = self._checkout(reference)
        self.initialized[reference] = checkout
        self.amounts[reference] = (amount_minor, currency.upper())
        return checkout

    def _verification(self, reference: str, provider_ref: str | None) -> PaymentVerification:
        if reference not in self.amounts:
            raise IntegrationError(self.provider, "unknown reference", retryable=False)
        amount, currency = self.amounts[reference]
        outcome = self.outcomes.get(reference)
        if outcome is not None:
            status = outcome.status
            amount = outcome.amount_minor if outcome.amount_minor is not None else amount
            currency = outcome.currency or currency
        else:
            status = PaymentStatus.FAILED if "fail" in reference else PaymentStatus.SUCCEEDED
        return PaymentVerification(
            provider=self.provider,
            reference=reference,
            status=status,
            amount_minor=amount,
            currency=currency,
            paid_at=datetime.now(UTC) if status is PaymentStatus.SUCCEEDED else None,
            provider_ref=provider_ref or f"mock-txn-{reference}",
        )

    async def verify(
        self, reference: str, *, provider_ref: str | None = None
    ) -> PaymentVerification:
        self._check_up()
        return self._verification(reference, provider_ref)

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        return _signature_matches(MOCK_WEBHOOK_SECRET, body, signature)

    def parse_webhook(self, body: bytes) -> WebhookNotice:
        return parse_paystack_webhook(body)


@dataclass
class MockStripeClient(MockPaymentClient):
    """Stripe-style mock: a session id per checkout, webhooks signed like Stripe's."""

    provider: str = "mock-stripe"

    def _checkout(self, reference: str) -> Checkout:
        session_id = "cs_mock_" + hashlib.sha256(reference.encode()).hexdigest()[:24]
        return Checkout(
            provider=self.provider,
            reference=reference,
            authorization_url=f"https://checkout.mock.carekin.invalid/stripe/{session_id}",
            provider_ref=session_id,
        )

    async def verify(
        self, reference: str, *, provider_ref: str | None = None
    ) -> PaymentVerification:
        self._check_up()
        checkout = self.initialized.get(reference)
        if checkout is None or provider_ref != checkout.provider_ref:
            raise IntegrationError(
                self.provider, "checkout session belongs to another payment", retryable=False
            )
        return self._verification(reference, provider_ref)

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        return stripe_signature_matches(MOCK_STRIPE_WEBHOOK_SECRET, body, signature)

    def parse_webhook(self, body: bytes) -> WebhookNotice:
        return parse_stripe_webhook(body)


_mock_client = MockPaymentClient()
_mock_stripe_client = MockStripeClient()


def get_gateway_client(gateway: Gateway | str) -> PaymentClient:
    """The client for one gateway: the mock, the real one, or 503 if it isn't set up."""
    gateway = Gateway(gateway)
    settings = get_settings()
    if settings.USE_MOCK_INTEGRATIONS:
        return _mock_stripe_client if gateway is Gateway.STRIPE else _mock_client
    if gateway is Gateway.PAYSTACK:
        if settings.PAYSTACK_SECRET_KEY is None:
            raise not_configured("payments", "Paystack needs PAYSTACK_SECRET_KEY.")
        return PaystackClient(settings)
    if settings.STRIPE_SECRET_KEY is None or settings.STRIPE_WEBHOOK_SECRET is None:
        raise not_configured(
            "payments", "Stripe needs STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET."
        )
    return StripeClient(settings)


def get_payment_client(currency: str = "NGN") -> PaymentClient:
    """The gateway for a currency: Paystack for NGN, Stripe for anything else."""
    gateway = Gateway.PAYSTACK if currency.upper() == "NGN" else Gateway.STRIPE
    return get_gateway_client(gateway)
