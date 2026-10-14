"""NIN verification provider client (caregiver vetting, Day 6; PRD trust and safety).

The provider is open decision Topic 6 (e.g. NIMC via a licensed verifier such as
Prembly, Dojah or VerifyMe). Until it is chosen, only the mock exists.

A NIN is sensitive personal data under the NDPA: results carry the last 4 digits only,
and callers must never store or log the full number.

Mock rules (by the NIN's last digit), so every outcome can be demoed and tested:
    0   no record found
    1   record found, name doesn't match
    2   record found, date of birth doesn't match
    9   provider error (IntegrationError)
    any other digit: full match
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from app.config import get_settings
from app.integrations.base import IntegrationError, not_configured

_NIN = re.compile(r"^\d{11}$")


class InvalidNIN(ValueError):
    """Not 11 digits. Raised before any provider call."""


@dataclass(frozen=True, slots=True)
class NINResult:
    found: bool
    name_matches: bool
    dob_matches: bool
    nin_last4: str
    provider: str
    provider_ref: str

    @property
    def verified(self) -> bool:
        return self.found and self.name_matches and self.dob_matches


class NINClient(Protocol):
    async def verify(
        self, nin: str, *, first_name: str, last_name: str, date_of_birth: date
    ) -> NINResult:
        """Check the NIN against the national register. Raises IntegrationError on failure."""
        ...


def clean_nin(nin: str) -> str:
    digits = re.sub(r"[\s-]", "", nin)
    if not _NIN.match(digits):
        raise InvalidNIN("A NIN is 11 digits")
    return digits


class MockNINClient:
    provider = "mock"

    async def verify(
        self, nin: str, *, first_name: str, last_name: str, date_of_birth: date
    ) -> NINResult:
        digits = clean_nin(nin)
        last = digits[-1]
        if last == "9":
            raise IntegrationError(self.provider, "simulated NIN provider outage")
        found = last != "0"
        return NINResult(
            found=found,
            name_matches=found and last != "1",
            dob_matches=found and last != "2",
            nin_last4=digits[-4:],
            provider=self.provider,
            # Stable per NIN, but the NIN can't be recovered from it.
            provider_ref="mock-nin-" + hashlib.sha256(digits.encode()).hexdigest()[:12],
        )


_mock_client = MockNINClient()


def get_nin_client() -> NINClient:
    """FastAPI dependency."""
    if get_settings().USE_MOCK_INTEGRATIONS:
        return _mock_client
    raise not_configured("nin", "NIN verification has no provider yet (open decision Topic 6).")
