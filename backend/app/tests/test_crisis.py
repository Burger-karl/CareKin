"""Tests: Crisis button flow (PRD 7.2, CR-1 to CR-5), against real Postgres + PostGIS."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select, text, update

from app import database
from app.config import get_settings
from app.core import encryption
from app.core.audit import AuditLog
from app.core.permissions import UserRole
from app.core.security import create_access_token
from app.domains.auth.models import User
from app.domains.crisis import router as crisis_router
from app.domains.crisis import service
from app.domains.crisis.facilities import facilities_available
from app.domains.crisis.models import (
    AlertStatus,
    CrisisAlert,
    CrisisEvent,
    CrisisStatus,
    LocationSource,
)
from app.domains.passport.models import PassportEntry
from app.domains.patients.models import Patient
from app.integrations.sms import MockSMSClient, SentMessage, SMSDeliveryError, get_sms_client
from app.main import app
from app.tests.crisis_factories import (
    ENUGU,
    add_contact,
    add_family_link,
    add_guardian,
    auth,
    grant_consent,
    make_facility,
    make_patient,
    make_user,
    phone,
    point,
    trigger_body,
)

DISCLAIMER_START = "CareKin does not give medical advice"

# The facilities domain (Day 7) owns the facility tables. Until its models exist, the
# ranking tests are skipped and the "no facility directory yet" tests run instead.
FACILITIES = facilities_available()
needs_facilities = pytest.mark.skipif(
    not FACILITIES, reason="facilities models not built yet (facilities domain, Day 7)"
)
without_facilities = pytest.mark.skipif(FACILITIES, reason="facilities models exist")


@dataclass
class FlakySMSClient(MockSMSClient):
    """The team's mock SMS client, plus numbers that the "provider" refuses."""

    fail_numbers: set[str] = field(default_factory=set)

    async def send(self, to: str, body: str) -> SentMessage:
        if to in self.fail_numbers:
            raise SMSDeliveryError("Simulated failure")
        return await super().send(to, body)


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests don't set FIELD_ENCRYPTION_KEY; use a throwaway key (as test_patients does)."""
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_fernet", lambda: key)


@pytest.fixture
def sms() -> Iterator[FlakySMSClient]:
    """Overrides the conftest fixture so tests can make chosen numbers fail."""
    mock = FlakySMSClient()
    app.dependency_overrides[get_sms_client] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_sms_client, None)


@dataclass
class Family:
    patient_user: User
    patient: Patient
    mum: User  # primary guardian, gets alerts
    dad: User  # guardian who turned crisis alerts off
    aunt_phone: str
    ops: User
    stranger: User
    caregiver: User
    cousin: User  # family abroad, location shared
    uncle: User  # family abroad, location not shared


@pytest.fixture
async def family(db) -> Family:
    patient_user = await make_user(db, UserRole.PATIENT, "Chidi Okafor")
    patient = await make_patient(db, user=patient_user, created_by=patient_user, home=(6.45, 7.51))
    mum = await make_user(db, UserRole.GUARDIAN, "Ngozi Okafor")
    dad = await make_user(db, UserRole.GUARDIAN, "Emeka Okafor")
    await add_guardian(db, patient, mum, primary=True)
    await add_guardian(db, patient, dad, alerts=False)
    aunt_phone = phone()
    await add_contact(db, patient, "Aunty Ada", aunt_phone, priority=1)
    await add_contact(db, patient, "Mum (again)", mum.phone, priority=2)  # duplicate phone
    cousin = await make_user(db, UserRole.FAMILY_ABROAD, "Cousin Obi")
    uncle = await make_user(db, UserRole.FAMILY_ABROAD, "Uncle Ike")
    await add_family_link(db, patient, cousin, {"location": True})
    await add_family_link(db, patient, uncle, {"health_data": True})
    return Family(
        patient_user=patient_user,
        patient=patient,
        mum=mum,
        dad=dad,
        aunt_phone=aunt_phone,
        ops=await make_user(db, UserRole.OPS, "Ops Agent"),
        stranger=await make_user(db, UserRole.PATIENT, "Someone Else"),
        caregiver=await make_user(db, UserRole.CAREGIVER, "Care Giver"),
        cousin=cousin,
        uncle=uncle,
    )


