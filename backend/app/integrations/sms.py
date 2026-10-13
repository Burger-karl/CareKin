"""SMS delivery (OTP codes now; crisis alerts on Day 8).

The provider is not chosen yet (Termii, Africa's Talking and Twilio are the usual
options for Nigeria). Until it is, USE_MOCK_INTEGRATIONS=true routes every
message to MockSMSClient, which records messages in memory and logs a masked
copy. Adding the real provider means writing one class with `send()` and
returning it from get_sms_client(); nothing else changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.config import get_settings
from app.core.exceptions import ServiceUnavailableError
from app.core.phone import mask_phone

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SentMessage:
    to: str
    body: str
    provider_ref: str
    sent_at: datetime


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


_mock_client = MockSMSClient()


def get_sms_client() -> SMSClient:
    """FastAPI dependency. Tests override it with a fresh MockSMSClient."""
    if get_settings().USE_MOCK_INTEGRATIONS:
        return _mock_client
    raise ServiceUnavailableError(
        "SMS delivery is not configured. Set USE_MOCK_INTEGRATIONS=true until a provider is added.",
        code="sms_not_configured",
    )
