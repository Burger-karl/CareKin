"""Tests: partner clients and their mocks (build plan Day 3, ADR 0004). No network."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings, get_settings
from app.core.exceptions import ServiceUnavailableError
from app.integrations import background_check, escrow, nin, payments, whatsapp
from app.integrations.base import IntegrationError, request_json
from app.integrations.escrow import EscrowStateError, EscrowStatus, MockEscrowClient
from app.integrations.payments import (
    MOCK_WEBHOOK_SECRET,
    MockPaymentClient,
    PaymentStatus,
    PaystackClient,
    paystack_signature,
)
from app.integrations.whatsapp import (
    MOCK_APP_SECRET,
    MOCK_VERIFY_TOKEN,
    CloudAPIWhatsAppClient,
    MockWhatsAppClient,
    WebhookVerificationFailed,
    parse_incoming,
    sign_body,
)


def transport(handler, seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def record(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return httpx.MockTransport(record)


# ------------------------------------------------------------------ shared HTTP helper
@pytest.mark.parametrize(
    ("response", "message", "retryable"),
    [
        (httpx.Response(503, text="down"), "HTTP 503", True),
        (httpx.Response(200, text="<html>"), "non-JSON", True),
        (httpx.Response(200, json=[1, 2]), "unexpected response shape", True),
        (httpx.Response(400, json={"message": "Invalid key"}), "HTTP 400: Invalid key", False),
        (
            httpx.Response(400, json={"error": {"message": "(#131047) Re-engagement"}}),
            "131047",
            False,
        ),
        (httpx.Response(422, json={"error": "bad"}), "HTTP 422: bad", False),
    ],
)
async def test_request_json_failures(response, message, retryable):
    with pytest.raises(IntegrationError, match=message) as exc:
        await request_json(
            "p", "GET", "https://x.test/", timeout=1, transport=transport(lambda _: response)
        )
    assert exc.value.retryable is retryable and exc.value.provider == "p"


async def test_request_json_network_error():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(IntegrationError, match="unreachable \\(ReadTimeout\\)"):
        await request_json("p", "GET", "https://x.test/", timeout=1, transport=transport(boom))


# ------------------------------------------------------------------ NIN
@pytest.mark.parametrize(
    ("number", "passed", "detail"),
    [
        ("12345678901", True, "NIN record matches"),
        ("12345670000", False, "No record found for this NIN"),
        ("12345671111", False, "NIN record name does not match"),
    ],
)
async def test_mock_nin_outcomes(number, passed, detail):
    client = nin.MockNINClient()
    result = await client.verify(number, full_name="Ada Eze")
    assert (result.passed, result.detail, result.nin_last4) == (passed, detail, number[-4:])
    assert result.reference.startswith("mock-nin-")
    assert number not in result.reference and number not in result.detail  # NDPA
    assert client.lookups == 1 and client.name == "mock"


async def test_mock_nin_outage_is_a_service_unavailable_error():
    client = nin.MockNINClient()
    with pytest.raises(ServiceUnavailableError) as exc:  # what caregivers catches
        await client.verify("12345679999", full_name="Ada Eze")
    assert isinstance(exc.value, IntegrationError) and exc.value.code == "provider_unavailable"
    assert exc.value.status_code == 503
    with pytest.raises(nin.InvalidNIN):
        await client.verify("1234", full_name="Ada Eze")
    assert nin.clean_nin("123 4567-8903") == "12345678903"


# ------------------------------------------------------------------ background checks
@pytest.mark.parametrize(
    ("name", "passed"),
    [("Ada Eze", True), ("Flagged Person", False), ("someone FLAGGED", False)],
)
async def test_mock_background_check_outcomes(name, passed):
    client = background_check.MockBackgroundCheckClient()
    result = await client.verify(full_name=name, phone="+2348030000001")
    assert result.passed is passed and result.reference.startswith("mock-bg-")
    assert client.lookups == 1 and client.name == "mock"
    # Same person, same reference: retries don't look like new subjects.
    again = await client.verify(full_name=name, phone="+2348030000001")
    assert again.reference == result.reference


async def test_mock_background_check_outage():
    client = background_check.MockBackgroundCheckClient()
    with pytest.raises(ServiceUnavailableError):
        await client.verify(full_name="Provider Outage", phone="+2348030000009")
    # Phone numbers never trigger outcomes (test users get sequential numbers).
    for digit in "0123456789":
        result = await client.verify(full_name="Ada Eze", phone=f"+234803000000{digit}")
        assert result.passed


# ------------------------------------------------------------------ escrow
async def test_escrow_happy_path_and_idempotency():
    client = MockEscrowClient()
    kwargs = {
        "reference": "booking-1",
        "amount_minor": 1_500_000,
        "currency": "ngn",
        "payer_ref": "fam",
        "payee_ref": "cg",
        "description": "Shift",
    }
    record = await client.create(**kwargs)
    assert record.status is EscrowStatus.PENDING and record.currency == "NGN"
    assert await client.create(**kwargs) == record  # retried create returns the same escrow

    with pytest.raises(EscrowStateError, match="can't release an escrow that is pending") as exc:
        await client.release(record.escrow_ref)
    assert exc.value.status_code == 409 and exc.value.code == "escrow_state_conflict"

    client.simulate_funding(record.escrow_ref)
    released = await client.release(record.escrow_ref)
    assert released.status is EscrowStatus.RELEASED
    assert (
        await client.release(record.escrow_ref)
    ).status is EscrowStatus.RELEASED  # retry is a no-op
    with pytest.raises(EscrowStateError):
        await client.refund(record.escrow_ref, reason="too late")


async def test_escrow_dispute_blocks_release_ts5():
    client = MockEscrowClient()
    record = await client.create(
        reference="booking-2",
        amount_minor=500_000,
        currency="NGN",
        payer_ref="f",
        payee_ref="c",
        description="x",
    )
    client.simulate_funding(record.escrow_ref)
    held = await client.open_dispute(record.escrow_ref, reason="incident")
    assert held.status is EscrowStatus.ON_HOLD_DISPUTE

    with pytest.raises(EscrowStateError):
        await client.release(record.escrow_ref)
    assert (await client.close_dispute(record.escrow_ref)).status is EscrowStatus.HELD
    await client.open_dispute(record.escrow_ref, reason="again")
    assert (await client.refund(record.escrow_ref, reason="upheld")).status is EscrowStatus.REFUNDED


async def test_escrow_rejects_bad_input():
    client = MockEscrowClient()
    with pytest.raises(EscrowStateError):
        await client.create(
            reference="b",
            amount_minor=0,
            currency="NGN",
            payer_ref="",
            payee_ref="",
            description="",
        )
    await client.create(
        reference="b", amount_minor=100, currency="NGN", payer_ref="", payee_ref="", description=""
    )
    with pytest.raises(EscrowStateError, match="different amount"):
        await client.create(
            reference="b",
            amount_minor=200,
            currency="NGN",
            payer_ref="",
            payee_ref="",
            description="",
        )
    with pytest.raises(IntegrationError, match="unknown escrow"):
        await client.get("nope")
    unpaid = await client.create(
        reference="c", amount_minor=100, currency="NGN", payer_ref="", payee_ref="", description=""
    )
    assert (
        await client.refund(unpaid.escrow_ref, reason="cancelled")
    ).status is EscrowStatus.REFUNDED
    assert {s.value for s in EscrowStatus} == {
        "pending",
        "held",
        "on_hold_dispute",
        "released",
        "refunded",
    }


# ------------------------------------------------------------------ payments
def paystack_settings(**overrides) -> Settings:
    return Settings(_env_file=None, PAYSTACK_SECRET_KEY="sk_test_abc", **overrides)


async def test_paystack_initialize():
    seen: list[httpx.Request] = []
    client = PaystackClient(
        paystack_settings(),
        transport=transport(
            lambda _: httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "Authorization URL created",
                    "data": {
                        "authorization_url": "https://checkout.paystack.com/0peioxfhpn",
                        "access_code": "0peioxfhpn",
                        "reference": "bk-123",
                    },
                },
            ),
            seen,
        ),
    )
    checkout = await client.initialize(
        reference="bk-123",
        amount_minor=1_500_000,
        currency="ngn",
        email="ada@example.com",
        callback_url="https://app.carekin.ng/pay/done",
        metadata={"booking_id": "123"},
    )
    assert checkout.authorization_url.startswith("https://checkout.paystack.com/")
    (request,) = seen
    assert str(request.url) == "https://api.paystack.co/transaction/initialize"
    assert request.headers["authorization"] == "Bearer sk_test_abc"
    assert json.loads(request.content) == {
        "email": "ada@example.com",
        "amount": 1_500_000,
        "currency": "NGN",
        "reference": "bk-123",
        "callback_url": "https://app.carekin.ng/pay/done",
        "metadata": {"booking_id": "123"},
    }


@pytest.mark.parametrize(
    ("paystack_status", "ours"),
    [
        ("success", PaymentStatus.SUCCEEDED),
        ("failed", PaymentStatus.FAILED),
        ("abandoned", PaymentStatus.INITIATED),
        ("ongoing", PaymentStatus.INITIATED),
        ("reversed", PaymentStatus.REFUNDED),
    ],
)
async def test_paystack_verify_maps_statuses(paystack_status, ours):
    seen: list[httpx.Request] = []
    data = {
        "id": 4099260516,
        "status": paystack_status,
        "reference": "bk-123",
        "amount": 1_500_000,
        "currency": "NGN",
        "paid_at": "2026-10-14T10:05:11.000Z" if paystack_status == "success" else None,
    }
    client = PaystackClient(
        paystack_settings(),
        transport=transport(
            lambda _: httpx.Response(200, json={"status": True, "data": data}), seen
        ),
    )
    result = await client.verify("bk-123")
    assert (
        result.status is ours
        and result.amount_minor == 1_500_000
        and result.provider_ref == "4099260516"
    )
    assert seen[0].url.path == "/transaction/verify/bk-123"
    if ours is PaymentStatus.SUCCEEDED:
        assert result.paid_at.isoformat() == "2026-10-14T10:05:11+00:00"


async def test_paystack_refusals_and_bad_input():
    client = PaystackClient(
        paystack_settings(),
        transport=transport(
            lambda _: httpx.Response(
                400, json={"status": False, "message": "Duplicate Transaction Reference"}
            )
        ),
    )
    with pytest.raises(IntegrationError, match="Duplicate Transaction Reference") as exc:
        await client.initialize(reference="bk-1", amount_minor=100, currency="NGN", email="a@b.co")
    assert exc.value.retryable is False
    with pytest.raises(ValueError, match="references"):
        await client.initialize(
            reference="bad ref/../", amount_minor=100, currency="NGN", email="a@b.co"
        )
    with pytest.raises(ValueError, match="positive"):
        await client.initialize(reference="ok", amount_minor=0, currency="NGN", email="a@b.co")
    weird = PaystackClient(
        paystack_settings(),
        transport=transport(lambda _: httpx.Response(200, json={"status": False})),
    )
    with pytest.raises(IntegrationError):
        await weird.verify("bk-1")


def test_paystack_webhook_signature():
    client = PaystackClient(paystack_settings())
    body = b'{"event":"charge.success","data":{"reference":"bk-1"}}'
    good = paystack_signature("sk_test_abc", body)
    assert client.verify_webhook(body, good)
    assert not client.verify_webhook(body + b" ", good)
    assert not client.verify_webhook(body, None)
    assert not client.verify_webhook(body, paystack_signature("other", body))


async def test_mock_payments():
    client = MockPaymentClient()
    checkout = await client.initialize(
        reference="bk-9", amount_minor=200_000, currency="NGN", email="a@b.co"
    )
    assert checkout.authorization_url.endswith("/bk-9")
    assert (await client.verify("bk-9")).status is PaymentStatus.SUCCEEDED
    await client.initialize(
        reference="bk-fail-1", amount_minor=200_000, currency="NGN", email="a@b.co"
    )
    failed = await client.verify("bk-fail-1")
    assert failed.status is PaymentStatus.FAILED and failed.paid_at is None
    with pytest.raises(IntegrationError):
        await client.verify("never-initialized")
    body = b"{}"
    assert client.verify_webhook(body, paystack_signature(MOCK_WEBHOOK_SECRET, body))
    assert not client.verify_webhook(body, "forged")


# ------------------------------------------------------------------ WhatsApp
def whatsapp_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "WHATSAPP_ACCESS_TOKEN": "EAAG-token",
        "WHATSAPP_PHONE_NUMBER_ID": "1234567890",
        "WHATSAPP_VERIFY_TOKEN": "verify-me",
        "WHATSAPP_APP_SECRET": "app-secret",
    }
    values.update(overrides)
    return Settings(**values)


def wa_ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": "2348031234567", "wa_id": "2348031234567"}],
            "messages": [{"id": "wamid.HBgN"}],
        },
    )


async def test_cloud_api_send_text_and_template():
    seen: list[httpx.Request] = []
    client = CloudAPIWhatsAppClient(whatsapp_settings(), transport=transport(wa_ok, seen))

    sent = await client.send_text("+2348031234567", "Reminder: drink water")
    assert sent.provider_ref == "wamid.HBgN"
    assert str(seen[0].url) == "https://graph.facebook.com/v21.0/1234567890/messages"
    assert seen[0].headers["authorization"] == "Bearer EAAG-token"
    assert json.loads(seen[0].content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "2348031234567",
        "type": "text",
        "text": {"preview_url": False, "body": "Reminder: drink water"},
    }

    await client.send_template(
        "+2348031234567", "appointment_reminder", parameters=["Ada", "10:00"]
    )
    template = json.loads(seen[1].content)["template"]
    assert template == {
        "name": "appointment_reminder",
        "language": {"code": "en"},
        "components": [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": "Ada"}, {"type": "text", "text": "10:00"}],
            }
        ],
    }


async def test_cloud_api_outside_24h_window_is_not_retryable():
    def refused(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "(#131047) Re-engagement message", "code": 131047}},
        )

    client = CloudAPIWhatsAppClient(whatsapp_settings(), transport=transport(refused))
    with pytest.raises(IntegrationError, match="131047") as exc:
        await client.send_text("+2348031234567", "hi")
    assert exc.value.retryable is False
    with pytest.raises(ValueError):
        await client.send_text("08031234567", "hi")
    with pytest.raises(ValueError):
        await client.send_text("+2348031234567", "")


def test_cloud_api_needs_every_setting():
    with pytest.raises(IntegrationError, match="WHATSAPP_APP_SECRET"):
        CloudAPIWhatsAppClient(whatsapp_settings(WHATSAPP_APP_SECRET=None))


def test_webhook_handshake_and_signatures():
    for client, token, secret in (
        (CloudAPIWhatsAppClient(whatsapp_settings()), "verify-me", "app-secret"),
        (MockWhatsAppClient(), MOCK_VERIFY_TOKEN, MOCK_APP_SECRET),
    ):
        assert client.verify_subscription("subscribe", token, "1158201444") == "1158201444"
        for mode, bad_token in (
            ("subscribe", "wrong"),
            ("unsubscribe", token),
            ("subscribe", None),
        ):
            with pytest.raises(WebhookVerificationFailed):
                client.verify_subscription(mode, bad_token, "x")
        body = b'{"object":"whatsapp_business_account"}'
        assert client.verify_signature(body, sign_body(secret, body))
        assert not client.verify_signature(body, sign_body("other", body))
        assert not client.verify_signature(body, None)
        assert not client.verify_signature(body, "md5=abc")


def test_parse_incoming_messages():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "2348031234567",
                                    "id": "wamid.1",
                                    "timestamp": "1760436000",
                                    "type": "text",
                                    "text": {"body": "I'm in pain"},
                                },
                                {
                                    "from": "2348031234567",
                                    "id": "wamid.2",
                                    "timestamp": "1760436001",
                                    "type": "image",
                                    "image": {"id": "x"},
                                },
                            ],
                            "statuses": [{"id": "wamid.0", "status": "delivered"}],
                        }
                    }
                ]
            }
        ],
    }
    first, second = parse_incoming(payload)
    assert (first.from_phone, first.text, first.message_id) == (
        "+2348031234567",
        "I'm in pain",
        "wamid.1",
    )
    assert second.text is None
    assert first.received_at.isoformat() == "2025-10-14T10:00:00+00:00"
    assert parse_incoming({"entry": [{"changes": [{"value": {"statuses": []}}]}]}) == []


async def test_mock_whatsapp_records():
    client = MockWhatsAppClient()
    await client.send_text("+2348031234567", "hello")
    await client.send_template("+2348031234567", "welcome")
    assert [m.provider_ref for m in client.outbox] == ["mock-wamid-1", "mock-wamid-2"]
    assert client.outbox[1].template == "welcome"


# ------------------------------------------------------------------ factories
FACTORIES = [
    ("nin", nin.get_nin_client, nin.MockNINClient),
    (
        "background_check",
        background_check.get_background_check_client,
        background_check.MockBackgroundCheckClient,
    ),
    ("escrow", escrow.get_escrow_client, escrow.MockEscrowClient),
    ("payments", payments.get_payment_client, payments.MockPaymentClient),
    ("whatsapp", whatsapp.get_whatsapp_client, whatsapp.MockWhatsAppClient),
]


@pytest.mark.parametrize(("name", "factory", "mock_cls"), FACTORIES)
def test_factories_use_mocks_when_asked(name, factory, mock_cls):
    assert get_settings().USE_MOCK_INTEGRATIONS is True
    assert isinstance(factory(), mock_cls)


@pytest.mark.parametrize(("name", "factory", "mock_cls"), FACTORIES)
def test_factories_never_fall_back_to_mocks(monkeypatch, name, factory, mock_cls):
    monkeypatch.setattr(get_settings(), "USE_MOCK_INTEGRATIONS", False)
    for key in ("PAYSTACK_SECRET_KEY", "WHATSAPP_ACCESS_TOKEN"):
        monkeypatch.setattr(get_settings(), key, None)
    with pytest.raises(ServiceUnavailableError) as exc:
        factory()
    assert exc.value.code == f"{name}_not_configured"


def test_real_clients_are_returned_when_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "USE_MOCK_INTEGRATIONS", False)
    for key, value in whatsapp_settings().model_dump().items():
        if key.startswith("WHATSAPP_"):
            monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", paystack_settings().PAYSTACK_SECRET_KEY)
    assert isinstance(whatsapp.get_whatsapp_client(), CloudAPIWhatsAppClient)
    assert isinstance(payments.get_payment_client("NGN"), PaystackClient)
    with pytest.raises(ServiceUnavailableError, match="Stripe"):
        payments.get_payment_client("GBP")
