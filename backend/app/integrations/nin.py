"""NIN verification provider client (caregiver vetting, Day 6; PRD trust and safety).

The provider is Dojah (ADR 0006): GET {DOJAH_BASE_URL}/api/v1/kyc/nin?nin=... returns the
NIMC record, and we pass the check when the record's first and last names match the
caregiver's account name. The Dojah sandbox (https://sandbox.dojah.io) is free and
returns simulated records; live calls are charged to the Dojah wallet.

Used by caregivers.service: `await client.verify(nin, full_name=...)` returns a
NINResult with `passed`, `reference` and a short `detail`; provider outages raise
IntegrationError (a ServiceUnavailableError), which the caller records as `error`.

Which client runs is NIN_PROVIDER ("mock" or "dojah"). Unset, it follows
USE_MOCK_INTEGRATIONS, so a demo can call the Dojah sandbox while payments stay mocked.

A NIN is sensitive personal data under the NDPA: results carry the last 4 digits only,
nothing here stores or logs the full number, and the record Dojah returns (photo, date
of birth, phone) is read for the name match and then dropped.

Mock rules (by the NIN's last 4 digits), so every outcome can be demoed and tested:
    0000   no record found                  -> passed False
    1111   record found, name doesn't match -> passed False
    9999   provider outage                  -> IntegrationError
    any other NIN: match                    -> passed True
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.config import Settings, get_settings
from app.integrations.base import IntegrationError, not_configured

logger = logging.getLogger(__name__)

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


# ------------------------------------------------------------------ name matching
def _name_tokens(name: str) -> set[str]:
    """Lower-case words without accents or punctuation: "Ụzọ-Amaka O'Neil" -> {uzo, amaka, oneil}."""
    plain = unicodedata.normalize("NFKD", name)
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch)).casefold()
    plain = re.sub(r"['\u2019`.]", "", plain)
    return {token for token in re.split(r"[^a-z]+", plain) if token}


def names_match(full_name: str, first_name: str, last_name: str, middle_name: str = "") -> bool:
    """Does the account name belong to the NIMC record?

    Passes when the record's first and last names are both in the account name, in any
    order, so "Eze Ngozi" and "Ngozi Adaeze Eze" match NGOZI / EZE. Also passes when
    every word of a two-or-more-word account name is in the record and the surname is
    among them, for people who go by their middle name ("Adaeze Eze").
    Anything else fails, and ops reviews the caregiver by hand.
    """
    account = _name_tokens(full_name)
    first, last = _name_tokens(first_name), _name_tokens(last_name)
    record = first | last | _name_tokens(middle_name)
    if not account or not first or not last:
        return False
    if first <= account and last <= account:
        return True
    return len(account) >= 2 and account <= record and bool(last & account)


# ------------------------------------------------------------------ Dojah
DOJAH_SANDBOX_URL = "https://sandbox.dojah.io"


class DojahNINClient:
    """Dojah KYC lookup: GET {base_url}/api/v1/kyc/nin with AppId and Authorization headers.

    Responses (https://docs.dojah.io/api-reference/individual-verification/nigeria/lookup-nin):
        200 {"entity": {first_name, last_name, middle_name, ...}}  -> name match decides
        404 no record                                                -> passed False
        400 bad NIN, 401 bad keys, 402 empty wallet                  -> IntegrationError, no retry
        424 NIMC unavailable, 429 rate limited, 5xx, network errors  -> IntegrationError, retry
    """

    name = "dojah"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        if not (settings.DOJAH_APP_ID and settings.DOJAH_SECRET_KEY):
            raise ValueError("Dojah needs DOJAH_APP_ID and DOJAH_SECRET_KEY")
        self._url = settings.DOJAH_BASE_URL.rstrip("/") + "/api/v1/kyc/nin"
        self._headers = {
            "AppId": settings.DOJAH_APP_ID,
            # Dojah takes the secret key as is, without "Bearer".
            "Authorization": settings.DOJAH_SECRET_KEY.get_secret_value(),
            "Accept": "application/json",
        }
        self._timeout = settings.INTEGRATION_TIMEOUT_SECONDS
        self._transport = transport
        # Keyed, so the NIN can't be recovered from a stored reference by trying all
        # 10^11 possible numbers.
        self._ref_key = settings.SECRET_KEY.get_secret_value().encode()
        self.sandbox = self._url.startswith(DOJAH_SANDBOX_URL)

    def _reference(self, digits: str) -> str:
        digest = hmac.new(self._ref_key, digits.encode(), hashlib.sha256).hexdigest()[:16]
        return f"dojah-{digest}"

    async def verify(self, nin: str, *, full_name: str) -> NINResult:
        digits = clean_nin(nin)
        last4 = digits[-4:]
        reference = self._reference(digits)
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                response = await http.get(self._url, params={"nin": digits}, headers=self._headers)
        except httpx.HTTPError as exc:
            raise IntegrationError(self.name, f"unreachable ({type(exc).__name__})") from exc

        status = response.status_code
        if status == 404:
            return NINResult(False, reference, "No record found for this NIN", last4)
        if status == 402:
            logger.error("Dojah wallet is empty: NIN checks will fail until it is funded")
            raise IntegrationError(self.name, "wallet balance too low", retryable=False)
        if status == 401:
            raise IntegrationError(self.name, "credentials rejected", retryable=False)
        if status == 400:
            raise IntegrationError(self.name, "request refused (HTTP 400)", retryable=False)
        if status != 200:
            # 424: NIMC is down, 429: rate limited, 5xx: Dojah is down. Worth retrying later.
            raise IntegrationError(self.name, f"HTTP {status}")

        entity = self._entity(response)
        matched = names_match(
            full_name,
            str(entity.get("first_name") or ""),
            str(entity.get("last_name") or ""),
            str(entity.get("middle_name") or ""),
        )
        # The record (photo, date of birth, phone) goes no further than this function.
        detail = "NIN record matches" if matched else "NIN record name does not match"
        return NINResult(matched, reference, detail, last4)

    def _entity(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise IntegrationError(self.name, "non-JSON response") from exc
        entity = data.get("entity") if isinstance(data, dict) else None
        if not isinstance(entity, dict):
            raise IntegrationError(self.name, "unexpected response shape")
        return entity


_mock_client = MockNINClient()


def get_nin_client() -> NINClient:
    """FastAPI dependency: the mock or Dojah, by NIN_PROVIDER (see the module docstring)."""
    settings = get_settings()
    if settings.nin_provider == "mock":
        return _mock_client
    try:
        return DojahNINClient(settings)
    except ValueError as exc:
        raise not_configured("nin", f"NIN verification is not configured ({exc}).") from exc