@pytest.fixture
async def facilities(db) -> dict[str, Any]:
    if not FACILITIES:
        pytest.skip("facilities models not built yet")
    lat, lng = ENUGU
    return {
        "near": await make_facility(db, "Near General", lat + 0.01, lng),
        "mid": await make_facility(
            db,
            "Mid Teaching",
            lat + 0.03,
            lng,
            services=("haematologist", "transfusion"),
        ),
        "stale": await make_facility(db, "Stale Clinic", lat + 0.05, lng, status="stale"),
        "unverified": await make_facility(
            db, "Unverified Close", lat + 0.004, lng, status="unverified"
        ),
        "lab_only": await make_facility(
            db, "Lab Only", lat + 0.007, lng, services=("genotype_testing",)
        ),
        "closed_er": await make_facility(db, "Closed ER", lat + 0.005, lng, available=False),
    }


async def rows(model, *where):
    async with database.SessionLocal() as s:
        return list(await s.scalars(select(model).where(*where)))


def no_background_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate the API process dying right after the commit."""
    monkeypatch.setattr(crisis_router, "_send_alerts", lambda *a, **k: None)


async def press(api, family: Family, who: User | None = None, **kwargs):
    return await api.post(
        "/crisis/events",
        json=trigger_body(family.patient, **kwargs),
        headers=auth(who or family.patient_user),
    )


# ------------------------------------------------------------------ trigger
async def test_trigger_alerts_contacts(api, family, sms):
    resp = await press(api, family)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["disclaimer"].startswith(DISCLAIMER_START)
    assert body["emergency_number"] == "112"
    assert body["location"]["source"] == "device" and body["location"]["stale"] is False

    # Mum once (her duplicate contact entry is skipped), aunt once, dad opted out.
    assert [a["recipient_name"] for a in body["alerts"]] == ["Ngozi Okafor", "Aunty Ada"]
    assert [a["recipient_type"] for a in body["alerts"]] == ["guardian", "emergency_contact"]
    assert all("*" in a["phone"] for a in body["alerts"])  # masked in the API
    assert body["no_contacts"] is False

    assert body["nearby"]["facilities_available"] is FACILITIES

    # Background delivery has run: both alerts sent by SMS.
    alerts = await rows(CrisisAlert)
    assert {a.status for a in alerts} == {AlertStatus.SENT}
    assert all(a.provider_ref and a.sent_at and a.attempts == 1 for a in alerts)
    assert sorted(m.to for m in sms.outbox) == sorted([family.mum.phone, family.aunt_phone])


@needs_facilities
async def test_nearest_verified_facilities_are_listed_first(api, family, facilities):
    nearby = (await press(api, family)).json()["nearby"]

    # Unverified, lab-only and closed-ER listings are left out; stale ones stay (FF-5).
    assert [f["name"] for f in nearby["facilities"]] == [
        "Near General",
        "Mid Teaching",
        "Stale Clinic",
    ]
    assert nearby["widened_search"] is False and nearby["radius_km"] == 50
    first = nearby["facilities"][0]
    assert 1000 < first["distance_m"] < 1250
    assert first["call_number"] == facilities["near"].emergency_phone
    assert first["directions_url"].startswith("https://www.google.com/maps/dir/?api=1&destination=")
    assert first["latitude"] == pytest.approx(ENUGU[0] + 0.01)
    assert nearby["facilities"][1]["crisis_services"] == ["haematologist", "transfusion"]
    assert nearby["facilities"][2]["verification_status"] == "stale"


@without_facilities
async def test_without_the_facility_directory_crisis_still_works(api, family, sms):
    created = await press(api, family)

    assert created.status_code == 201
    nearby = created.json()["nearby"]
    assert nearby["facilities_available"] is False
    assert nearby["facilities"] == [] and nearby["radius_km"] is None
    assert len(sms.outbox) == 2  # alerts don't depend on facilities
    event_id = created.json()["id"]
    listing = await api.get(f"/crisis/events/{event_id}/facilities", headers=auth(family.mum))
    assert listing.status_code == 200 and listing.json()["facilities_available"] is False

    # No facility can be named yet, so an outcome facility is "not found".
    resolve = await api.post(
        f"/crisis/events/{event_id}/resolve",
        json={"outcome_facility_id": str(uuid.uuid4())},
        headers=auth(family.mum),
    )
    assert resolve.status_code == 404
    assert resolve.json()["error"]["code"] == "facility_not_found"


async def test_alert_text_is_short_private_and_gives_no_medical_advice(api, family, sms):
    await press(api, family)
    message = sms.outbox[0].body

    assert message.startswith("CareKin ALERT: Chidi pressed the crisis button at ")
    assert "Okafor" not in message  # first name only
    assert "https://maps.google.com/?q=6.44130,7.49880" in message
    assert "Emergency: 112" in message
    for word in ("sickle", "scd", "pain", "dose", "medication", "drug", "take ", "hydrat"):
        assert word not in message.lower()
    assert len(message) <= 170


async def test_same_press_sent_twice_returns_original_event(api, family, sms):
    body = trigger_body(family.patient)
    first = await api.post("/crisis/events", json=body, headers=auth(family.patient_user))
    again = await api.post("/crisis/events", json=body, headers=auth(family.patient_user))

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"]
    assert len(await rows(CrisisEvent)) == 1
    assert len(await rows(CrisisAlert)) == 2
    assert len(sms.outbox) == 2  # nobody alerted twice


async def test_simultaneous_copies_of_one_press_create_one_event(api, family, sms):
    body = trigger_body(family.patient)
    responses = await asyncio.gather(
        *(
            api.post("/crisis/events", json=body, headers=auth(family.patient_user))
            for _ in range(5)
        )
    )

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert len(await rows(CrisisEvent)) == 1
    assert len(sms.outbox) == 2


async def test_second_press_during_active_crisis_returns_existing_event(api, family, sms):
    first = await press(api, family)
    second = await press(api, family, who=family.mum)

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(sms.outbox) == 2


async def test_new_press_after_window_creates_new_event(api, family, monkeypatch):
    monkeypatch.setattr(get_settings(), "CRISIS_DUPLICATE_WINDOW_SECONDS", 0)
    a = await press(api, family)
    b = await press(api, family)
    assert (a.status_code, b.status_code) == (201, 201)
    assert a.json()["id"] != b.json()["id"]


async def test_client_event_id_reused_for_another_patient_is_rejected(api, db, family):
    other_patient = await make_patient(db, user=family.stranger, created_by=family.stranger)
    body = trigger_body(family.patient)
    await api.post("/crisis/events", json=body, headers=auth(family.patient_user))

    reused = {**body, "patient_id": str(other_patient.id)}
    resp = await api.post("/crisis/events", json=reused, headers=auth(family.stranger))

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "client_event_id_reused"


async def test_guardian_can_trigger_and_is_not_alerted_about_their_own_press(api, family, sms):
    resp = await press(api, family, who=family.mum)

    assert resp.status_code == 201
    assert [a["recipient_name"] for a in resp.json()["alerts"]] == ["Aunty Ada"]
    assert [m.to for m in sms.outbox] == [family.aunt_phone]


async def test_deactivated_guardian_is_not_alerted(api, db, family, sms):
    family.mum.is_active = False
    db.add(family.mum)
    await db.commit()

    resp = await press(api, family)

    # Mum's guardian alert is skipped; her old number on the contact list still gets one.
    names = [a["recipient_name"] for a in resp.json()["alerts"]]
    assert names == ["Aunty Ada", "Mum (again)"]
    assert len(sms.outbox) == 2


# ------------------------------------------------------------------ access
async def test_unrelated_user_gets_404_and_nothing_is_created(api, family, sms):
    resp = await press(api, family, who=family.stranger)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "patient_not_found"
    assert await rows(CrisisEvent) == []
    assert sms.outbox == []


@pytest.mark.parametrize("who", ["caregiver", "ops", "cousin"])
async def test_roles_that_cannot_press_the_button(api, family, who):
    resp = await press(api, family, who=getattr(family, who))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "role_forbidden"


async def test_authentication_errors(api, family):
    body = trigger_body(family.patient)
    missing = await api.post("/crisis/events", json=body)
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "not_authenticated"
    assert missing.headers["www-authenticate"] == "Bearer"

    garbage = await api.post("/crisis/events", json=body, headers={"Authorization": "Bearer nope"})
    assert garbage.json()["error"]["code"] == "token_invalid"

    expired, _ = create_access_token(
        family.patient_user.id, UserRole.PATIENT.value, expires_delta=timedelta(seconds=-1)
    )
    resp = await api.post(
        "/crisis/events", json=body, headers={"Authorization": f"Bearer {expired}"}
    )
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "token_expired"


async def test_deactivated_user_is_rejected(api, db, family):
    family.patient_user.is_active = False
    db.add(family.patient_user)
    await db.commit()
    resp = await press(api, family)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "account_disabled"


async def test_who_can_follow_an_event(api, family):
    event_id = (await press(api, family)).json()["id"]

    for user in (family.patient_user, family.mum, family.dad, family.ops, family.cousin):
        assert (await api.get(f"/crisis/events/{event_id}", headers=auth(user))).status_code == 200
        assert (
            await api.get(f"/crisis/events/{event_id}/facilities", headers=auth(user))
        ).status_code == 200

    # Family link without the location share: can see the patient, not the crisis.
    uncle = await api.get(f"/crisis/events/{event_id}", headers=auth(family.uncle))
    assert uncle.status_code == 403
    assert uncle.json()["error"]["code"] == "crisis_access_denied"

    stranger = await api.get(f"/crisis/events/{event_id}", headers=auth(family.stranger))
    assert stranger.status_code == 404
    assert stranger.json()["error"]["code"] == "crisis_not_found"

    caregiver = await api.get(f"/crisis/events/{event_id}", headers=auth(family.caregiver))
    assert caregiver.json()["error"]["code"] == "role_forbidden"

    missing = await api.get(f"/crisis/events/{uuid.uuid4()}", headers=auth(family.ops))
    assert missing.status_code == 404


async def test_family_abroad_cannot_resolve_or_resend(api, family):
    event_id = (await press(api, family)).json()["id"]
    resolve = await api.post(
        f"/crisis/events/{event_id}/resolve", json={}, headers=auth(family.cousin)
    )
    retry = await api.post(
        "/crisis/alert", json={"crisis_event_id": event_id}, headers=auth(family.cousin)
    )
    assert resolve.status_code == retry.status_code == 403


# ------------------------------------------------------------------ location
async def test_no_gps_falls_back_to_home_location(api, family, sms):
    resp = await press(api, family, location=None)
    body = resp.json()
    assert resp.status_code == 201
    assert body["location"]["source"] == "home"
    assert body["location"]["latitude"] == pytest.approx(6.45)
    assert (
        "No live location. Home: https://maps.google.com/?q=6.45000,7.51000" in sms.outbox[0].body
    )


async def test_no_location_at_all_still_alerts_everyone(api, db, family, sms):
    no_home = await make_patient(db, user=None, created_by=family.mum, name="Ifeoma")
    await add_guardian(db, no_home, family.mum)
    await add_contact(db, no_home, "Neighbour", phone())

    resp = await api.post(
        "/crisis/events", json=trigger_body(no_home, location=None), headers=auth(family.mum)
    )

    body = resp.json()
    assert resp.status_code == 201
    assert body["location"]["source"] == "none"
    assert body["nearby"]["facilities"] == [] and body["nearby"]["radius_km"] is None
    assert len(sms.outbox) == 1 and "Location unavailable." in sms.outbox[0].body


async def test_patient_with_no_contacts_is_told_to_call_emergency_number(api, db, sms):
    alone_user = await make_user(db, UserRole.PATIENT, "Alone")
    alone = await make_patient(db, user=alone_user, created_by=alone_user)
    resp = await api.post("/crisis/events", json=trigger_body(alone), headers=auth(alone_user))
    assert resp.status_code == 201
    assert resp.json()["no_contacts"] is True and resp.json()["alerts"] == []
    assert resp.json()["emergency_number"] == "112"
    assert sms.outbox == []


async def test_old_gps_fix_is_flagged_stale(api, family):
    body = trigger_body(family.patient)
    body["location"]["captured_at"] = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    resp = await api.post("/crisis/events", json=body, headers=auth(family.patient_user))
    assert resp.json()["location"]["stale"] is True


async def test_offline_press_keeps_its_time_but_implausible_clocks_are_ignored(
    api, family, monkeypatch
):
    monkeypatch.setattr(get_settings(), "CRISIS_DUPLICATE_WINDOW_SECONDS", 0)
    now = datetime.now(UTC)
    two_hours_ago = now - timedelta(hours=2)
    offline = await press(api, family, triggered_at=two_hours_ago.isoformat())
    assert datetime.fromisoformat(offline.json()["triggered_at"]) == two_hours_ago

    future = await press(api, family, triggered_at=(now + timedelta(days=3)).isoformat())
    assert abs(datetime.fromisoformat(future.json()["triggered_at"]) - now) < timedelta(minutes=1)


@pytest.mark.parametrize(
    "location",
    [
        {"latitude": 91, "longitude": 7},
        {"latitude": 6, "longitude": 181},
        {"latitude": "x", "longitude": 7},
    ],
)
async def test_invalid_coordinates_are_rejected(api, family, location):
    body = trigger_body(family.patient)
    body["location"] = location
    resp = await api.post("/crisis/events", json=body, headers=auth(family.patient_user))
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"][:2] == ["body", "location"]


async def test_naive_timestamps_and_unknown_fields_are_rejected(api, family):
    naive = await press(api, family, triggered_at="2026-10-13T10:00:00")
    assert naive.status_code == 422
    extra = await press(api, family, is_admin=True)
    assert extra.status_code == 422


# ------------------------------------------------------------------ facilities
@needs_facilities
async def test_search_widens_when_nothing_verified_is_close(api, db, family):
    await make_facility(db, "Far Away Teaching", ENUGU[0] + 0.72, ENUGU[1])  # ~80 km
    nearby = (await press(api, family)).json()["nearby"]
    assert nearby["widened_search"] is True and nearby["radius_km"] == 150
    assert [f["name"] for f in nearby["facilities"]] == ["Far Away Teaching"]


@needs_facilities
async def test_nothing_within_the_widest_radius(api, db, family):
    await make_facility(db, "Lagos", 6.5244, 3.3792)  # ~460 km away
    nearby = (await press(api, family)).json()["nearby"]
    assert nearby["facilities"] == [] and nearby["widened_search"] is True


@needs_facilities
async def test_facility_list_is_capped(api, db, family, monkeypatch):
    monkeypatch.setattr(get_settings(), "CRISIS_FACILITY_LIMIT", 3)
    for i in range(6):
        await make_facility(db, f"F{i}", ENUGU[0] + 0.01 * (i + 1), ENUGU[1])
    created = await press(api, family)
    names = [f["name"] for f in created.json()["nearby"]["facilities"]]
    assert names == ["F0", "F1", "F2"]

    again = await api.get(
        f"/crisis/events/{created.json()['id']}/facilities", headers=auth(family.mum)
    )
    assert [f["name"] for f in again.json()["facilities"]] == names


@needs_facilities
async def test_trigger_is_fast_with_many_facilities(api, db, family):
    from app.domains.facilities.models import Facility, FacilityService

    for i in range(300):
        facility = Facility(
            name=f"Bulk {i}",
            facility_type="clinic",
            address="Enugu",
            phone=phone(),
            location=point(ENUGU[0] + (i % 30) * 0.02, ENUGU[1] + (i // 30) * 0.02),
            verification_status="verified",
        )
        db.add(facility)
        await db.flush()
        db.add(FacilityService(facility_id=facility.id, service_type="emergency_24h"))
    await db.commit()

    start = time.perf_counter()
    resp = await press(api, family)
    elapsed = time.perf_counter() - start
    assert resp.status_code == 201
    assert len(resp.json()["nearby"]["facilities"]) == 5
    assert elapsed < 2.0, f"trigger took {elapsed:.2f}s"


# ------------------------------------------------------------------ delivery and retries
async def test_failed_sms_is_recorded_and_manual_retry_sends_it(api, family, sms):
    sms.fail_numbers.add(family.aunt_phone)
    event_id = (await press(api, family)).json()["id"]

    status = (await api.get(f"/crisis/events/{event_id}", headers=auth(family.mum))).json()
    by_name = {a["recipient_name"]: a for a in status["alerts"]}
    assert by_name["Aunty Ada"]["status"] == "failed" and by_name["Aunty Ada"]["attempts"] == 1
    assert by_name["Ngozi Okafor"]["status"] == "sent"
    (failed,) = await rows(CrisisAlert, CrisisAlert.status == AlertStatus.FAILED)
    assert failed.last_error == "Simulated failure"

    sms.fail_numbers.clear()
    retry = await api.post(
        "/crisis/alert", json={"crisis_event_id": event_id}, headers=auth(family.mum)
    )
    assert retry.status_code == 202
    assert retry.json()["queued"] == 1 and retry.json()["new_recipients"] == 0
    # The 202 is built before the background send runs.
    assert {a["recipient_name"]: a["status"] for a in retry.json()["alerts"]} == {
        "Ngozi Okafor": "sent",
        "Aunty Ada": "queued",
    }
    after = (await api.get(f"/crisis/events/{event_id}", headers=auth(family.mum))).json()
    assert {a["status"] for a in after["alerts"]} == {"sent"}
    assert [m.to for m in sms.outbox].count(family.mum.phone) == 1  # sent alerts aren't resent


async def test_retry_alerts_contacts_added_after_the_crisis_began(api, db, sms):
    alone_user = await make_user(db, UserRole.PATIENT, "Alone")
    alone = await make_patient(db, user=alone_user, created_by=alone_user)
    created = await api.post("/crisis/events", json=trigger_body(alone), headers=auth(alone_user))
    assert created.json()["no_contacts"] is True

    new_number = phone()
    await add_contact(db, alone, "Late Contact", new_number)
    retry = await api.post(
        "/crisis/alert", json={"crisis_event_id": created.json()["id"]}, headers=auth(alone_user)
    )

    assert retry.json()["new_recipients"] == 1
    assert [m.to for m in sms.outbox] == [new_number]


async def test_automatic_retries_stop_at_max_attempts_but_manual_retry_continues(api, family, sms):
    sms.fail_numbers.add(family.aunt_phone)
    event_id = (await press(api, family)).json()["id"]

    for _ in range(5):
        await service.deliver_pending_alerts(database.SessionLocal, sms)
    (aunt,) = await rows(CrisisAlert, CrisisAlert.phone == family.aunt_phone)
    assert aunt.attempts == get_settings().CRISIS_ALERT_MAX_ATTEMPTS == 3
    assert aunt.status is AlertStatus.FAILED

    await api.post("/crisis/alert", json={"crisis_event_id": event_id}, headers=auth(family.mum))
    (aunt,) = await rows(CrisisAlert, CrisisAlert.phone == family.aunt_phone)
    assert aunt.attempts == 4


async def test_sweep_sends_stuck_alerts_but_not_ancient_ones(api, family, sms, monkeypatch):
    no_background_delivery(monkeypatch)
    created = await press(api, family)
    assert {a["status"] for a in created.json()["alerts"]} == {"queued"}

    # Too recent: the sweep leaves alerts alone for CRISIS_ALERT_RETRY_AFTER_SECONDS.
    report = await service.sweep_pending_alerts(database.SessionLocal, sms)
    assert report.sent == 0

    async with database.engine.begin() as conn:
        await conn.execute(update(CrisisAlert).values(updated_at=func.now() - timedelta(minutes=2)))
    report = await service.sweep_pending_alerts(database.SessionLocal, sms)
    assert report.sent == 2 and len(sms.outbox) == 2

    # Alerts older than the sweep's maximum age are left for a human.
    monkeypatch.setattr(get_settings(), "CRISIS_DUPLICATE_WINDOW_SECONDS", 0)
    await press(api, family)
    async with database.engine.begin() as conn:
        await conn.execute(
            update(CrisisAlert)
            .where(CrisisAlert.status == AlertStatus.QUEUED)
            .values(
                created_at=func.now() - timedelta(hours=7),
                updated_at=func.now() - timedelta(hours=7),
            )
        )
    assert (await service.sweep_pending_alerts(database.SessionLocal, sms)).sent == 0


async def test_parallel_delivery_runs_never_double_send(api, db, family, sms, monkeypatch):
    no_background_delivery(monkeypatch)
    for i in range(8):
        await add_contact(db, family.patient, f"Extra {i}", phone(), priority=5)
    await press(api, family)

    reports = await asyncio.gather(
        *(service.deliver_pending_alerts(database.SessionLocal, sms) for _ in range(4))
    )

    assert sum(r.sent for r in reports) == 10
    assert len(sms.outbox) == 10 and len({m.to for m in sms.outbox}) == 10


async def test_alerts_not_yet_sent_are_skipped_if_the_crisis_is_closed(
    api, family, sms, monkeypatch
):
    no_background_delivery(monkeypatch)
    event_id = (await press(api, family)).json()["id"]
    await api.post(
        f"/crisis/events/{event_id}/resolve",
        json={"false_alarm": True},
        headers=auth(family.patient_user),
    )
    report = await service.deliver_pending_alerts(database.SessionLocal, sms)
    assert (report.sent, report.skipped) == (0, 2)
    assert sms.outbox == []
    assert {a.status for a in await rows(CrisisAlert)} == {AlertStatus.SKIPPED}


async def test_unexpected_sms_errors_do_not_stop_other_alerts(api, family, monkeypatch):
    no_background_delivery(monkeypatch)
    await press(api, family)

    class Broken(MockSMSClient):
        async def send(self, to: str, body: str) -> SentMessage:
            if to == family.mum.phone:
                raise RuntimeError("provider SDK bug")
            return await super().send(to, body)

    broken = Broken()
    report = await service.deliver_pending_alerts(database.SessionLocal, broken)
    assert (report.sent, report.failed) == (1, 1)
    (mum_alert,) = await rows(CrisisAlert, CrisisAlert.phone == family.mum.phone)
    assert mum_alert.last_error == "Unexpected error: RuntimeError"


def test_celery_sweep_task_runs_eagerly(monkeypatch):
    from app.domains.crisis.tasks import sweep_alerts
    from app.worker import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    result = sweep_alerts.delay().get(timeout=10)
    assert result == {"sent": 0, "failed": 0, "skipped": 0}


def test_beat_schedules_the_sweep():
    from app.worker import celery_app

    schedule = celery_app.conf.beat_schedule["crisis-sweep-alerts"]
    assert schedule["task"] == "crisis.sweep_alerts" and schedule["schedule"] == 30.0
    assert "crisis.sweep_alerts" in celery_app.tasks and "crisis.send_alerts" in celery_app.tasks


# ------------------------------------------------------------------ resolve
async def test_resolve_with_consent_writes_an_encrypted_passport_entry(api, db, family):
    await grant_consent(db, family.mum)
    event_id = (await press(api, family)).json()["id"]

    resp = await api.post(
        f"/crisis/events/{event_id}/resolve",
        json={"add_to_passport": True, "note": "Started after a long bus journey"},
        headers=auth(family.mum),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "resolved" and body["resolved_at"]
    assert body["outcome_facility_id"] is None
    (entry,) = await rows(PassportEntry)
    assert str(entry.id) == body["passport_entry_id"]
    assert entry.summary == "Crisis button pressed."
    assert entry.details == "Started after a long bus journey"
    assert entry.crisis_event_id == uuid.UUID(event_id)
    assert entry.created_by == family.mum.id and entry.facility_id is None
    async with database.engine.connect() as conn:
        raw_details, raw_summary = (
            await conn.execute(text("SELECT details, summary FROM passport_entries"))
        ).one()
    assert b"bus journey" not in bytes(raw_details)  # ciphertext at rest (HP-4)
    assert b"Crisis button" not in bytes(raw_summary)
    (event,) = await rows(CrisisEvent)
    assert event.resolved_by == family.mum.id and event.status is CrisisStatus.RESOLVED


@needs_facilities
async def test_resolve_can_name_the_facility_they_went_to(api, db, family, facilities):
    await grant_consent(db, family.mum)
    event_id = (await press(api, family)).json()["id"]
    near = str(facilities["near"].id)

    resp = await api.post(
        f"/crisis/events/{event_id}/resolve",
        json={"outcome_facility_id": near, "add_to_passport": True},
        headers=auth(family.mum),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome_facility_id"] == near
    (entry,) = await rows(PassportEntry)
    assert entry.summary == "Crisis button pressed. Went to Near General."
    assert str(entry.facility_id) == near


@pytest.mark.parametrize("consent", [None, "revoked", "withdrawn_later", "old_policy"])
async def test_passport_entry_needs_current_consent(api, db, family, consent):
    user = family.patient_user
    if consent == "revoked":
        await grant_consent(db, user, revoked=True)
    elif consent == "withdrawn_later":
        await grant_consent(db, user, when=datetime.now(UTC) - timedelta(days=2))
        await grant_consent(db, user, granted=False)
    elif consent == "old_policy":
        await grant_consent(db, user, policy_version="2020-01-v0")
    event_id = (await press(api, family)).json()["id"]

    resp = await api.post(
        f"/crisis/events/{event_id}/resolve",
        json={"add_to_passport": True},
        headers=auth(user),
    )

    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "consent_required"
    assert error["details"] == {"consent_type": "health_data", "next": "/auth/consent"}
    assert (await rows(CrisisEvent))[0].status is CrisisStatus.ACTIVE  # nothing changed
    assert await rows(PassportEntry) == []


async def test_resolve_without_passport_needs_no_consent(api, family):
    event_id = (await press(api, family)).json()["id"]
    resp = await api.post(
        f"/crisis/events/{event_id}/resolve", json={}, headers=auth(family.patient_user)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved" and resp.json()["passport_entry_id"] is None


async def test_resolve_rules(api, family):
    event_id = (await press(api, family)).json()["id"]
    url = f"/crisis/events/{event_id}/resolve"

    bad = await api.post(
        url, json={"false_alarm": True, "add_to_passport": True}, headers=auth(family.patient_user)
    )
    assert bad.status_code == 422

    missing_facility = await api.post(
        url, json={"outcome_facility_id": str(uuid.uuid4())}, headers=auth(family.ops)
    )
    assert missing_facility.status_code == 404
    assert missing_facility.json()["error"]["code"] == "facility_not_found"

    stranger = await api.post(url, json={}, headers=auth(family.stranger))
    assert stranger.status_code == 404

    ok = await api.post(url, json={"false_alarm": True}, headers=auth(family.ops))
    assert ok.status_code == 200 and ok.json()["status"] == "cancelled"
    assert ok.json()["passport_entry_id"] is None

    again = await api.post(url, json={}, headers=auth(family.patient_user))
    assert again.status_code == 409 and again.json()["error"]["code"] == "crisis_closed"

    retry = await api.post(
        "/crisis/alert", json={"crisis_event_id": event_id}, headers=auth(family.mum)
    )
    assert retry.status_code == 409


async def test_two_people_resolving_at_once_only_one_succeeds(api, db, family):
    await grant_consent(db, family.patient_user)
    await grant_consent(db, family.mum)
    event_id = (await press(api, family)).json()["id"]
    url = f"/crisis/events/{event_id}/resolve"
    responses = await asyncio.gather(
        api.post(url, json={"add_to_passport": True}, headers=auth(family.patient_user)),
        api.post(url, json={"add_to_passport": True}, headers=auth(family.mum)),
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(await rows(PassportEntry)) == 1


# ------------------------------------------------------------------ audit
async def test_actions_are_audited_without_health_data_or_coordinates(api, db, family):
    await grant_consent(db, family.mum)
    created = await api.post(
        "/crisis/events",
        json=trigger_body(family.patient),
        headers={**auth(family.patient_user), "User-Agent": "CareKin-Android/1.0"},
    )
    event_id = created.json()["id"]
    await api.post(
        f"/crisis/events/{event_id}/resolve",
        json={"add_to_passport": True, "note": "private note"},
        headers=auth(family.mum),
    )

    logs = await rows(AuditLog)
    actions = {(log.action, log.actor_id) for log in logs}
    assert ("crisis.trigger", family.patient_user.id) in actions
    assert ("passport.entry.create", family.mum.id) in actions
    assert ("crisis.resolve", family.mum.id) in actions
    trigger_log = next(log for log in logs if log.action == "crisis.trigger")
    assert trigger_log.meta == {"location_source": "device", "alerts_queued": 2}
    assert trigger_log.user_agent == "CareKin-Android/1.0"
    assert str(trigger_log.entity_id) == event_id
    for log in logs:
        assert "private note" not in str(log.meta) and "6.44" not in str(log.meta)


# ------------------------------------------------------------------ misc
async def test_location_round_trips_through_postgis(api, family):
    created = await press(api, family)
    (event,) = await rows(CrisisEvent)
    assert event.location_source is LocationSource.DEVICE
    assert event.latitude == pytest.approx(ENUGU[0]) and event.longitude == pytest.approx(ENUGU[1])
    assert created.json()["location"]["accuracy_m"] == 12
