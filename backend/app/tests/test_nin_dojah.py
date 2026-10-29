"""Tests: NIN verification through Dojah (ADR 0006).

Dojah is never called for real here: an httpx.MockTransport stands in for it and answers
the way the Dojah docs describe (lookup-nin: 200 with an entity, 404, 400, 401, 402, 424,
429, 5xx). The end-to-end tests drive POST /caregivers/{id}/verify with that client.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable, Iterator

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.config import Settings, get_settings
from app.core.exceptions import ServiceUnavailableError
from app.core.permissions import UserRole
from app.domains.auth.dependencies import get_current_user
from app.domains.auth.models import User
from app.domains.caregivers.models import VerificationCheck
from app.integrations.background_check import MockBackgroundCheckClient, get_background_check_client
from app.integrations.base import IntegrationError
from app.integrations.nin import (
    DojahNINClient,
    InvalidNIN,
    MockNINClient,
    get_nin_client,
    names_match,
)
from app.main import app

NIN = "70123456789"  # Dojah's sandbox test NIN
APP_ID = "app-123"
SECRET = "sk_test_abc"
RECORD = {
    "first_name": "NGOZI",
    "middle_name": "ADAEZE",
    "last_name": "EZE",
    "gender": "Female",
    "photo": "/9j/4AAQSkZJRgABAgAAAQABAAD",
    "date_of_birth": "1990-04-12",
    "phone_number": "08031112222",
    "employment_status": "employed",
    "marital_status": "Married",
}


def dojah_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "DOJAH_APP_ID": APP_ID,
        "DOJAH_SECRET_KEY": SecretStr(SECRET),
        "SECRET_KEY": SecretStr("x" * 48),
        **overrides,
    }
    return get_settings().model_copy(update=values)


def answering(status: int, body: object = None, seen: list[httpx.Request] | None = None):
    """A MockTransport that answers every request with `status` and `body`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.MockTransport(handler)


def client(status: int = 200, body: object = None, seen=None, **settings) -> DojahNINClient:
    if body is None and status == 200:
        body = {"entity": RECORD}
    return DojahNINClient(dojah_settings(**settings), transport=answering(status, body, seen))


# ================================================================ name matching
@pytest.mark.parametrize(
    "account_name",
    [
        "Ngozi Eze",
        "ngozi eze",
        "Eze Ngozi",  # surname first, as on many forms
        "Ngozi Adaeze Eze",
        "  NGOZI   EZE  ",
        "Adaeze Eze",  # goes by her middle name
        "Ngozi Eze-",
    ],
)
def test_names_that_match(account_name: str) -> None:
    assert names_match(account_name, "NGOZI", "EZE", "ADAEZE")


@pytest.mark.parametrize(
    "account_name",
    [
        "Ngozi",  # one name only
        "Eze",
        "Ngozi Okafor",  # wrong surname
        "Chioma Eze",  # wrong first name
        "Adaeze Ngozi",  # no surname
        "",
        "123 456",
    ],
)
def test_names_that_dont_match(account_name: str) -> None:
    assert not names_match(account_name, "NGOZI", "EZE", "ADAEZE")


def test_name_matching_ignores_accents_and_punctuation() -> None:
    assert names_match("Ụzọamaka Ọkafọr", "UZOAMAKA", "OKAFOR")
    assert names_match("Tobi O'Neil", "TOBI", "ONEIL")
    assert names_match("Chidi Okafor-Eze", "CHIDI", "OKAFOR-EZE")
    assert names_match("Chidi Okafor Eze", "CHIDI", "OKAFOR EZE")


def test_a_record_without_first_or_last_name_never_matches() -> None:
    assert not names_match("Ngozi Eze", "", "EZE")
    assert not names_match("Ngozi Eze", "NGOZI", "")


# ================================================================ the HTTP call
async def test_lookup_sends_the_documented_request() -> None:
    seen: list[httpx.Request] = []
    await client(seen=seen).verify(NIN, full_name="Ngozi Eze")
    [request] = seen
    assert request.method == "GET"
    assert str(request.url) == f"https://sandbox.dojah.io/api/v1/kyc/nin?nin={NIN}"
    assert request.headers["AppId"] == APP_ID
    assert request.headers["Authorization"] == SECRET  # raw key, no "Bearer"


