"""WhatsApp Business (Meta Cloud API) client: the care companion (Day 15) and reminders.

Meta's rules this client follows:
- Free-form text only reaches someone who messaged us in the last 24 hours. Outside that
  window, send an approved template (`send_template`); Meta refuses free text with
  error 131047, raised here as a non-retryable IntegrationError.
- Webhooks: Meta first calls GET with hub.mode/hub.verify_token/hub.challenge
  (`verify_subscription`), then POSTs events signed with the app secret in
  X-Hub-Signature-256 (`verify_signature`). Reject anything unsigned.

Crisis alerts go by SMS, not WhatsApp: SMS reaches basic phones without data.

The mock records messages in `outbox` and uses MOCK_VERIFY_TOKEN / MOCK_APP_SECRET.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from app.config import Settings, get_settings
from app.core.phone import mask_phone
from app.integrations.base import IntegrationError, not_configured, request_json

logger = logging.getLogger(__name__)

MOCK_VERIFY_TOKEN = "mock-verify-token"
MOCK_APP_SECRET = "mock-app-secret"
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
MAX_TEXT_LENGTH = 4096  # Meta's limit for a text message body


class WebhookVerificationFailed(Exception):
    """The subscription handshake had the wrong mode or token."""


@dataclass(frozen=True, slots=True)
class WhatsAppMessage:
    to: str
    provider_ref: str
    sent_at: datetime
    body: str | None = None
    template: str | None = None


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    from_phone: str  # E.164
    message_id: str
    text: str | None  # None for media, locations, reactions...
    received_at: datetime


class WhatsAppClient(Protocol):
    async def send_text(self, to: str, body: str) -> WhatsAppMessage: ...

    async def send_template(
        self, to: str, template: str, *, language: str = "en", parameters: list[str] | None = None
    ) -> WhatsAppMessage: ...

    def verify_subscription(self, mode: str | None, token: str | None, challenge: str) -> str:
        """Return the challenge to echo back, or raise WebhookVerificationFailed."""
        ...

    def verify_signature(self, body: bytes, header: str | None) -> bool: ...


def _validate(to: str) -> None:
    if not _E164.match(to):
        raise ValueError("WhatsApp numbers must be in E.164 format")


def _subscription(expected: str, mode: str | None, token: str | None, challenge: str) -> str:
    if mode != "subscribe" or not token or not hmac.compare_digest(token, expected):
        raise WebhookVerificationFailed("WhatsApp webhook verification failed")
    return challenge


def _signature_ok(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def sign_body(secret: str, body: bytes) -> str:
    """The X-Hub-Signature-256 value Meta would send (for tests and local tools)."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def parse_incoming(payload: dict[str, Any]) -> list[IncomingMessage]:
    """Messages from a webhook POST body. Status updates and other events are skipped."""
    messages: list[IncomingMessage] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for msg in value.get("messages") or []:
                sender = str(msg.get("from") or "")
                if not sender or not msg.get("id"):
                    continue
                text = (msg.get("text") or {}).get("body") if msg.get("type") == "text" else None
                timestamp = msg.get("timestamp")
                messages.append(
                    IncomingMessage(
                        from_phone="+" + sender.lstrip("+"),
                        message_id=str(msg["id"]),
                        text=text,
                        received_at=(
                            datetime.fromtimestamp(int(timestamp), UTC)
                            if timestamp
                            else datetime.now(UTC)
                        ),
                    )
                )
    return messages


