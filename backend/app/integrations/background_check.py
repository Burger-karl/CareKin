"""Background check provider client (caregiver vetting, Day 6).

The provider is open decision Topic 6. Until it is chosen, only the mock exists.

Checks are asynchronous at real providers: `request_check` returns a reference with
status `requested`, and `get_result` is polled (or a webhook arrives) until it is
`passed` or `failed`. Statuses match the caregivers domain's CheckStatus. Results carry
an outcome only, never the provider's full report: anything adverse goes to a human
in the ops console, and approval is always a human decision (PRD section 6).

Mock rules (by the subject phone number's last digit):
    7   failed (needs human review)
    8   stays `requested` (provider still working)
    9   request fails (IntegrationError)
    any other digit: passed
The outcome is encoded in the mock reference, so any process can poll it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Protocol

from app.config import get_settings
from app.integrations.base import IntegrationError, not_configured


class BackgroundCheckStatus(StrEnum):
    REQUESTED = "requested"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BackgroundCheckSubject:
    full_name: str
    phone: str  # E.164
    date_of_birth: date | None = None


@dataclass(frozen=True, slots=True)
class BackgroundCheckResult:
    provider: str
    provider_ref: str
    status: BackgroundCheckStatus
    summary: str | None = None  # short, non-sensitive; never the full report
    completed_at: datetime | None = None


class BackgroundCheckClient(Protocol):
    async def request_check(self, subject: BackgroundCheckSubject) -> BackgroundCheckResult:
        """Start a check. Raises IntegrationError if the provider refuses."""
        ...

    async def get_result(self, provider_ref: str) -> BackgroundCheckResult:
        """Current status of a check started earlier."""
        ...


class MockBackgroundCheckClient:
    provider = "mock"

    async def request_check(self, subject: BackgroundCheckSubject) -> BackgroundCheckResult:
        digit = subject.phone[-1:]
        if digit == "9":
            raise IntegrationError(self.provider, "simulated background-check outage")
        seed = f"{subject.full_name}|{subject.phone}".encode()
        ref = f"mock-bg-{hashlib.sha256(seed).hexdigest()[:12]}-{digit}"
        return BackgroundCheckResult(self.provider, ref, BackgroundCheckStatus.REQUESTED)

    async def get_result(self, provider_ref: str) -> BackgroundCheckResult:
        if not provider_ref.startswith("mock-bg-"):
            raise IntegrationError(self.provider, "unknown check reference", retryable=False)
        digit = provider_ref[-1]
        if digit == "8":
            return BackgroundCheckResult(
                self.provider, provider_ref, BackgroundCheckStatus.REQUESTED
            )
        now = datetime.now(UTC)
        if digit == "7":
            return BackgroundCheckResult(
                self.provider,
                provider_ref,
                BackgroundCheckStatus.FAILED,
                summary="Record found that needs human review",
                completed_at=now,
            )
        return BackgroundCheckResult(
            self.provider, provider_ref, BackgroundCheckStatus.PASSED, completed_at=now
        )


_mock_client = MockBackgroundCheckClient()


def get_background_check_client() -> BackgroundCheckClient:
    """FastAPI dependency."""
    if get_settings().USE_MOCK_INTEGRATIONS:
        return _mock_client
    raise not_configured(
        "background_check", "Background checks have no provider yet (open decision Topic 6)."
    )