async def test_live_base_url_is_used_when_set() -> None:
    seen: list[httpx.Request] = []
    dojah = client(seen=seen, DOJAH_BASE_URL="https://api.dojah.io/")
    assert dojah.sandbox is False
    await dojah.verify(NIN, full_name="Ngozi Eze")
    assert str(seen[0].url).startswith("https://api.dojah.io/api/v1/kyc/nin?")


async def test_a_matching_record_passes() -> None:
    result = await client().verify(NIN, full_name="Ngozi Eze")
    assert (result.passed, result.detail, result.nin_last4) == (True, "NIN record matches", "6789")
    assert result.reference.startswith("dojah-")


async def test_a_record_for_someone_else_fails() -> None:
    result = await client().verify(NIN, full_name="Chioma Okafor")
    assert (result.passed, result.detail) == (False, "NIN record name does not match")


async def test_no_record_fails_without_an_error() -> None:
    result = await client(404, {"error": "NIN not found"}).verify(NIN, full_name="Ngozi Eze")
    assert (result.passed, result.detail) == (False, "No record found for this NIN")


@pytest.mark.parametrize(
    ("status", "message"),
    [(400, "HTTP 400"), (401, "credentials rejected"), (402, "wallet balance too low")],
)
async def test_refusals_are_errors_that_wont_fix_themselves(status: int, message: str) -> None:
    with pytest.raises(IntegrationError, match=message) as exc:
        await client(status, {"error": "refused"}).verify(NIN, full_name="Ngozi Eze")
    assert exc.value.retryable is False


@pytest.mark.parametrize("status", [424, 429, 500, 502, 503])
async def test_outages_are_errors_worth_retrying(status: int) -> None:
    with pytest.raises(IntegrationError) as exc:
        await client(status, {"error": "unavailable"}).verify(NIN, full_name="Ngozi Eze")
    assert exc.value.retryable is True
    assert isinstance(exc.value, ServiceUnavailableError)


async def test_network_failure_is_an_outage() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    dojah = DojahNINClient(dojah_settings(), transport=httpx.MockTransport(boom))
    with pytest.raises(IntegrationError, match="unreachable") as exc:
        await dojah.verify(NIN, full_name="Ngozi Eze")
    assert exc.value.retryable is True


@pytest.mark.parametrize(
    "body", ["<html>gateway</html>", {"data": {}}, {"entity": None}, {"entity": "x"}, []]
)
async def test_unexpected_responses_are_errors(body: object) -> None:
    with pytest.raises(IntegrationError):
        await client(200, body).verify(NIN, full_name="Ngozi Eze")


async def test_a_bad_nin_never_reaches_dojah() -> None:
    seen: list[httpx.Request] = []
    for bad in ("1234", "abcdefghijk", "123456789012", ""):
        with pytest.raises(InvalidNIN):
            await client(seen=seen).verify(bad, full_name="Ngozi Eze")
    assert seen == []


async def test_spaces_and_dashes_in_the_nin_are_dropped() -> None:
    seen: list[httpx.Request] = []
    await client(seen=seen).verify("701-2345 6789", full_name="Ngozi Eze")
    assert seen[0].url.params["nin"] == NIN


async def test_reference_is_stable_but_does_not_reveal_the_nin() -> None:
    dojah = client()
    first = await dojah.verify(NIN, full_name="Ngozi Eze")
    again = await dojah.verify(NIN, full_name="Ngozi Eze")
    other = await dojah.verify("70123456780", full_name="Ngozi Eze")
    assert first.reference == again.reference != other.reference
    assert NIN not in first.reference
    # Keyed with SECRET_KEY: a different deployment gives a different reference.
    elsewhere = await client(SECRET_KEY=SecretStr("y" * 48)).verify(NIN, full_name="Ngozi Eze")
    assert elsewhere.reference != first.reference


async def test_nothing_personal_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    await client().verify(NIN, full_name="Ngozi Eze")
    with pytest.raises(IntegrationError):
        await client(402).verify(NIN, full_name="Ngozi Eze")
    logged = caplog.text
    for secret in (NIN, SECRET, RECORD["date_of_birth"], RECORD["phone_number"], RECORD["photo"]):
        assert secret not in logged