class CloudAPIWhatsAppClient:
    provider = "whatsapp_cloud"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        missing = [
            name
            for name in (
                "WHATSAPP_ACCESS_TOKEN",
                "WHATSAPP_PHONE_NUMBER_ID",
                "WHATSAPP_VERIFY_TOKEN",
                "WHATSAPP_APP_SECRET",
            )
            if not getattr(settings, name)
        ]
        if missing:
            raise IntegrationError(self.provider, f"missing {', '.join(missing)}", retryable=False)
        self._token = settings.WHATSAPP_ACCESS_TOKEN.get_secret_value()
        self._verify_token = settings.WHATSAPP_VERIFY_TOKEN.get_secret_value()
        self._app_secret = settings.WHATSAPP_APP_SECRET.get_secret_value()
        self._url = (
            f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/"
            f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        self._timeout = settings.INTEGRATION_TIMEOUT_SECONDS
        self._transport = transport

    async def _send(self, to: str, payload: dict[str, Any]) -> str:
        _validate(to)
        data = await request_json(
            self.provider,
            "POST",
            self._url,
            timeout=self._timeout,
            transport=self._transport,
            headers={"Authorization": f"Bearer {self._token}"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to.lstrip("+"),
                **payload,
            },
        )
        try:
            message_id = str(data["messages"][0]["id"])
        except (KeyError, IndexError, TypeError):
            raise IntegrationError(self.provider, "no message id in the response") from None
        logger.info("WhatsApp message to %s accepted", mask_phone(to))
        return message_id

    async def send_text(self, to: str, body: str) -> WhatsAppMessage:
        if not body or len(body) > MAX_TEXT_LENGTH:
            raise ValueError(f"WhatsApp text must be 1-{MAX_TEXT_LENGTH} characters")
        ref = await self._send(to, {"type": "text", "text": {"preview_url": False, "body": body}})
        return WhatsAppMessage(to=to, provider_ref=ref, sent_at=datetime.now(UTC), body=body)

    async def send_template(
        self, to: str, template: str, *, language: str = "en", parameters: list[str] | None = None
    ) -> WhatsAppMessage:
        body: dict[str, Any] = {"name": template, "language": {"code": language}}
        if parameters:
            body["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in parameters]}
            ]
        ref = await self._send(to, {"type": "template", "template": body})
        return WhatsAppMessage(
            to=to, provider_ref=ref, sent_at=datetime.now(UTC), template=template
        )

    def verify_subscription(self, mode: str | None, token: str | None, challenge: str) -> str:
        return _subscription(self._verify_token, mode, token, challenge)

    def verify_signature(self, body: bytes, header: str | None) -> bool:
        return _signature_ok(self._app_secret, body, header)


@dataclass
class MockWhatsAppClient:
    outbox: list[WhatsAppMessage] = field(default_factory=list)

    def _record(self, to: str, **kwargs: Any) -> WhatsAppMessage:
        _validate(to)
        message = WhatsAppMessage(
            to=to,
            provider_ref=f"mock-wamid-{len(self.outbox) + 1}",
            sent_at=datetime.now(UTC),
            **kwargs,
        )
        self.outbox.append(message)
        return message

    async def send_text(self, to: str, body: str) -> WhatsAppMessage:
        if not body or len(body) > MAX_TEXT_LENGTH:
            raise ValueError(f"WhatsApp text must be 1-{MAX_TEXT_LENGTH} characters")
        return self._record(to, body=body)

    async def send_template(
        self, to: str, template: str, *, language: str = "en", parameters: list[str] | None = None
    ) -> WhatsAppMessage:
        return self._record(to, template=template)

    def verify_subscription(self, mode: str | None, token: str | None, challenge: str) -> str:
        return _subscription(MOCK_VERIFY_TOKEN, mode, token, challenge)

    def verify_signature(self, body: bytes, header: str | None) -> bool:
        return _signature_ok(MOCK_APP_SECRET, body, header)


_mock_client = MockWhatsAppClient()


def get_whatsapp_client() -> WhatsAppClient:
    """FastAPI dependency."""
    settings = get_settings()
    if settings.USE_MOCK_INTEGRATIONS:
        return _mock_client
    try:
        return CloudAPIWhatsAppClient(settings)
    except IntegrationError as exc:
        raise not_configured("whatsapp", f"WhatsApp Cloud API is not configured ({exc}).") from exc
