"""Tests for the caregivers domain (Day 6): onboarding, certifications, availability, verification.

Users are created directly in the database and the app is told who is calling
by overriding get_current_user, so these tests do not depend on the OTP login
flow (auth has its own tests). Role checks, ownership rules, audit entries and
the database all run for real.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.permissions import UserRole
from app.domains.auth.dependencies import get_current_user
from app.domains.auth.models import User
from app.domains.caregivers import schemas as s
from app.domains.caregivers.models import Caregiver, CaregiverStatus, VerificationCheck
from app.integrations.background_check import (
    MockBackgroundCheckClient,
    get_background_check_client,
)
from app.integrations.document_store import MockDocumentStore, get_document_store
from app.integrations.nin import MockNINClient, get_nin_client
from app.main import app

PDF = b"%PDF-1.4 test certificate"
NIN = "12345678901"


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def as_user() -> Iterator[Callable[[User], None]]:
    """Make the next requests come from `user` (skips the login flow)."""

    def _act(user: User) -> None:
        app.dependency_overrides[get_current_user] = lambda: user

    yield _act
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
async def make_user(db: AsyncSession) -> AsyncIterator[Callable[..., Awaitable[User]]]:
    numbers = itertools.count(1)

    async def _make(role: UserRole, name: str = "Test User") -> User:
        user = User(phone=f"+23480312345{next(numbers):02d}", full_name=name, role=role)
        db.add(user)
        await db.commit()
        return user

    yield _make


@pytest.fixture
def nin_mock() -> Iterator[MockNINClient]:
    mock = MockNINClient()
    app.dependency_overrides[get_nin_client] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_nin_client, None)


@pytest.fixture
def background_mock() -> Iterator[MockBackgroundCheckClient]:
    mock = MockBackgroundCheckClient()
    app.dependency_overrides[get_background_check_client] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_background_check_client, None)


@pytest.fixture
def store() -> Iterator[MockDocumentStore]:
    mock = MockDocumentStore()
    app.dependency_overrides[get_document_store] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_document_store, None)


# ----------------------------------------------------------------- helpers
def guarantor(phone: str) -> dict[str, str]:
    return {
        "full_name": "Ada Obi",
        "phone": phone,
        "relationship": "Pastor",
        "address": "12 Ogui Road, Enugu",
    }


def application(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "tier": "companion",
        "guarantors": [guarantor("08031230001"), guarantor("08031230002")],
    }
    body.update(overrides)
    return body


def error_code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


async def apply_as(
    api: httpx.AsyncClient, as_user: Callable[[User], None], user: User, **overrides: object
) -> dict:
    as_user(user)
    response = await api.post("/caregivers", json=application(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


async def audit_actions(db: AsyncSession) -> list[str]:
    return list(await db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


async def approve(db: AsyncSession, caregiver_id: str) -> None:
    await db.execute(
        update(Caregiver)
        .where(Caregiver.id == uuid.UUID(caregiver_id))
        .values(status=CaregiverStatus.APPROVED)
    )
    await db.commit()


# ------------------------------------------------------- schema unit tests
def test_guarantor_phones_are_normalised() -> None:
    body = s.CaregiverCreateRequest(
        tier="companion", guarantors=[guarantor("08031230001"), guarantor("08031230002")]
    )
    assert body.guarantors[0].phone == "+2348031230001"


@pytest.mark.parametrize(
    "guarantors",
    [
        [guarantor("08031230001")],
        [guarantor("08031230001"), guarantor("0803 123 0001")],
        [guarantor("08031230001"), guarantor("08031230002"), guarantor("08031230003")],
    ],
    ids=["one", "same phone twice", "three"],
)
def test_exactly_two_different_guarantors_required(guarantors: list[dict[str, str]]) -> None:
    with pytest.raises(ValidationError):
        s.CaregiverCreateRequest(tier="companion", guarantors=guarantors)


def test_availability_window_must_end_after_it_starts() -> None:
    with pytest.raises(ValidationError):
        s.AvailabilityWindowIn(day_of_week=0, start_time="17:00", end_time="09:00")


def test_nursing_licence_needs_a_licence_number() -> None:
    with pytest.raises(ValidationError):
        s.CertificationCreateRequest(cert_type="nursing_licence", title="RN")


@pytest.mark.parametrize(
    "payload",
    [
        {"checks": ["guarantor"]},
        {"checks": ["nin"]},
        {"checks": ["background"], "nin": NIN},
        {"checks": ["nin"], "nin": "123"},
    ],
    ids=["unsupported check", "nin missing", "nin without nin check", "nin not 11 digits"],
)
def test_verify_request_rules(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        s.VerifyRequest(**payload)


# ------------------------------------------------------------ POST /caregivers
async def test_caregiver_can_apply(api, as_user, make_user, db) -> None:
    user = await make_user(UserRole.CAREGIVER)
    body = await apply_as(api, as_user, user, bio="Five years of home care")
    assert body["status"] == "applied"
    assert body["tier"] == "companion"
    assert len(body["guarantors"]) == 2
    assert body["user_id"] == str(user.id)
    assert "caregiver.create" in await audit_actions(db)


async def test_location_round_trips(api, as_user, make_user) -> None:
    user = await make_user(UserRole.CAREGIVER)
    body = await apply_as(
        api, as_user, user, base_latitude=6.4584, base_longitude=7.5464, service_radius_km=15
    )
    assert body["base_latitude"] == pytest.approx(6.4584)
    assert body["base_longitude"] == pytest.approx(7.5464)


async def test_only_caregivers_can_apply(api, as_user, make_user) -> None:
    patient = await make_user(UserRole.PATIENT)
    as_user(patient)
    response = await api.post("/caregivers", json=application())
    assert response.status_code == 403
    assert error_code(response) == "role_forbidden"


async def test_second_application_is_a_conflict(api, as_user, make_user) -> None:
    user = await make_user(UserRole.CAREGIVER)
    await apply_as(api, as_user, user)
    response = await api.post("/caregivers", json=application())
    assert response.status_code == 409
    assert error_code(response) == "caregiver_exists"


async def test_application_needs_two_guarantors(api, as_user, make_user) -> None:
    as_user(await make_user(UserRole.CAREGIVER))
    response = await api.post("/caregivers", json=application(guarantors=[guarantor("08031230001")]))
    assert response.status_code == 422


async def test_guarantor_cannot_be_the_applicant(api, as_user, make_user) -> None:
    user = await make_user(UserRole.CAREGIVER)  # phone +2348031234501
    as_user(user)
    own_number = user.phone.replace("+234", "0")
    response = await api.post(
        "/caregivers", json=application(guarantors=[guarantor(own_number), guarantor("08031230002")])
    )
    assert response.status_code == 400
    assert error_code(response) == "guarantor_is_self"


# ------------------------------------------------------ GET /caregivers/{id}
async def test_owner_sees_the_full_profile(api, as_user, make_user) -> None:
    user = await make_user(UserRole.CAREGIVER)
    created = await apply_as(api, as_user, user)
    response = await api.get(f"/caregivers/{created['id']}")
    assert response.status_code == 200
    assert len(response.json()["guarantors"]) == 2


async def test_ops_sees_the_full_profile(api, as_user, make_user) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    as_user(await make_user(UserRole.OPS))
    response = await api.get(f"/caregivers/{created['id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "applied"


async def test_unapproved_caregiver_is_hidden_from_others(api, as_user, make_user) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    as_user(await make_user(UserRole.PATIENT))
    response = await api.get(f"/caregivers/{created['id']}")
    assert response.status_code == 404
    assert error_code(response) == "caregiver_not_found"


async def test_approved_caregiver_shows_a_public_profile(api, as_user, make_user, db) -> None:
    created = await apply_as(
        api, as_user, await make_user(UserRole.CAREGIVER, name="Chi Okeke"), bio="Hello"
    )
    await approve(db, created["id"])
    as_user(await make_user(UserRole.PATIENT))
    response = await api.get(f"/caregivers/{created['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Chi Okeke"
    assert body["bio"] == "Hello"
    for private in ("guarantors", "user_id", "status", "verification_checks", "base_latitude"):
        assert private not in body


async def test_unknown_caregiver_is_not_found(api, as_user, make_user) -> None:
    as_user(await make_user(UserRole.PATIENT))
    response = await api.get(f"/caregivers/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------- PATCH /caregivers/{id}
async def test_owner_can_update_the_profile(api, as_user, make_user, db) -> None:
    user = await make_user(UserRole.CAREGIVER)
    created = await apply_as(api, as_user, user)
    response = await api.patch(
        f"/caregivers/{created['id']}",
        json={"bio": "Secret bio text", "hourly_rate_minor": 250000, "languages": ["Igbo", " english "]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["bio"] == "Secret bio text"
    assert body["hourly_rate_minor"] == 250000
    assert body["languages"] == ["igbo", "english"]
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "caregiver.update"))
    assert entry is not None
    assert set(entry.meta["fields"]) == {"bio", "hourly_rate_minor", "languages"}
    assert "Secret bio text" not in str(entry.meta)  # field names only, never values


async def test_another_caregiver_cannot_update_the_profile(api, as_user, make_user) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    as_user(await make_user(UserRole.CAREGIVER))
    response = await api.patch(f"/caregivers/{created['id']}", json={"bio": "Hijacked"})
    assert response.status_code == 403
    assert error_code(response) == "not_your_profile"


async def test_ops_can_update_a_profile(api, as_user, make_user) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    as_user(await make_user(UserRole.OPS))
    response = await api.patch(f"/caregivers/{created['id']}", json={"years_experience": 4})
    assert response.status_code == 200
    assert response.json()["years_experience"] == 4


async def test_empty_update_is_rejected(api, as_user, make_user) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    response = await api.patch(f"/caregivers/{created['id']}", json={})
    assert response.status_code == 422


async def test_tier_cannot_be_changed_by_update(api, as_user, make_user) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    response = await api.patch(f"/caregivers/{created['id']}", json={"tier": "licensed_nurse"})
    assert response.status_code == 422


# ------------------------------------------ POST /caregivers/{id}/certifications
async def test_licence_upload_is_stored_privately(api, as_user, make_user, store, db) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER), tier="licensed_nurse")
    response = await api.post(
        f"/caregivers/{created['id']}/certifications",
        data={"cert_type": "nursing_licence", "title": "RN licence", "licence_number": "RN12345"},
        files={"file": ("licence.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["has_document"] is True
    assert "document_key" not in body
    assert len(store.files) == 1
    key = next(iter(store.files))
    assert key.startswith(f"caregivers/{created['id']}/certifications/")
    assert key.endswith(".pdf")
    assert "certification.create" in await audit_actions(db)


async def test_academy_module_needs_no_file(api, as_user, make_user, store) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    response = await api.post(
        f"/caregivers/{created['id']}/certifications",
        data={"cert_type": "academy_module", "title": "Sickle cell basics"},
    )
    assert response.status_code == 201
    assert response.json()["has_document"] is False
    assert response.json()["status"] == "pending"
    assert store.files == {}


async def test_licence_without_a_file_is_rejected(api, as_user, make_user, store) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    response = await api.post(
        f"/caregivers/{created['id']}/certifications",
        data={"cert_type": "nursing_licence", "title": "RN", "licence_number": "RN12345"},
    )
    assert response.status_code == 400
    assert error_code(response) == "document_required"


async def test_licence_without_a_number_is_rejected(api, as_user, make_user, store) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    response = await api.post(
        f"/caregivers/{created['id']}/certifications",
        data={"cert_type": "nursing_licence", "title": "RN"},
        files={"file": ("licence.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 422
    assert store.files == {}


async def test_unsupported_file_type_is_rejected(api, as_user, make_user, store) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    response = await api.post(
        f"/caregivers/{created['id']}/certifications",
        data={"cert_type": "other", "title": "Reference letter"},
        files={"file": ("letter.pdf", b"just some text", "application/pdf")},  # name lies
    )
    assert response.status_code == 400
    assert error_code(response) == "document_type_unsupported"
    assert store.files == {}


async def test_oversized_file_is_rejected(api, as_user, make_user, store) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    response = await api.post(
        f"/caregivers/{created['id']}/certifications",
        data={"cert_type": "other", "title": "Big scan"},
        files={"file": ("scan.pdf", b"%PDF" + b"0" * (5 * 1024 * 1024), "application/pdf")},
    )
    assert response.status_code == 400
    assert error_code(response) == "document_too_large"
    assert store.files == {}


async def test_another_caregiver_cannot_add_certifications(api, as_user, make_user, store) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    as_user(await make_user(UserRole.CAREGIVER))
    response = await api.post(
        f"/caregivers/{created['id']}/certifications",
        data={"cert_type": "academy_module", "title": "Basics"},
    )
    assert response.status_code == 403


# ------------------------------------------------ /caregivers/{id}/availability
WEEKDAY_MORNING = {"day_of_week": 0, "start_time": "08:00:00", "end_time": "12:00:00"}
WEEKDAY_EVENING = {"day_of_week": 0, "start_time": "17:00:00", "end_time": "20:00:00"}
TUESDAY = {"day_of_week": 1, "start_time": "09:00:00", "end_time": "13:00:00"}


async def test_availability_is_set_and_returned_in_order(api, as_user, make_user, db) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    url = f"/caregivers/{created['id']}/availability"
    response = await api.post(url, json={"windows": [TUESDAY, WEEKDAY_EVENING, WEEKDAY_MORNING]})
    assert response.status_code == 200
    days_and_starts = [(w["day_of_week"], w["start_time"]) for w in response.json()["windows"]]
    assert days_and_starts == [(0, "08:00:00"), (0, "17:00:00"), (1, "09:00:00")]
    assert (await api.get(url)).json() == response.json()
    assert "caregiver.availability_set" in await audit_actions(db)


async def test_setting_availability_replaces_the_schedule(api, as_user, make_user) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    url = f"/caregivers/{created['id']}/availability"
    await api.post(url, json={"windows": [WEEKDAY_MORNING, TUESDAY]})
    response = await api.post(url, json={"windows": [WEEKDAY_EVENING]})
    assert [w["start_time"] for w in response.json()["windows"]] == ["17:00:00"]
    cleared = await api.post(url, json={"windows": []})
    assert cleared.json()["windows"] == []


async def test_overlapping_windows_are_rejected(api, as_user, make_user) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    clash = {"day_of_week": 0, "start_time": "11:00:00", "end_time": "14:00:00"}
    response = await api.post(
        f"/caregivers/{created['id']}/availability", json={"windows": [WEEKDAY_MORNING, clash]}
    )
    assert response.status_code == 422


async def test_others_cannot_see_or_set_availability_of_unapproved_caregiver(
    api, as_user, make_user
) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    url = f"/caregivers/{created['id']}/availability"
    as_user(await make_user(UserRole.PATIENT))
    assert (await api.get(url)).status_code == 404
    as_user(await make_user(UserRole.CAREGIVER))
    assert (await api.post(url, json={"windows": [TUESDAY]})).status_code == 403


async def test_approved_caregivers_availability_is_visible_to_others(
    api, as_user, make_user, db
) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    url = f"/caregivers/{created['id']}/availability"
    await api.post(url, json={"windows": [TUESDAY]})
    await approve(db, created["id"])
    as_user(await make_user(UserRole.PATIENT))
    response = await api.get(url)
    assert response.status_code == 200
    assert len(response.json()["windows"]) == 1


# ------------------------------------------------- POST /caregivers/{id}/verify
async def test_ops_verification_records_results_and_audit(
    api, as_user, make_user, nin_mock, background_mock, db
) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    as_user(await make_user(UserRole.OPS))
    response = await api.post(f"/caregivers/{created['id']}/verify", json={"nin": NIN})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "verifying"
    assert {c["check_type"]: c["status"] for c in body["checks"]} == {
        "nin": "passed",
        "background": "passed",
    }
    assert NIN not in response.text

    actions = await audit_actions(db)
    assert "verification.nin" in actions
    assert "verification.background" in actions

    # The NIN is nowhere in what was stored.
    checks = (await db.scalars(select(VerificationCheck))).all()
    assert len(checks) == 2
    assert all(c.completed_at is not None and c.provider_ref for c in checks)
    stored = " ".join(str(c.result) for c in checks)
    stored += " ".join(str(m) for m in await db.scalars(select(AuditLog.meta)))
    assert NIN not in stored
    assert nin_mock.lookups == 1
    assert background_mock.lookups == 1


async def test_owner_sees_progress_but_not_provider_details(
    api, as_user, make_user, nin_mock, background_mock
) -> None:
    owner = await make_user(UserRole.CAREGIVER)
    created = await apply_as(api, as_user, owner)
    as_user(await make_user(UserRole.OPS))
    await api.post(f"/caregivers/{created['id']}/verify", json={"nin": NIN})
    as_user(owner)
    body = (await api.get(f"/caregivers/{created['id']}")).json()
    assert body["status"] == "verifying"
    assert {c["check_type"] for c in body["verification_checks"]} == {"nin", "background"}
    for check in body["verification_checks"]:
        assert set(check) == {"check_type", "status", "completed_at"}


async def test_only_ops_can_verify(api, as_user, make_user, nin_mock, background_mock) -> None:
    owner = await make_user(UserRole.CAREGIVER)
    created = await apply_as(api, as_user, owner)
    response = await api.post(f"/caregivers/{created['id']}/verify", json={"nin": NIN})
    assert response.status_code == 403
    assert error_code(response) == "role_forbidden"
    assert nin_mock.lookups == 0


async def test_failed_nin_check_is_recorded_as_failed(
    api, as_user, make_user, nin_mock, background_mock
) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    as_user(await make_user(UserRole.OPS))
    response = await api.post(
        f"/caregivers/{created['id']}/verify", json={"checks": ["nin"], "nin": "12345670000"}
    )
    assert response.status_code == 200
    assert response.json()["checks"][0]["status"] == "failed"
    assert response.json()["status"] == "verifying"  # only a human approves or rejects


async def test_flagged_background_check_is_recorded_as_failed(
    api, as_user, make_user, background_mock
) -> None:
    created = await apply_as(
        api, as_user, await make_user(UserRole.CAREGIVER, name="Flagged Person")
    )
    as_user(await make_user(UserRole.OPS))
    response = await api.post(f"/caregivers/{created['id']}/verify", json={"checks": ["background"]})
    assert response.json()["checks"][0]["status"] == "failed"


async def test_provider_outage_is_recorded_and_can_be_retried(
    api, as_user, make_user, nin_mock
) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    as_user(await make_user(UserRole.OPS))
    url = f"/caregivers/{created['id']}/verify"
    down = await api.post(url, json={"checks": ["nin"], "nin": "12345679999"})
    assert down.status_code == 200
    assert down.json()["checks"][0]["status"] == "error"
    retry = await api.post(url, json={"checks": ["nin"], "nin": NIN})
    assert retry.status_code == 200
    assert retry.json()["checks"][0]["status"] == "passed"


async def test_verify_needs_the_nin(api, as_user, make_user, nin_mock) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    as_user(await make_user(UserRole.OPS))
    response = await api.post(f"/caregivers/{created['id']}/verify", json={})
    assert response.status_code == 422
    assert nin_mock.lookups == 0


async def test_approved_caregiver_cannot_be_verified_again(
    api, as_user, make_user, nin_mock, background_mock, db
) -> None:
    created = await apply_as(api, as_user, await make_user(UserRole.CAREGIVER))
    await approve(db, created["id"])
    as_user(await make_user(UserRole.OPS))
    response = await api.post(f"/caregivers/{created['id']}/verify", json={"nin": NIN})
    assert response.status_code == 409
    assert error_code(response) == "caregiver_not_verifiable"


async def test_verify_unknown_caregiver_is_not_found(api, as_user, make_user, nin_mock) -> None:
    as_user(await make_user(UserRole.OPS))
    response = await api.post(f"/caregivers/{uuid.uuid4()}/verify", json={"nin": NIN})
    assert response.status_code == 404