def test_client_needs_both_keys() -> None:
    for missing in ({"DOJAH_APP_ID": None}, {"DOJAH_SECRET_KEY": None}):
        with pytest.raises(ValueError, match="DOJAH_APP_ID and DOJAH_SECRET_KEY"):
            DojahNINClient(dojah_settings(**missing))


# ================================================================ choosing the client
@pytest.fixture
def settings_patch(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    def _set(**values: object) -> None:
        for key, value in values.items():
            monkeypatch.setattr(get_settings(), key, value)

    return _set


def test_provider_follows_mocks_when_unset(settings_patch) -> None:
    settings_patch(NIN_PROVIDER=None, USE_MOCK_INTEGRATIONS=True)
    assert isinstance(get_nin_client(), MockNINClient)


def test_dojah_can_run_while_other_partners_are_mocked(settings_patch) -> None:
    settings_patch(
        NIN_PROVIDER="dojah",
        USE_MOCK_INTEGRATIONS=True,
        DOJAH_APP_ID=APP_ID,
        DOJAH_SECRET_KEY=SecretStr(SECRET),
    )
    assert isinstance(get_nin_client(), DojahNINClient)


def test_mock_can_be_forced_with_real_partners(settings_patch) -> None:
    settings_patch(NIN_PROVIDER="mock", USE_MOCK_INTEGRATIONS=False)
    assert isinstance(get_nin_client(), MockNINClient)


def test_real_partners_use_dojah(settings_patch) -> None:
    settings_patch(
        NIN_PROVIDER=None,
        USE_MOCK_INTEGRATIONS=False,
        DOJAH_APP_ID=APP_ID,
        DOJAH_SECRET_KEY=SecretStr(SECRET),
    )
    assert isinstance(get_nin_client(), DojahNINClient)


def test_dojah_without_keys_is_a_clear_503(settings_patch) -> None:
    settings_patch(NIN_PROVIDER="dojah", DOJAH_APP_ID=None, DOJAH_SECRET_KEY=None)
    with pytest.raises(ServiceUnavailableError) as exc:
        get_nin_client()
    assert exc.value.code == "nin_not_configured"
    assert "DOJAH_APP_ID" in str(exc.value)


# ================================================================ start-up guards
DEPLOYED = {
    "SECRET_KEY": "s" * 48,
    "FIELD_ENCRYPTION_KEY": "2wLXDuyvc1lMRvbSZRPLdzqNplpi3vsiHEFhnIUM2yo=",
    "CORS_ORIGINS": "https://app.carekin.ng",
    "LLM_PROVIDER": "ollama",
    "SMS_PROVIDER": "termii",
    "SMS_PROVIDER_API_KEY": "k",
    "SMS_PROVIDER_BASE_URL": "https://api.termii.example",
}


def make_settings(**env: str) -> Settings:
    return Settings(_env_file=None, **{**DEPLOYED, **env})


def test_staging_refuses_dojah_without_keys() -> None:
    with pytest.raises(ValueError, match="DOJAH_APP_ID and DOJAH_SECRET_KEY"):
        make_settings(ENVIRONMENT="staging", NIN_PROVIDER="dojah")


def test_production_refuses_the_dojah_sandbox() -> None:
    keys = {"NIN_PROVIDER": "dojah", "DOJAH_APP_ID": APP_ID, "DOJAH_SECRET_KEY": SECRET}
    with pytest.raises(ValueError, match="sandbox in production"):
        make_settings(ENVIRONMENT="production", **keys)
    live = make_settings(ENVIRONMENT="production", DOJAH_BASE_URL="https://api.dojah.io", **keys)
    assert live.nin_provider == "dojah"
    # Staging may use the sandbox.
    assert make_settings(ENVIRONMENT="staging", **keys).nin_provider == "dojah"


def test_blank_provider_settings_mean_unset() -> None:
    settings = Settings(_env_file=None, SMS_PROVIDER="", NIN_PROVIDER="  ")
    assert (settings.SMS_PROVIDER, settings.NIN_PROVIDER) == (None, None)
    assert settings.nin_provider == "mock"


# ================================================================ end to end
@pytest.fixture
def act_as() -> Iterator[Callable[[User], None]]:
    def _act(user: User) -> None:
        app.dependency_overrides[get_current_user] = lambda: user

    yield _act
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def people(db):
    numbers = itertools.count(1)

    async def _make(role: UserRole, name: str) -> User:
        user = User(phone=f"+23480377700{next(numbers):02d}", full_name=name, role=role)
        db.add(user)
        await db.commit()
        return user

    return _make


@pytest.fixture
def use_dojah() -> Iterator[Callable[..., list[httpx.Request]]]:
    """Point the verify route at a Dojah client that answers with `status` and `body`."""

    def _use(status: int = 200, body: object = None) -> list[httpx.Request]:
        seen: list[httpx.Request] = []
        dojah = client(status, body, seen)
        app.dependency_overrides[get_nin_client] = lambda: dojah
        return seen

    app.dependency_overrides[get_background_check_client] = MockBackgroundCheckClient
    yield _use
    app.dependency_overrides.pop(get_nin_client, None)
    app.dependency_overrides.pop(get_background_check_client, None)


async def applicant(api, act_as, people, name: str) -> str:
    act_as(await people(UserRole.CAREGIVER, name))
    guarantor = {"full_name": "Ada Obi", "relationship": "Pastor", "address": "12 Ogui Road, Enugu"}
    r = await api.post(
        "/caregivers",
        json={
            "tier": "companion",
            "guarantors": [
                {**guarantor, "phone": "08031230001"},
                {**guarantor, "phone": "08031230002"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_ops_verifies_a_caregiver_through_dojah(api, db, act_as, people, use_dojah) -> None:
    caregiver_id = await applicant(api, act_as, people, "Ngozi Eze")
    seen = use_dojah()
    act_as(await people(UserRole.OPS, "Ops Desk"))
    r = await api.post(f"/caregivers/{caregiver_id}/verify", json={"nin": NIN})
    assert r.status_code == 200, r.text
    checks = {c["check_type"]: c["status"] for c in r.json()["checks"]}
    assert checks == {"nin": "passed", "background": "passed"}
    assert len(seen) == 1
    assert NIN not in r.text

    # Ops can now approve: both checks passed and there are two guarantors.
    r = await api.post(f"/ops/caregivers/{caregiver_id}/approve", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    # Only the outcome is stored: no NIN, and nothing from the NIMC record.
    stored = (await db.scalars(select(VerificationCheck))).all()
    text = " ".join(f"{c.provider_ref} {c.result}" for c in stored)
    for leaked in (NIN, RECORD["date_of_birth"], RECORD["phone_number"], RECORD["photo"], "ADAEZE"):
        assert leaked not in text


async def test_a_name_mismatch_blocks_approval(api, act_as, people, use_dojah) -> None:
    caregiver_id = await applicant(api, act_as, people, "Chioma Okafor")
    use_dojah()
    act_as(await people(UserRole.OPS, "Ops Desk"))
    r = await api.post(f"/caregivers/{caregiver_id}/verify", json={"checks": ["nin"], "nin": NIN})
    assert r.json()["checks"][0]["status"] == "failed"
    r = await api.post(f"/ops/caregivers/{caregiver_id}/approve", json={"decision": "approve"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "caregiver_not_ready"


@pytest.mark.parametrize(("status", "outcome"), [(404, "failed"), (402, "error"), (424, "error")])
async def test_dojah_answers_are_recorded(api, act_as, people, use_dojah, status, outcome) -> None:
    caregiver_id = await applicant(api, act_as, people, "Ngozi Eze")
    use_dojah(status, {"error": "x"})
    act_as(await people(UserRole.OPS, "Ops Desk"))
    r = await api.post(f"/caregivers/{caregiver_id}/verify", json={"checks": ["nin"], "nin": NIN})
    assert r.status_code == 200, r.text
    assert r.json()["checks"][0]["status"] == outcome


async def test_an_outage_can_be_retried(api, act_as, people, use_dojah) -> None:
    caregiver_id = await applicant(api, act_as, people, "Ngozi Eze")
    act_as(await people(UserRole.OPS, "Ops Desk"))
    url = f"/caregivers/{caregiver_id}/verify"
    use_dojah(424, {"error": "NIMC unavailable"})
    assert (await api.post(url, json={"checks": ["nin"], "nin": NIN})).json()["checks"][0][
        "status"
    ] == "error"
    use_dojah()
    assert (await api.post(url, json={"checks": ["nin"], "nin": NIN})).json()["checks"][0][
        "status"
    ] == "passed"
