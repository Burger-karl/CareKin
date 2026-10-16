"""SMS delivery: OTP codes and crisis alerts.

Settings.sms_provider picks the client:
    mock    MockSMSClient records messages in memory and logs a masked copy.
            The default while USE_MOCK_INTEGRATIONS=true. Refused in production.
    termii  TermiiSMSClient, sent on Termii's "dnd" channel. Numbers on Nigeria's
            Do-Not-Disturb register silently drop "generic" messages, and an OTP or
            a crisis alert must reach them. The dnd channel needs an approved sender ID.

Every client raises SMSDeliveryError when a message isn't accepted; callers may retry.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.config import Settings, get_settings
from app.core.exceptions import ServiceUnavailableError
from app.core.phone import mask_phone

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SentMessage:
    to: str
    body: str
    provider_ref: str
    sent_at: datetime


class SMSDeliveryError(Exception):
    """The provider didn't accept the message. Safe to retry."""


_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class SMSClient(Protocol):
    async def send(self, to: str, body: str) -> SentMessage:
        """Send `body` to the E.164 number `to`. Raises on delivery failure."""
        ...


@dataclass
class MockSMSClient:
    """Deterministic stand-in: never leaves the process. Tests read `outbox`."""

    outbox: list[SentMessage] = field(default_factory=list)

    async def send(self, to: str, body: str) -> SentMessage:
        message = SentMessage(
            to=to,
            body=body,
            provider_ref=f"mock-{len(self.outbox) + 1}",
            sent_at=datetime.now(UTC),
        )
        self.outbox.append(message)
        # Log the number masked and never the body: it contains the code.
        logger.info("Mock SMS to %s (%d chars)", mask_phone(to), len(body))
        return message

    def last_to(self, to: str) -> SentMessage | None:
        for message in reversed(self.outbox):
            if message.to == to:
                return message
        return None


class TermiiSMSClient:
    """Termii Messaging API: POST {base_url}/api/sms/send."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        if not (settings.SMS_PROVIDER_API_KEY and settings.SMS_PROVIDER_BASE_URL):
            raise SMSDeliveryError("Termii needs SMS_PROVIDER_API_KEY and SMS_PROVIDER_BASE_URL")
        self._api_key = settings.SMS_PROVIDER_API_KEY.get_secret_value()
        self._url = settings.SMS_PROVIDER_BASE_URL.rstrip("/") + "/api/sms/send"
        self._sender = settings.SMS_SENDER_ID
        self._timeout = settings.SMS_TIMEOUT_SECONDS
        self._transport = transport

    async def send(self, to: str, body: str) -> SentMessage:
        if not _E164.match(to):
            raise SMSDeliveryError("Phone number must be in E.164 format")
        payload = {
            "api_key": self._api_key,
            "to": to.lstrip("+"),  # Termii wants the international number without "+"
            "from": self._sender,
            "sms": body,
            "type": "plain",
            "channel": "dnd",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                response = await http.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            raise SMSDeliveryError(f"Termii unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise SMSDeliveryError(f"Termii returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise SMSDeliveryError("Termii returned a non-JSON response") from exc
        message_id = data.get("message_id_str") or data.get("message_id")
        if data.get("code") != "ok" or not message_id:
            raise SMSDeliveryError(f"Termii rejected the message: {data.get('message', 'unknown')}")
        logger.info("SMS to %s accepted by Termii", mask_phone(to))
        return SentMessage(
            to=to, body=body, provider_ref=str(message_id), sent_at=datetime.now(UTC)
        )


_mock_client = MockSMSClient()


def get_sms_client() -> SMSClient:
    """FastAPI dependency. Tests override it with a fresh MockSMSClient."""
    settings = get_settings()
    if settings.sms_provider == "mock":
        return _mock_client
    try:
        return TermiiSMSClient(settings)
    except SMSDeliveryError as exc:
        raise ServiceUnavailableError(
            "SMS delivery is not configured. Set SMS_PROVIDER_API_KEY and SMS_PROVIDER_BASE_URL, "
            "or USE_MOCK_INTEGRATIONS=true for local development.",
            code="sms_not_configured",
        ) from exc
