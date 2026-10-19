"""Day 11 gateway and escrow clients: Stripe (built today), webhook parsing and
signatures, the gateway factory, and the escrow partner's new fund() call. No network:
real clients run against httpx.MockTransport."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.core.exceptions import ServiceUnavailableError
from app.integrations import payments
from app.integrations.base import IntegrationError
from app.integrations.escrow import EscrowStateError, EscrowStatus, MockEscrowClient
from app.integrations.payments import (
    Gateway,
    MockPaymentClient,
    MockStripeClient,
    PaymentStatus,
    PaystackClient,
    StripeClient,
    parse_paystack_webhook,
    parse_stripe_webhook,
    stripe_signature,
    stripe_signature_matches,
)


def stripe_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "STRIPE_SECRET_KEY": "sk_test_stripe",
        "STRIPE_WEBHOOK_SECRET": "whsec_test",
        **overrides,
    }
    return Settings(**values)


def transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ------------------------------------------------------------------ Stripe signatures
def test_stripe_signature_round_trip():
    body = b'{"id":"evt_1"}'
    header = stripe_signature("whsec_x", body)
    assert stripe_signature_matches("whsec_x", body, header)
    assert not stripe_signature_matches("whsec_y", body, header)
    assert not stripe_signature_matches("whsec_x", body + b" ", header)


def test_stripe_signature_accepts_any_v1_and_checks_the_window():
    body = b"{}"
    now = 1_800_000_000
    good = stripe_signature("whsec_x", body, now).split("v1=")[1]
    header = f"t={now},v1=deadbeef,v1={good},v0=ignored"
    assert stripe_signature_matches("whsec_x", body, header, now=now + 299)
    assert not stripe_signature_matches("whsec_x", body, header, now=now + 301)
    assert not stripe_signature_matches("whsec_x", body, header, now=now - 301)


@pytest.mark.parametrize(
    "header", [None, "", "garbage", "t=abc,v1=00", "v1=00", f"t={int(time.time())}"]
)
def test_stripe_signature_malformed_headers(header):
    assert not stripe_signature_matches("whsec_x", b"{}", header)


# ------------------------------------------------------------------ Stripe client
async def test_stripe_initialize_sends_a_checkout_session():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["idem"] = request.headers.get("idempotency-key")
        seen["form"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(
            200, json={"id": "cs_test_123", "url": "https://checkout.stripe.com/c/cs_test_123"}
        )

    client = StripeClient(stripe_settings(), transport=transport(handler))
    checkout = await client.initialize(
        reference="CK-ABC",
        amount_minor=600_000,
        currency="NGN",
        email="ada@example.com",
        callback_url="https://app.carekin.ng/return?booking=1",
        metadata={"booking_id": "b-1"},
    )
    assert checkout.provider_ref == "cs_test_123"
    assert checkout.authorization_url.startswith("https://checkout.stripe.com/")
    assert seen["url"] == "https://api.stripe.com/v1/checkout/sessions"
    assert seen["auth"] == "Bearer sk_test_stripe"
    assert seen["idem"] == "carekin-CK-ABC"
    form = seen["form"]
    assert form["mode"] == "payment" and form["client_reference_id"] == "CK-ABC"
    assert form["line_items[0][price_data][currency]"] == "ngn"
    assert form["line_items[0][price_data][unit_amount]"] == "600000"
    assert form["metadata[reference]"] == "CK-ABC" and form["metadata[booking_id]"] == "b-1"
    assert form["customer_email"] == "ada@example.com"


async def test_stripe_initialize_needs_a_return_url_and_good_input():
    client = StripeClient(stripe_settings(), transport=transport(lambda r: httpx.Response(500)))
    with pytest.raises(ValueError, match="callback_url"):
        await client.initialize(reference="CK-1", amount_minor=1, currency="NGN", email="")
    with pytest.raises(ValueError, match="positive"):
        await client.initialize(
            reference="CK-1", amount_minor=0, currency="NGN", email="", callback_url="https://x"
        )
    with pytest.raises(ValueError, match="references"):
        await client.initialize(
            reference="bad ref", amount_minor=5, currency="NGN", email="", callback_url="https://x"
        )


@pytest.mark.parametrize(
    ("session", "status"),
    [
        ({"payment_status": "paid", "status": "complete"}, PaymentStatus.SUCCEEDED),
        ({"payment_status": "unpaid", "status": "open"}, PaymentStatus.INITIATED),
        ({"payment_status": "unpaid", "status": "complete"}, PaymentStatus.INITIATED),
        ({"payment_status": "unpaid", "status": "expired"}, PaymentStatus.FAILED),
    ],
)
async def test_stripe_verify_maps_session_states(session, status):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/checkout/sessions/cs_1"
        return httpx.Response(
            200,
            json={
                "id": "cs_1",
                "client_reference_id": "CK-1",
                "amount_total": 600_000,
                "currency": "ngn",
                "payment_intent": "pi_1",
                **session,
            },
        )

    client = StripeClient(stripe_settings(), transport=transport(handler))
    result = await client.verify("CK-1", provider_ref="cs_1")
    assert result.status is status
    assert result.amount_minor == 600_000 and result.currency == "NGN"
    assert (result.paid_at is not None) == (status is PaymentStatus.SUCCEEDED)


async def test_stripe_verify_refuses_a_session_for_another_payment():
    client = StripeClient(
        stripe_settings(),
        transport=transport(
            lambda r: httpx.Response(
                200,
                json={"id": "cs_1", "client_reference_id": "CK-OTHER", "payment_status": "paid"},
            )
        ),
    )
    with pytest.raises(IntegrationError, match="another payment"):
        await client.verify("CK-1", provider_ref="cs_1")
    with pytest.raises(IntegrationError, match="session id"):
        await client.verify("CK-1", provider_ref=None)
    with pytest.raises(IntegrationError, match="session id"):
        await client.verify("CK-1", provider_ref="../../v1/charges")


async def test_stripe_errors_are_integration_errors():
    client = StripeClient(
        stripe_settings(),
        transport=transport(
            lambda r: httpx.Response(400, json={"error": {"message": "No such session"}})
        ),
    )
    with pytest.raises(IntegrationError, match="No such session") as exc:
        await client.verify("CK-1", provider_ref="cs_missing")
    assert exc.value.retryable is False
    down = StripeClient(stripe_settings(), transport=transport(lambda r: httpx.Response(502)))
    with pytest.raises(IntegrationError) as exc:
        await down.verify("CK-1", provider_ref="cs_1")
    assert exc.value.retryable is True
    empty = StripeClient(
        stripe_settings(), transport=transport(lambda r: httpx.Response(200, json={}))
    )
    with pytest.raises(IntegrationError, match="no checkout session"):
        await empty.initialize(
            reference="CK-1", amount_minor=5, currency="NGN", email="", callback_url="https://x"
        )


def test_stripe_client_needs_both_secrets():
    with pytest.raises(IntegrationError):
        StripeClient(stripe_settings(STRIPE_WEBHOOK_SECRET=None))
    with pytest.raises(IntegrationError):
        StripeClient(stripe_settings(STRIPE_SECRET_KEY=None))


def test_stripe_client_checks_webhooks_with_its_secret():
    client = StripeClient(stripe_settings())
    body = b'{"id":"evt_1","type":"checkout.session.completed"}'
    assert client.verify_webhook(body, stripe_signature("whsec_test", body))
    assert not client.verify_webhook(body, stripe_signature("sk_test_stripe", body))


# ------------------------------------------------------------------ webhook parsing
def test_paystack_parse_redacts_and_keys_by_transaction():
    body = json.dumps(
        {
            "event": "charge.success",
            "data": {
                "id": 302961,
                "reference": "CK-1",
                "status": "success",
                "amount": "600000",
                "currency": "NGN",
                "customer": {"email": "a@b.co", "phone": "+2348031234567"},
                "authorization": {"last4": "4081"},
            },
        }
    ).encode()
    notice = parse_paystack_webhook(body)
    assert notice.event_id == "charge.success:302961"
    assert notice.reference == "CK-1" and notice.provider_ref == "302961"
    assert notice.payload["data"]["amount"] == 600_000
    assert "a@b.co" not in json.dumps(notice.payload) and "4081" not in json.dumps(notice.payload)


def test_paystack_parse_edge_cases():
    no_id = parse_paystack_webhook(b'{"event":"charge.success","data":{"reference":"CK-9"}}')
    assert no_id.event_id == "charge.success:CK-9"
    transfer = parse_paystack_webhook(
        b'{"event":"transfer.success","data":{"id":5,"reference":"TRF-1"}}'
    )
    assert transfer.reference is None  # not about a checkout of ours
    bad_ref = parse_paystack_webhook(
        b'{"event":"charge.success","data":{"id":5,"reference":"no spaces allowed"}}'
    )
    assert bad_ref.reference is None
    for body in (b"", b"null", b'{"event":1,"data":{}}', b'{"event":"x","data":{}}', b"\xff\xfe"):
        with pytest.raises(ValueError):
            parse_paystack_webhook(body)


def test_stripe_parse():
    body = json.dumps(
        {
            "id": "evt_9",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_9",
                    "metadata": {"reference": "CK-9"},
                    "amount_total": 5,
                    "customer_details": {"email": "x@y.z"},
                }
            },
        }
    ).encode()
    notice = parse_stripe_webhook(body)
    assert (notice.event_id, notice.reference, notice.provider_ref) == ("evt_9", "CK-9", "cs_9")
    assert "x@y.z" not in json.dumps(notice.payload)
    other = parse_stripe_webhook(
        b'{"id":"evt_2","type":"invoice.paid","data":{"object":{"id":"in_1","client_reference_id":"CK-9"}}}'
    )
    assert other.reference is None and other.provider_ref is None
    for body in (b"{}", b'{"id":"evt_1"}', b"[]"):
        with pytest.raises(ValueError):
            parse_stripe_webhook(body)


# ------------------------------------------------------------------ mocks
async def test_mock_outcomes_and_outages():
    client = MockPaymentClient()
    await client.initialize(reference="CK-1", amount_minor=500, currency="ngn", email="")
    assert (await client.verify("CK-1")).status is PaymentStatus.SUCCEEDED
    client.set_outcome("CK-1", PaymentStatus.SUCCEEDED, amount_minor=499)
    assert (await client.verify("CK-1")).amount_minor == 499
    client.unavailable = True
    with pytest.raises(IntegrationError) as exc:
        await client.verify("CK-1")
    assert exc.value.retryable is True


async def test_mock_stripe_needs_the_right_session():
    client = MockStripeClient()
    checkout = await client.initialize(reference="CK-1", amount_minor=500, currency="NGN", email="")
    assert checkout.provider_ref and checkout.provider_ref.startswith("cs_mock_")
    ok = await client.verify("CK-1", provider_ref=checkout.provider_ref)
    assert ok.status is PaymentStatus.SUCCEEDED
    with pytest.raises(IntegrationError):
        await client.verify("CK-1", provider_ref="cs_other")


async def test_mock_escrow_fund_and_outage():
    escrow = MockEscrowClient()
    record = await escrow.create(
        reference="b-1",
        amount_minor=500,
        currency="NGN",
        payer_ref="p",
        payee_ref="c",
        description="",
    )
    held = await escrow.fund(record.escrow_ref, payment_ref="CK-1")
    assert held.status is EscrowStatus.HELD
    again = await escrow.fund(record.escrow_ref, payment_ref="CK-1")
    assert again.status is EscrowStatus.HELD  # idempotent
    await escrow.release(record.escrow_ref)
    with pytest.raises(EscrowStateError):
        await escrow.refund(record.escrow_ref, reason="late")
    escrow.unavailable = True
    with pytest.raises(IntegrationError):
        await escrow.get(record.escrow_ref)


# ------------------------------------------------------------------ factory
def test_gateway_factory_with_mocks():
    assert isinstance(payments.get_gateway_client(Gateway.PAYSTACK), MockPaymentClient)
    assert isinstance(payments.get_gateway_client("stripe"), MockStripeClient)
    assert isinstance(payments.get_payment_client("GBP"), MockStripeClient)


def test_gateway_factory_without_mocks(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "USE_MOCK_INTEGRATIONS", False)
    for key in ("PAYSTACK_SECRET_KEY", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
        monkeypatch.setattr(settings, key, None)
    for gateway in Gateway:
        with pytest.raises(ServiceUnavailableError) as exc:
            payments.get_gateway_client(gateway)
        assert exc.value.code == "payments_not_configured"
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", SecretStr("sk_test_p"))
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", SecretStr("sk_test_s"))
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", SecretStr("whsec_s"))
    assert isinstance(payments.get_gateway_client("paystack"), PaystackClient)
    assert isinstance(payments.get_gateway_client("stripe"), StripeClient)
    with pytest.raises(ValueError):
        payments.get_gateway_client("flutterwave")


async def test_paystack_verify_reads_string_amounts_and_uppercases_currency():
    client = PaystackClient(
        Settings(_env_file=None, PAYSTACK_SECRET_KEY="sk_test_abc"),
        transport=transport(
            lambda r: httpx.Response(
                200,
                json={
                    "status": True,
                    "data": {
                        "reference": "CK-1",
                        "status": "success",
                        "amount": "600000",
                        "currency": "ngn",
                        "id": 1,
                    },
                },
            )
        ),
    )
    result = await client.verify("CK-1")
    assert result.amount_minor == 600_000 and result.currency == "NGN"
    assert result.status is PaymentStatus.SUCCEEDED and result.provider_ref == "1"
