"""Tests: one-tap crisis from a set-up device, without signing in."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app import database
from app.core import encryption
from app.core.audit import AuditLog
from app.core.permissions import UserRole
from app.domains.auth.models import User
from app.domains.crisis.models import CrisisDeviceKey, CrisisEvent, CrisisStatus
from app.domains.patients.models import Guardian, Patient
from app.tests.crisis_factories import (
    ENUGU,
    add_contact,
    add_guardian,
    auth,
    make_patient,
    make_user,
    phone,
)


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_fernet", lambda: key)


@dataclass
class Home:
    patient_user: User
    patient: Patient
    mum: User
    stranger: User


@pytest.fixture
async def home(db) -> Home:
    patient_user = await make_user(db, UserRole.PATIENT, "Chidi Okafor")
    patient = await make_patient(db, user=patient_user, created_by=patient_user, home=(6.45, 7.51))
    mum = await make_user(db, UserRole.GUARDIAN, "Ngozi Okafor")
    await add_guardian(db, patient, mum, primary=True)
    await add_contact(db, patient, "Aunty Ada", phone())
    stranger = await make_user(db, UserRole.GUARDIAN, "Someone Else")
    return Home(patient_user, patient, mum, stranger)


async def turn_on(api, home: Home, who: User | None = None) -> dict:
    resp = await api.post(
        "/crisis/device-keys",
        json={"patient_id": str(home.patient.id), "label": "Chrome on Android"},
        headers=auth(who or home.mum),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def press_body(**extra) -> dict:
    return {
        "client_event_id": str(uuid.uuid4()),
        "location": {"latitude": ENUGU[0], "longitude": ENUGU[1], "accuracy_m": 15},
        **extra,
    }


async def press(api, key: str | None, **extra):
    headers = {"X-Crisis-Key": key} if key else {}
    return await api.post("/crisis/device-press", json=press_body(**extra), headers=headers)


async def test_a_set_up_device_can_press_without_signing_in(api, home, sms):
    created = await turn_on(api, home)
    assert created["key"].startswith("ckd_")

    resp = await press(api, created["key"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["patient_id"] == str(home.patient.id)
    assert body["patient_first_name"] == "Chidi"
    assert body["status"] == "active"
    assert body["location"]["source"] == "device"
    assert body["alerts"], "contacts are alerted"


async def test_only_the_hash_of_the_key_is_stored(api, home):
    created = await turn_on(api, home)
    async with database.SessionLocal() as s:
        row = await s.get(CrisisDeviceKey, uuid.UUID(created["id"]))
    assert row is not None
    assert created["key"] not in row.key_hash
    assert len(row.key_hash) == 64


async def test_press_is_made_in_the_owners_name_and_marks_the_key_used(api, home):
    created = await turn_on(api, home)
    resp = await press(api, created["key"])
    async with database.SessionLocal() as s:
        event = await s.get(CrisisEvent, uuid.UUID(resp.json()["id"]))
        key = await s.get(CrisisDeviceKey, uuid.UUID(created["id"]))
        audit = await s.scalar(
            select(AuditLog).where(
                AuditLog.action == "crisis.trigger", AuditLog.entity_id == event.id
            )
        )
    assert event.triggered_by == home.mum.id
    assert key.last_used_at is not None
    assert audit.meta["device_key_id"] == created["id"]


async def test_same_press_twice_returns_the_same_event(api, home):
    created = await turn_on(api, home)
    body = press_body()
    first = await api.post(
        "/crisis/device-press", json=body, headers={"X-Crisis-Key": created["key"]}
    )
    again = await api.post(
        "/crisis/device-press", json=body, headers={"X-Crisis-Key": created["key"]}
    )
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]


@pytest.mark.parametrize("key", [None, "", "ckd_not-a-real-key", "Bearer something"])
async def test_missing_or_unknown_keys_are_refused(api, home, key):
    resp = await press(api, key)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "crisis_key_invalid"


async def test_a_revoked_key_stops_working(api, home):
    created = await turn_on(api, home)
    resp = await api.delete(f"/crisis/device-keys/{created['id']}", headers=auth(home.patient_user))
    assert resp.status_code == 204
    assert (await press(api, created["key"])).status_code == 401


async def test_the_key_stops_when_its_owner_loses_access(api, db, home):
    created = await turn_on(api, home)
    async with database.SessionLocal() as s:
        guardian = await s.scalar(
            select(Guardian).where(
                Guardian.patient_id == home.patient.id, Guardian.user_id == home.mum.id
            )
        )
        await s.delete(guardian)
        await s.commit()
    assert (await press(api, created["key"])).status_code == 401


async def test_a_device_can_follow_and_cancel_its_own_crisis(api, home):
    created = await turn_on(api, home)
    headers = {"X-Crisis-Key": created["key"]}
    event_id = (await press(api, created["key"])).json()["id"]

    seen = await api.get(f"/crisis/device-press/{event_id}", headers=headers)
    assert seen.status_code == 200
    assert seen.json()["nearby"]["crisis_event_id"] == event_id

    cancelled = await api.post(f"/crisis/device-press/{event_id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == CrisisStatus.CANCELLED.value


async def test_a_device_cannot_see_another_patients_crisis(api, db, home):
    created = await turn_on(api, home)
    other_user = await make_user(db, UserRole.PATIENT, "Other Person")
    other = await make_patient(db, user=other_user, created_by=other_user)
    other_key = (
        await api.post(
            "/crisis/device-keys",
            json={"patient_id": str(other.id), "label": "Phone"},
            headers=auth(other_user),
        )
    ).json()["key"]
    other_event = (await press(api, other_key)).json()["id"]

    resp = await api.get(
        f"/crisis/device-press/{other_event}", headers={"X-Crisis-Key": created["key"]}
    )
    assert resp.status_code == 404


async def test_a_device_can_forget_its_own_key(api, home):
    created = await turn_on(api, home)
    headers = {"X-Crisis-Key": created["key"]}
    assert (await api.delete("/crisis/device-press", headers=headers)).status_code == 204
    assert (await press(api, created["key"])).status_code == 401


async def test_only_the_patient_or_a_guardian_can_turn_it_on(api, home):
    resp = await api.post(
        "/crisis/device-keys",
        json={"patient_id": str(home.patient.id), "label": "Phone"},
        headers=auth(home.stranger),
    )
    assert resp.status_code in (403, 404)


async def test_listing_shows_active_devices_without_their_keys(api, home):
    mine = await turn_on(api, home)
    await turn_on(api, home, who=home.patient_user)
    resp = await api.get(
        "/crisis/device-keys", params={"patient_id": str(home.patient.id)}, headers=auth(home.mum)
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert all("key" not in r for r in rows)
    assert [r["added_by_you"] for r in rows if r["id"] == mine["id"]] == [True]


async def test_anonymous_press_is_logged_and_alerts_nobody(api, home, sms):
    resp = await api.post(
        "/crisis/anonymous-press", json={"location": {"latitude": 6.45123, "longitude": 7.51987}}
    )
    assert resp.status_code == 202
    assert resp.json()["logged"] is True
    assert sms.outbox == []
    async with database.SessionLocal() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == "crisis.anonymous_press"))
    assert row.meta == {"latitude": 6.451, "longitude": 7.52, "accuracy_m": None}


# ------------------------------------------------------------------ edge cases
async def test_cancelling_twice_is_a_conflict(api, home):
    created = await turn_on(api, home)
    headers = {"X-Crisis-Key": created["key"]}
    event_id = (await press(api, created["key"])).json()["id"]
    first = await api.post(f"/crisis/device-press/{event_id}/cancel", headers=headers)
    assert first.status_code == 200
    again = await api.post(f"/crisis/device-press/{event_id}/cancel", headers=headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "crisis_closed"


async def test_a_device_cannot_cancel_another_patients_crisis(api, db, home):
    created = await turn_on(api, home)
    other_user = await make_user(db, UserRole.PATIENT, "Other Person")
    other = await make_patient(db, user=other_user, created_by=other_user)
    other_key = (
        await api.post(
            "/crisis/device-keys",
            json={"patient_id": str(other.id), "label": "Phone"},
            headers=auth(other_user),
        )
    ).json()["key"]
    other_event = (await press(api, other_key)).json()["id"]

    resp = await api.post(
        f"/crisis/device-press/{other_event}/cancel", headers={"X-Crisis-Key": created["key"]}
    )
    assert resp.status_code == 404
    async with database.SessionLocal() as s:
        event = await s.get(CrisisEvent, uuid.UUID(other_event))
    assert event.status is CrisisStatus.ACTIVE


async def test_a_deactivated_owner_makes_the_key_useless(api, home):
    created = await turn_on(api, home)
    async with database.SessionLocal() as s:
        mum = await s.get(User, home.mum.id)
        mum.is_active = False
        await s.commit()
    assert (await press(api, created["key"])).status_code == 401


async def test_a_second_device_press_during_a_crisis_returns_the_open_one(api, home, sms):
    created = await turn_on(api, home)
    first = await press(api, created["key"])
    sent = len(sms.outbox)
    second = await press(api, created["key"])
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(sms.outbox) == sent, "nobody is alerted twice"


async def test_a_device_press_joins_a_crisis_started_while_signed_in(api, home):
    created = await turn_on(api, home)
    signed_in = await api.post(
        "/crisis/events",
        json={"patient_id": str(home.patient.id), "client_event_id": str(uuid.uuid4())},
        headers=auth(home.patient_user),
    )
    assert signed_in.status_code == 201
    resp = await press(api, created["key"])
    assert resp.status_code == 200
    assert resp.json()["id"] == signed_in.json()["id"]


async def test_reusing_another_crisis_client_event_id_is_rejected(api, db, home):
    created = await turn_on(api, home)
    other_user = await make_user(db, UserRole.PATIENT, "Other Person")
    other = await make_patient(db, user=other_user, created_by=other_user)
    used = str(uuid.uuid4())
    await api.post(
        "/crisis/events",
        json={"patient_id": str(other.id), "client_event_id": used},
        headers=auth(other_user),
    )
    resp = await press(api, created["key"], client_event_id=used)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "client_event_id_reused"


async def test_no_gps_falls_back_to_the_home_location(api, home):
    created = await turn_on(api, home)
    resp = await api.post(
        "/crisis/device-press",
        json={"client_event_id": str(uuid.uuid4())},
        headers={"X-Crisis-Key": created["key"]},
    )
    assert resp.status_code == 201
    assert resp.json()["location"]["source"] == "home"


async def test_an_implausible_offline_time_is_replaced_by_now(api, home):
    created = await turn_on(api, home)
    resp = await press(api, created["key"], triggered_at="2001-01-01T00:00:00+00:00")
    assert resp.status_code == 201
    assert not resp.json()["triggered_at"].startswith("2001")


@pytest.mark.parametrize(
    "body",
    [
        {"location": {"latitude": 95, "longitude": 7.5}},
        {"client_event_id": "not-a-uuid"},
        {"triggered_at": "2026-10-31T10:00:00"},  # no timezone
        {"patient_id": str(uuid.uuid4())},  # not allowed: the key decides the patient
    ],
)
async def test_bad_press_bodies_are_rejected(api, home, body):
    created = await turn_on(api, home)
    resp = await api.post(
        "/crisis/device-press",
        json={**press_body(), **body},
        headers={"X-Crisis-Key": created["key"]},
    )
    assert resp.status_code == 422


async def test_a_device_is_rate_limited(api, home, monkeypatch):
    from app.domains.crisis import router as crisis_router

    monkeypatch.setattr(crisis_router, "DEVICE_PRESSES_PER_HOUR", 3)
    created = await turn_on(api, home)
    codes = [(await press(api, created["key"])).status_code for _ in range(4)]
    assert codes == [201, 200, 200, 429]


async def test_redis_being_down_never_blocks_an_alert(api, home):
    from redis.exceptions import ConnectionError as RedisConnectionError

    from app.core.redis import get_redis
    from app.main import app

    class DownRedis:
        async def incr(self, *_a, **_k):
            raise RedisConnectionError("down")

    created = await turn_on(api, home)
    app.dependency_overrides[get_redis] = lambda: DownRedis()
    try:
        assert (await press(api, created["key"])).status_code == 201
    finally:
        app.dependency_overrides.pop(get_redis, None)


async def test_anonymous_presses_are_rate_limited(api, monkeypatch):
    from app.domains.crisis import router as crisis_router

    monkeypatch.setattr(crisis_router, "ANONYMOUS_PRESSES_PER_HOUR", 2)
    codes = [(await api.post("/crisis/anonymous-press", json={})).status_code for _ in range(3)]
    assert codes == [202, 202, 429]


async def test_anonymous_press_without_location_is_logged(api):
    resp = await api.post("/crisis/anonymous-press", json={})
    assert resp.status_code == 202
    async with database.SessionLocal() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == "crisis.anonymous_press"))
    assert row.meta == {"location": None}


async def test_too_many_devices_for_one_patient(api, home, monkeypatch):
    from app.domains.crisis import service as crisis_service

    monkeypatch.setattr(crisis_service, "MAX_DEVICE_KEYS_PER_PATIENT", 2)
    await turn_on(api, home)
    await turn_on(api, home)
    resp = await api.post(
        "/crisis/device-keys",
        json={"patient_id": str(home.patient.id), "label": "Third"},
        headers=auth(home.mum),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "too_many_device_keys"


async def test_a_revoked_device_frees_a_slot(api, home, monkeypatch):
    from app.domains.crisis import service as crisis_service

    monkeypatch.setattr(crisis_service, "MAX_DEVICE_KEYS_PER_PATIENT", 1)
    first = await turn_on(api, home)
    await api.delete(f"/crisis/device-keys/{first['id']}", headers=auth(home.mum))
    await turn_on(api, home)


@pytest.mark.parametrize("label", ["", "   ", "x" * 81])
async def test_labels_must_be_sensible(api, home, label):
    resp = await api.post(
        "/crisis/device-keys",
        json={"patient_id": str(home.patient.id), "label": label},
        headers=auth(home.mum),
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("role", [UserRole.FAMILY_ABROAD, UserRole.CAREGIVER, UserRole.OPS])
async def test_other_roles_cannot_turn_it_on(api, db, home, role):
    user = await make_user(db, role, "Not Allowed")
    resp = await api.post(
        "/crisis/device-keys",
        json={"patient_id": str(home.patient.id), "label": "Phone"},
        headers=auth(user),
    )
    assert resp.status_code == 403


async def test_a_stranger_cannot_list_or_revoke_devices(api, home):
    created = await turn_on(api, home)
    listing = await api.get(
        "/crisis/device-keys",
        params={"patient_id": str(home.patient.id)},
        headers=auth(home.stranger),
    )
    assert listing.status_code in (403, 404)
    revoke = await api.delete(f"/crisis/device-keys/{created['id']}", headers=auth(home.stranger))
    assert revoke.status_code == 404
    assert (await press(api, created["key"])).status_code == 201, "still works"


async def test_revoking_twice_is_not_found(api, home):
    created = await turn_on(api, home)
    url = f"/crisis/device-keys/{created['id']}"
    assert (await api.delete(url, headers=auth(home.mum))).status_code == 204
    assert (await api.delete(url, headers=auth(home.mum))).status_code == 404


async def test_forgetting_twice_is_refused_the_second_time(api, home):
    created = await turn_on(api, home)
    headers = {"X-Crisis-Key": created["key"]}
    assert (await api.delete("/crisis/device-press", headers=headers)).status_code == 204
    assert (await api.delete("/crisis/device-press", headers=headers)).status_code == 401


async def test_a_revoked_key_cannot_follow_its_old_crisis(api, home):
    created = await turn_on(api, home)
    headers = {"X-Crisis-Key": created["key"]}
    event_id = (await press(api, created["key"])).json()["id"]
    await api.delete(f"/crisis/device-keys/{created['id']}", headers=auth(home.mum))
    assert (await api.get(f"/crisis/device-press/{event_id}", headers=headers)).status_code == 401


async def test_a_session_token_is_not_a_crisis_key(api, home):
    no_key = await api.post("/crisis/device-press", json=press_body(), headers=auth(home.mum))
    assert no_key.status_code == 401


async def test_unknown_event_ids_are_not_found(api, home):
    created = await turn_on(api, home)
    resp = await api.get(
        f"/crisis/device-press/{uuid.uuid4()}", headers={"X-Crisis-Key": created["key"]}
    )
    assert resp.status_code == 404


async def test_a_patient_with_no_contacts_is_told_so(api, db):
    user = await make_user(db, UserRole.PATIENT, "Lone Patient")
    patient = await make_patient(db, user=user, created_by=user)
    key = (
        await api.post(
            "/crisis/device-keys",
            json={"patient_id": str(patient.id), "label": "Phone"},
            headers=auth(user),
        )
    ).json()["key"]
    resp = await press(api, key)
    assert resp.status_code == 201
    assert resp.json()["no_contacts"] is True
