"""Background check provider client (caregiver vetting, Day 6).

The provider is open decision Topic 6. Until it is chosen, only the mock exists.

Used by caregivers.service: `await client.verify(full_name=..., phone=...)` returns a
BackgroundCheckResult with `passed`, `reference` and a short `detail`. Results carry an
outcome only, never the provider's full report: anything adverse goes to a human in
the ops console, and approval is always a human decision (PRD section 6). Provider
outages raise IntegrationError (a ServiceUnavailableError).

When a real provider is chosen: most run checks asynchronously. Its client can submit
here and return `passed=False, detail="pending"`, or the caregivers flow can grow a
webhook; decide with the caregivers owner.

Mock rules (by the subject's name, case-insensitive), deterministic for tests and demos:
    contains "flagged"   failed: a record needs human review
    contains "outage"    provider outage (IntegrationError)
    anything else        passed
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from app.config import get_settings
from app.integrations.base import IntegrationError, not_configured


@dataclass(frozen=True, slots=True)
class BackgroundCheckResult:
    passed: bool
    reference: str
    detail: str  # short and non-sensitive; never the provider's report


class BackgroundCheckClient(Protocol):
    name: str

    async def verify(self, *, full_name: str, phone: str) -> BackgroundCheckResult:
        """Run a check on this person. Raises IntegrationError on outage."""
        ...


@dataclass
class MockBackgroundCheckClient:
    name: str = "mock"
    lookups: int = field(default=0)  # tests check the provider was (or wasn't) called

    async def verify(self, *, full_name: str, phone: str) -> BackgroundCheckResult:
        self.lookups += 1
        lowered = full_name.lower()
        if "outage" in lowered:
            raise IntegrationError(self.name, "simulated background-check outage")
        seed = f"{full_name}|{phone}".encode()
        reference = "mock-bg-" + hashlib.sha256(seed).hexdigest()[:12]
        if "flagged" in lowered:
            return BackgroundCheckResult(False, reference, "Record found that needs human review")
        return BackgroundCheckResult(True, reference, "No records found")


_mock_client = MockBackgroundCheckClient()


def get_background_check_client() -> BackgroundCheckClient:
    """FastAPI dependency."""
    if get_settings().USE_MOCK_INTEGRATIONS:
        return _mock_client
    raise not_configured(
        "background_check", "Background checks have no provider yet (open decision Topic 6)."
    )
