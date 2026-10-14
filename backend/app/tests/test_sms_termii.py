"""Tests: Termii SMS client and SMS configuration guards. No network: httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings
from app.core.exceptions import ServiceUnavailableError
from app.integrations import sms as sms_module
from app.integrations.sms import MockSMSClient, SMSDeliveryError, TermiiSMSClient

BASE_URL = "https://v3.api.termii.com"


def termii_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "SMS_PROVIDER": "termii",
        "SMS_PROVIDER_API_KEY": "test-key",
        "SMS_PROVIDER_BASE_URL": BASE_URL + "/",
        "SMS_SENDER_ID": "CareKin",
        **overrides,
    }
    return Settings(**values)


def client_returning(handler) -> tuple[TermiiSMSClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return TermiiSMSClient(termii_settings(), transport=httpx.MockTransport(record)), seen


async def test_sends_on_the_dnd_channel_without_the_plus():
    client, seen = client_returning(
        lambda _: httpx.Response(
            200,
            json={"code": "ok", "message_id_str": "3017544054459", "message": "Successfully Sent"},
        )
    )

    sent = await client.send("+2348031234567", "CareKin ALERT: test")

    assert sent.provider_ref == "3017544054459" and sent.to == "+2348031234567"
    (request,) = seen
    assert str(request.url) == f"{BASE_URL}/api/sms/send"
    payload = json.loads(request.content)
    assert payload == {
        "api_key": "test-key",
        "to": "2348031234567",
        "from": "CareKin",
        "sms": "CareKin ALERT: test",
        "type": "plain",
        "channel": "dnd",
    }


async def test_numeric_message_id_is_accepted():
    client, _ = client_returning(
        lambda _: httpx.Response(200, json={"code": "ok", "message_id": 42})
    )
    assert (await client.send("+2348031234567", "hi")).provider_ref == "42"


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401, json={"message": "Unauthorized"}), "HTTP 401"),
        (httpx.Response(500, text="oops"), "HTTP 500"),
        (httpx.Response(200, text="<html>"), "non-JSON"),
        (
            httpx.Response(200, json={"code": "error", "message": "Insufficient balance"}),
            "Insufficient",
        ),
        (httpx.Response(200, json={"code": "ok"}), "rejected"),
    ],
)
async def test_provider_failures_raise_delivery_errors(response, error):
    client, _ = client_returning(lambda _: response)
    with pytest.raises(SMSDeliveryError, match=error):
        await client.send("+2348031234567", "hi")


async def test_network_errors_raise_delivery_errors():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    client, _ = client_returning(boom)
    with pytest.raises(SMSDeliveryError, match="unreachable: ConnectTimeout"):
        await client.send("+2348031234567", "hi")


async def test_numbers_must_be_e164():
    client, seen = client_returning(lambda _: httpx.Response(200, json={"code": "ok"}))
    with pytest.raises(SMSDeliveryError, match="E.164"):
        await client.send("08031234567", "hi")
    assert seen == []


def test_client_needs_key_and_base_url():
    with pytest.raises(SMSDeliveryError):
        TermiiSMSClient(termii_settings(SMS_PROVIDER_API_KEY=None))


def test_get_sms_client_picks_the_provider(monkeypatch):
    monkeypatch.setattr(sms_module, "get_settings", lambda: termii_settings())
    assert isinstance(sms_module.get_sms_client(), TermiiSMSClient)

    monkeypatch.setattr(
        sms_module, "get_settings", lambda: termii_settings(SMS_PROVIDER_BASE_URL=None)
    )
    with pytest.raises(ServiceUnavailableError) as exc:
        sms_module.get_sms_client()
    assert exc.value.code == "sms_not_configured"

    monkeypatch.setattr(sms_module, "get_settings", lambda: termii_settings(SMS_PROVIDER="mock"))
    assert isinstance(sms_module.get_sms_client(), MockSMSClient)


# ------------------------------------------------------------------ configuration
def deployed(environment: str, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "ENVIRONMENT": environment,
        "SECRET_KEY": "x" * 64,
        "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "DEBUG": False,
        "CORS_ORIGINS": ["https://app.carekin.ng"],
        **overrides,
    }
    return Settings(**values)


def test_sms_provider_follows_use_mock_integrations_unless_set():
    assert termii_settings(SMS_PROVIDER=None, USE_MOCK_INTEGRATIONS=True).sms_provider == "mock"
    assert termii_settings(SMS_PROVIDER=None, USE_MOCK_INTEGRATIONS=False).sms_provider == "termii"
    assert (
        termii_settings(SMS_PROVIDER="termii", USE_MOCK_INTEGRATIONS=True).sms_provider == "termii"
    )


def test_production_refuses_mock_sms():
    with pytest.raises(ValidationError, match="SMS must not be mocked in production"):
        deployed("production", SMS_PROVIDER="mock")


def test_staging_may_use_mock_sms():
    assert deployed("staging", SMS_PROVIDER="mock").sms_provider == "mock"


def test_deployed_termii_needs_credentials():
    with pytest.raises(ValidationError, match="Termii SMS needs"):
        deployed("production", SMS_PROVIDER="termii")
    ok = deployed(
        "production",
        SMS_PROVIDER="termii",
        SMS_PROVIDER_API_KEY="k",
        SMS_PROVIDER_BASE_URL=BASE_URL,
    )
    assert ok.sms_provider == "termii"


@pytest.mark.parametrize("sender", ["CK", "CareKinAlerts"])
def test_sender_id_length_is_checked(sender):
    with pytest.raises(ValidationError):
        termii_settings(SMS_SENDER_ID=sender)
