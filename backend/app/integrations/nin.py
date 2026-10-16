"""NIN verification provider client (caregiver vetting, Day 6; PRD trust and safety).

The provider is open decision Topic 6 (e.g. NIMC via a licensed verifier such as
Prembly, Dojah or VerifyMe). Until it is chosen, only the mock exists.

Used by caregivers.service: `await client.verify(nin, full_name=...)` returns a
NINResult with `passed`, `reference` and a short `detail`; provider outages raise
IntegrationError (a ServiceUnavailableError), which the caller records as `error`.

A NIN is sensitive personal data under the NDPA: results carry the last 4 digits only,
and nothing here stores or logs the full number.

Mock rules (by the NIN's last 4 digits), so every outcome can be demoed and tested:
    0000   no record found                  -> passed False
    1111   record found, name doesn't match -> passed False
    9999   provider outage                  -> IntegrationError
    any other NIN: match                    -> passed True
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.config import get_settings
from app.integrations.base import IntegrationError, not_configured

_NIN = re.compile(r"^\d{11}$")


class InvalidNIN(ValueError):
    """Not 11 digits. Raised before any provider call."""


@dataclass(frozen=True, slots=True)
class NINResult:
    passed: bool  # a record exists and the name matches
    reference: str  # the provider's reference; the NIN can't be recovered from it
    detail: str  # short and non-sensitive, safe to store on the check
    nin_last4: str


class NINClient(Protocol):
    name: str

    async def verify(self, nin: str, *, full_name: str) -> NINResult:
        """Check the NIN against the national register. Raises IntegrationError on outage."""
        ...


def clean_nin(nin: str) -> str:
    digits = re.sub(r"[\s-]", "", nin)
    if not _NIN.match(digits):
        raise InvalidNIN("A NIN is 11 digits")
    return digits


@dataclass
class MockNINClient:
    name: str = "mock"
    lookups: int = field(default=0)  # tests check the provider was (or wasn't) called

    async def verify(self, nin: str, *, full_name: str) -> NINResult:
        self.lookups += 1
        digits = clean_nin(nin)
        last4 = digits[-4:]
        if last4 == "9999":
            raise IntegrationError(self.name, "simulated NIN provider outage")
        reference = "mock-nin-" + hashlib.sha256(digits.encode()).hexdigest()[:12]
        if last4 == "0000":
            return NINResult(False, reference, "No record found for this NIN", last4)
        if last4 == "1111":
            return NINResult(False, reference, "NIN record name does not match", last4)
        return NINResult(True, reference, "NIN record matches", last4)


_mock_client = MockNINClient()


def get_nin_client() -> NINClient:
    """FastAPI dependency."""
    if get_settings().USE_MOCK_INTEGRATIONS:
        return _mock_client
    raise not_configured("nin", "NIN verification has no provider yet (open decision Topic 6).")
