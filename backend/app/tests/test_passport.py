"""Day 9 health passport (HP-1 to HP-4): entries, reading, the one-page export, and
consented, time-limited shares with verified facilities."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core import encryption
from app.core.audit import AuditLog
from app.core.permissions import UserRole
from app.domains.auth.models import ConsentRecord, ConsentType, User
from app.domains.facilities.models import Facility
from app.domains.passport import export as pdf
from app.domains.passport.models import PassportEntry, PassportEntryType, PassportShare
from app.domains.patients.models import CareProfile, Patient
from app.tests.crisis_factories import (
    add_family_link,
    add_guardian,
    auth,
    grant_consent,
    make_facility,
    make_patient,
    make_user,
)


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_fernet", lambda: key)


@dataclass
class World:
    me: User
    patient: Patient
    guardian: User
    child: Patient
    family: User  # health_data shared
    viewer: User  # linked, health data not shared
    admin: User  # manages `hospital`
    other_admin: User  # manages `clinic`
    hospital: Facility
    clinic: Facility
    unverified: Facility
    caregiver: User
    ops: User
    stranger: User


@pytest.fixture
async def w(db) -> World:
    me = await make_user(db, UserRole.PATIENT, "Chidi Okafor")
    await grant_consent(db, me)
    patient = await make_patient(db, user=me, created_by=me)
    patient.genotype, patient.blood_group, patient.sex = "HbSS", "O+", "male"
    db.add(
        CareProfile(
            patient_id=patient.id,
            allergies="Penicillin",
            known_triggers="Cold weather",
            medications_overview="Folic acid",
        )
    )
    guardian = await make_user(db, UserRole.GUARDIAN, "Ngozi Okafor")
    await grant_consent(db, guardian)
    child = Patient(
        user_id=None,
        full_name="Ụzọamaka Okafor",
        date_of_birth=date(2016, 3, 2),
        created_by=guardian.id,
    )
    db.add(child)
    await db.commit()
    await add_guardian(db, child, guardian, primary=True)
    family = await make_user(db, UserRole.FAMILY_ABROAD, "Ada in London")
    await add_family_link(db, patient, family, {"health_data": True})
    viewer = await make_user(db, UserRole.FAMILY_ABROAD, "Cousin")
    await add_family_link(db, patient, viewer, {"location": True})
    admin = await make_user(db, UserRole.FACILITY_ADMIN, "Parklane Desk")
    other_admin = await make_user(db, UserRole.FACILITY_ADMIN, "Clinic Desk")
    hospital = await make_facility(db, "ESUT Teaching Hospital Parklane", 6.45, 7.50)
    clinic = await make_facility(db, "Ogui Clinic", 6.44, 7.49)
    unverified = await make_facility(db, "New Clinic", 6.43, 7.48, status="pending")
    hospital.claimed_by, clinic.claimed_by = admin.id, other_admin.id
    await db.commit()
    return World(
        me=me,
        patient=patient,
        guardian=guardian,
        child=child,
        family=family,
        viewer=viewer,
        admin=admin,
        other_admin=other_admin,
        hospital=hospital,
        clinic=clinic,
        unverified=unverified,
        caregiver=await make_user(db, UserRole.CAREGIVER, "Carer"),
        ops=await make_user(db, UserRole.OPS, "Ops"),
        stranger=await make_user(db, UserRole.PATIENT, "Stranger"),
    )


def entry(
    kind: str = "visit",
    days_ago: int = 1,
    summary: str = "Reviewed at the haematology clinic",
    **extra,
):
    return {
        "entry_type": kind,
        "occurred_at": (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),
        "summary": summary,
        **extra,
    }


async def add(api, w: World, headers=None, patient=None, **kwargs):
    response = await api.post(
        f"/passport/{(patient or w.patient).id}/entries",
        json=entry(**kwargs),
        headers=headers or auth(w.me),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def share(api, w: World, facility=None, headers=None, **body):
    return await api.post(
        f"/passport/{w.patient.id}/share",
        json={"facility_id": str((facility or w.hospital).id), **body},
        headers=headers or auth(w.me),
    )


async def actions(db) -> list[str]:
    return list(await db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


def page_count(content: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", content))


# ================================================================== entries (HP-1)
async def test_add_entry_is_encrypted_and_audited(api, db, w):
    body = await add(
        api,
        w,
        kind="transfusion",
        summary="Two units, ward 4",
        details="Haemoglobin was low",
        facility_id=str(w.hospital.id),
    )
    assert body["entry_type"] == "transfusion" and body["facility_name"] == w.hospital.name
    assert body["created_by"] == str(w.me.id)
    raw = (await db.execute(text("SELECT summary, details FROM passport_entries"))).one()
    assert b"Two units" not in bytes(raw[0]) and b"Haemoglobin" not in bytes(raw[1])
    assert "passport.entry.create" in await actions(db)
    meta = await db.scalar(select(AuditLog.meta).where(AuditLog.action == "passport.entry.create"))
    assert "Two units" not in str(meta)


async def test_guardian_adds_for_a_child(api, w):
    body = await add(api, w, headers=auth(w.guardian), patient=w.child, kind="crisis")
    assert body["patient_id"] == str(w.child.id)


async def test_entry_needs_health_data_consent(api, db):
    user = await make_user(db, UserRole.PATIENT, "No Consent")
    patient = await make_patient(db, user=user, created_by=user)
    response = await api.post(f"/passport/{patient.id}/entries", json=entry(), headers=auth(user))
    assert response.status_code == 403 and response.json()["error"]["code"] == "consent_required"


async def test_withdrawn_consent_blocks_new_entries(api, db):
    user = await make_user(db, UserRole.PATIENT, "Withdrew")
    await grant_consent(db, user, revoked=True)
    patient = await make_patient(db, user=user, created_by=user)
    response = await api.post(f"/passport/{patient.id}/entries", json=entry(), headers=auth(user))
    assert response.status_code == 403


async def test_who_may_add_entries(api, db, w):
    url = f"/passport/{w.patient.id}/entries"
    assert (await api.post(url, json=entry(), headers=auth(w.family))).status_code == 403
    for user in (w.caregiver, w.ops, w.stranger, w.admin):
        assert (await api.post(url, json=entry(), headers=auth(user))).status_code == 404
    await share(api, w)
    assert (await api.post(url, json=entry(), headers=auth(w.admin))).status_code == 403
    assert list(await db.scalars(select(PassportEntry))) == []


@pytest.mark.parametrize(
    "body",
    [
        {**entry(), "occurred_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        {**entry(), "occurred_at": "1980-01-01T00:00:00+00:00"},
        {**entry(), "occurred_at": "2026-10-01T10:00:00"},
        {**entry(), "entry_type": "diagnosis"},
        {**entry(), "summary": "   "},
        {**entry(), "summary": "x" * 501},
        {**entry(), "dose": "2 tablets"},
    ],
    ids=["future", "before-birth", "no-timezone", "bad-type", "blank", "too-long", "extra-field"],
)
async def test_entry_validation(api, w, body):
    response = await api.post(f"/passport/{w.patient.id}/entries", json=body, headers=auth(w.me))
    assert response.status_code == 422, response.text


async def test_entry_with_unknown_facility(api, w):
    response = await api.post(
        f"/passport/{w.patient.id}/entries",
        json=entry(facility_id=str(uuid.uuid4())),
        headers=auth(w.me),
    )
    assert response.status_code == 404 and response.json()["error"]["code"] == "facility_not_found"


async def test_unknown_patient_is_404(api, w):
    response = await api.post(f"/passport/{uuid.uuid4()}/entries", json=entry(), headers=auth(w.me))
    assert response.status_code == 404


# ================================================================== reading (HP-4)
async def test_owner_reads_everything_newest_first_and_each_view_is_audited(api, db, w):
    await add(api, w, kind="visit", days_ago=30)
    await add(api, w, kind="crisis", days_ago=2, summary="Severe pain")
    await add(api, w, kind="medication", days_ago=10, summary="Started folic acid")
    response = await api.get(f"/passport/{w.patient.id}", headers=auth(w.me))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [e["entry_type"] for e in body["entries"]] == ["crisis", "medication", "visit"]
    assert body["total_entries"] == 3
    assert body["patient"]["genotype"] == "HbSS" and body["profile"]["allergies"] == "Penicillin"
    assert body["access"] == {
        "via": "self",
        "can_manage": True,
        "share_id": None,
        "facility_id": None,
        "expires_at": None,
        "entry_types": ["crisis", "trigger", "visit", "transfusion", "medication"],
    }
    assert "not a diagnosis" in body["disclaimer"]
    await api.get(f"/passport/{w.patient.id}", headers=auth(w.me))
    views = list(await db.scalars(select(AuditLog).where(AuditLog.action == "passport.view")))
    assert len(views) == 2 and views[0].actor_id == w.me.id and views[0].meta["entries"] == 3


async def test_filters_and_paging(api, w):
    for i in range(5):
        await add(api, w, kind="crisis" if i % 2 else "visit", days_ago=i + 1)
    url = f"/passport/{w.patient.id}"
    crises = (await api.get(url, params={"entry_type": "crisis"}, headers=auth(w.me))).json()
    assert {e["entry_type"] for e in crises["entries"]} == {"crisis"} and crises[
        "total_entries"
    ] == 2
    recent = (
        await api.get(
            url,
            params={"since": (datetime.now(UTC) - timedelta(days=2, hours=12)).isoformat()},
            headers=auth(w.me),
        )
    ).json()
    assert recent["total_entries"] == 2
    page = (await api.get(url, params={"limit": 2, "offset": 2}, headers=auth(w.me))).json()
    assert len(page["entries"]) == 2 and page["total_entries"] == 5
    assert (await api.get(url, params={"limit": 0}, headers=auth(w.me))).status_code == 422


async def test_family_reads_only_when_health_data_is_shared(api, w):
    await add(api, w)
    shared = await api.get(f"/passport/{w.patient.id}", headers=auth(w.family))
    assert shared.status_code == 200 and shared.json()["access"]["via"] == "family"
    assert shared.json()["access"]["can_manage"] is False
    not_shared = await api.get(f"/passport/{w.patient.id}", headers=auth(w.viewer))
    assert (
        not_shared.status_code == 403
        and not_shared.json()["error"]["code"] == "health_data_not_shared"
    )


async def test_caregivers_ops_strangers_and_unshared_facilities_get_404(api, w):
    for user in (w.caregiver, w.ops, w.stranger, w.admin):
        assert (await api.get(f"/passport/{w.patient.id}", headers=auth(user))).status_code == 404
    assert (await api.get(f"/passport/{uuid.uuid4()}", headers=auth(w.me))).status_code == 404
    assert (await api.get(f"/passport/{w.patient.id}")).status_code == 401


async def test_a_resolved_crisis_appears_in_the_passport_and_export(api, w, monkeypatch):
    """Crisis day's done-when: the resolved crisis shows in the passport export (CR-4)."""
    from app.domains.crisis import router as crisis_router

    monkeypatch.setattr(crisis_router, "_send_alerts", lambda *a, **k: None)
    pressed = await api.post(
        "/crisis/events",
        json={"patient_id": str(w.patient.id), "client_event_id": str(uuid.uuid4())},
        headers=auth(w.me),
    )
    assert pressed.status_code == 201, pressed.text
    event_id = pressed.json()["id"]
    resolved = await api.post(
        f"/crisis/events/{event_id}/resolve",
        json={"add_to_passport": True, "outcome_facility_id": str(w.hospital.id)},
        headers=auth(w.me),
    )
    assert resolved.status_code == 200, resolved.text
    body = (await api.get(f"/passport/{w.patient.id}", headers=auth(w.me))).json()
    crisis = body["entries"][0]
    assert crisis["entry_type"] == "crisis" and crisis["crisis_event_id"] == event_id
    assert crisis["facility_name"] == w.hospital.name
    exported = await api.get(f"/passport/{w.patient.id}/export", headers=auth(w.me))
    assert exported.headers["x-entries-shown"] == "1"


# ================================================================== shares (HP-3)
async def test_share_records_consent_and_facility_reads_only_the_shared_scope(api, db, w):
    await add(api, w, kind="crisis", summary="Severe pain")
    await add(api, w, kind="visit", summary="Clinic review")
    await add(api, w, kind="transfusion", summary="Two units")
    response = await share(
        api, w, entry_types=["visit", "transfusion", "visit"], expires_in_hours=48
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "active" and body["entry_types"] == ["visit", "transfusion"]
    assert body["facility_name"] == w.hospital.name
    consent = await db.get(ConsentRecord, uuid.UUID(body["consent_record_id"]))
    assert consent.consent_type is ConsentType.PASSPORT_SHARE and consent.granted
    assert consent.patient_id == w.patient.id and consent.user_id == w.me.id
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(hours=47, minutes=59) < expires - datetime.now(UTC) <= timedelta(hours=48)
    assert {"consent.grant", "passport.share"} <= set(await actions(db))

    seen = await api.get(f"/passport/{w.patient.id}", headers=auth(w.admin))
    assert seen.status_code == 200
    read = seen.json()
    assert {e["entry_type"] for e in read["entries"]} == {"visit", "transfusion"}
    assert read["total_entries"] == 2
    assert read["access"]["via"] == "facility" and read["access"]["share_id"] == body["id"]
    asked = await api.get(
        f"/passport/{w.patient.id}", params={"entry_type": "crisis"}, headers=auth(w.admin)
    )
    assert asked.json()["entries"] == []  # can't widen the scope with a filter
    view = await db.scalar(
        select(AuditLog).where(AuditLog.action == "passport.view", AuditLog.actor_id == w.admin.id)
    )
    assert view.meta["via"] == "facility" and view.meta["share_id"] == body["id"]


async def test_share_without_profile_hides_genotype_and_profile(api, w):
    await share(api, w, include_profile=False)
    body = (await api.get(f"/passport/{w.patient.id}", headers=auth(w.admin))).json()
    assert body["profile"] is None
    assert body["patient"]["genotype"] is None and body["patient"]["blood_group"] is None


async def test_revoked_share_returns_403(api, db, w):
    created = (await share(api, w)).json()
    assert (await api.get(f"/passport/{w.patient.id}", headers=auth(w.admin))).status_code == 200
    revoked = await api.delete(
        f"/passport/{w.patient.id}/share/{created['id']}", headers=auth(w.me)
    )
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    for path in ("", "/export"):
        response = await api.get(f"/passport/{w.patient.id}{path}", headers=auth(w.admin))
        assert response.status_code == 403 and response.json()["error"]["code"] == "share_revoked"
    consent = await db.get(
        ConsentRecord, uuid.UUID(created["consent_record_id"]), populate_existing=True
    )
    assert consent.revoked_at is not None
    again = await api.delete(f"/passport/{w.patient.id}/share/{created['id']}", headers=auth(w.me))
    assert again.status_code == 200 and again.json()["revoked_at"] == revoked.json()["revoked_at"]
    assert (await actions(db)).count("passport.unshare") == 1


async def test_expired_share_returns_403(api, db, w):
    created = (await share(api, w, expires_in_hours=1)).json()
    await db.execute(
        text(
            "UPDATE passport_shares SET created_at = now() - interval '3 hours', "
            "expires_at = now() - interval '1 hour' WHERE id = :id"
        ),
        {"id": created["id"]},
    )
    await db.commit()
    response = await api.get(f"/passport/{w.patient.id}", headers=auth(w.admin))
    assert response.status_code == 403 and response.json()["error"]["code"] == "share_expired"
    shares = (await api.get(f"/passport/{w.patient.id}/shares", headers=auth(w.me))).json()
    assert shares[0]["status"] == "expired"


async def test_other_facilities_cannot_read(api, w):
    await share(api, w)
    assert (
        await api.get(f"/passport/{w.patient.id}", headers=auth(w.other_admin))
    ).status_code == 404


async def test_only_verified_facilities(api, w):
    response = await share(api, w, facility=w.unverified)
    assert (
        response.status_code == 422 and response.json()["error"]["code"] == "facility_not_verified"
    )
    missing = await api.post(
        f"/passport/{w.patient.id}/share",
        json={"facility_id": str(uuid.uuid4())},
        headers=auth(w.me),
    )
    assert missing.status_code == 404


async def test_one_active_share_per_facility_and_resharing_after_revoke(api, w):
    first = (await share(api, w)).json()
    duplicate = await share(api, w)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "share_exists"
    assert duplicate.json()["error"]["details"]["share_id"] == first["id"]
    await api.delete(f"/passport/{w.patient.id}/share/{first['id']}", headers=auth(w.me))
    second = await share(api, w, entry_types=["crisis"])
    assert second.status_code == 201
    body = (await api.get(f"/passport/{w.patient.id}", headers=auth(w.admin))).json()
    assert body["access"]["share_id"] == second.json()["id"] and body["access"]["entry_types"] == [
        "crisis"
    ]


@pytest.mark.parametrize(
    "extra",
    [
        {"entry_types": []},
        {"entry_types": ["diagnosis"]},
        {"expires_in_hours": 0},
        {"expires_in_hours": 721},
        {"facility_id": "not-a-uuid"},
        {"scope": "all"},
    ],
    ids=["no-types", "bad-type", "zero-hours", "too-long", "bad-facility", "extra-field"],
)
async def test_share_validation(api, w, extra):
    assert (await share(api, w, **extra)).status_code == 422


async def test_who_may_share_list_and_revoke(api, w):
    created = (await share(api, w)).json()
    for user in (w.family, w.admin):
        assert (await share(api, w, facility=w.clinic, headers=auth(user))).status_code == 403
        assert (
            await api.get(f"/passport/{w.patient.id}/shares", headers=auth(user))
        ).status_code == 403
        url = f"/passport/{w.patient.id}/share/{created['id']}"
        assert (await api.delete(url, headers=auth(user))).status_code == 403
    for user in (w.stranger, w.caregiver):
        assert (await share(api, w, facility=w.clinic, headers=auth(user))).status_code == 404


async def test_guardian_shares_a_childs_passport(api, db, w):
    await add(api, w, headers=auth(w.guardian), patient=w.child, kind="crisis")
    response = await api.post(
        f"/passport/{w.child.id}/share",
        json={"facility_id": str(w.hospital.id)},
        headers=auth(w.guardian),
    )
    assert response.status_code == 201
    consent = await db.get(ConsentRecord, uuid.UUID(response.json()["consent_record_id"]))
    assert consent.user_id == w.guardian.id and consent.patient_id == w.child.id
    assert (await api.get(f"/passport/{w.child.id}", headers=auth(w.admin))).json()[
        "total_entries"
    ] == 1


async def test_revoking_a_share_of_another_patient_is_404(api, w):
    created = (await share(api, w)).json()
    await add(api, w, headers=auth(w.guardian), patient=w.child)
    response = await api.delete(
        f"/passport/{w.child.id}/share/{created['id']}", headers=auth(w.guardian)
    )
    assert response.status_code == 404
    unknown = await api.delete(f"/passport/{w.patient.id}/share/{uuid.uuid4()}", headers=auth(w.me))
    assert unknown.status_code == 404


async def test_received_shares_for_the_facility_portal(api, w):
    first = (await share(api, w)).json()
    await api.post(
        f"/passport/{w.child.id}/share",
        json={"facility_id": str(w.hospital.id)},
        headers=auth(w.guardian),
    )
    received = (await api.get("/passport/shares/received", headers=auth(w.admin))).json()
    assert {r["patient_name"] for r in received} == {"Chidi Okafor", "Ụzọamaka Okafor"}
    await api.delete(f"/passport/{w.patient.id}/share/{first['id']}", headers=auth(w.me))
    received = (await api.get("/passport/shares/received", headers=auth(w.admin))).json()
    assert [r["patient_name"] for r in received] == ["Ụzọamaka Okafor"]
    assert (await api.get("/passport/shares/received", headers=auth(w.other_admin))).json() == []
    assert (await api.get("/passport/shares/received", headers=auth(w.me))).status_code == 403


async def test_database_guards_on_shares(db, w):
    consent = ConsentRecord(
        user_id=w.me.id, consent_type=ConsentType.PASSPORT_SHARE, policy_version="x", granted=True
    )
    db.add(consent)
    await db.commit()
    consent_id, patient_id, facility_id = consent.id, w.patient.id, w.hospital.id
    now = datetime.now(UTC)
    for bad in (
        {"entry_types": ["diagnosis"], "expires_at": now + timedelta(hours=1)},
        {"entry_types": [], "expires_at": now + timedelta(hours=1)},
        {"entry_types": ["visit"], "expires_at": now - timedelta(hours=1)},
    ):
        db.add(
            PassportShare(
                patient_id=patient_id,
                facility_id=facility_id,
                consent_record_id=consent_id,
                created_at=now,
                **bad,
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


# ================================================================== export (HP-2)
async def test_export_is_one_page_pdf_and_audited(api, db, w):
    await add(api, w, kind="crisis", summary="Severe pain <b>bold?</b> & fever")
    response = await api.get(f"/passport/{w.patient.id}/export", headers=auth(w.me))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert "carekin-health-passport.pdf" in response.headers["content-disposition"]
    assert w.patient.full_name not in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF") and page_count(response.content) == 1
    assert response.headers["x-entries-shown"] == "1" and response.headers["x-entries-total"] == "1"
    exported = await db.scalar(select(AuditLog).where(AuditLog.action == "passport.export"))
    assert exported.actor_id == w.me.id and exported.meta["entries_shown"] == 1


async def test_export_stays_on_one_page_with_many_long_entries(api, db, w):
    for i in range(80):
        db.add(
            PassportEntry(
                patient_id=w.patient.id,
                entry_type=list(PassportEntryType)[i % 5],
                occurred_at=datetime.now(UTC) - timedelta(days=i),
                summary=("Long summary with words " * 20) + ("x" * 150),
                facility_id=w.hospital.id,
            )
        )
    await db.commit()
    response = await api.get(f"/passport/{w.patient.id}/export", headers=auth(w.me))
    assert page_count(response.content) == 1
    shown, total = (
        int(response.headers["x-entries-shown"]),
        int(response.headers["x-entries-total"]),
    )
    assert 0 < shown < total == 80


async def test_facility_export_covers_only_the_shared_types(api, w):
    await add(api, w, kind="crisis")
    await add(api, w, kind="visit")
    await share(api, w, entry_types=["visit"])
    response = await api.get(f"/passport/{w.patient.id}/export", headers=auth(w.admin))
    assert response.status_code == 200 and response.headers["x-entries-total"] == "1"


async def test_export_access(api, w):
    assert (
        await api.get(f"/passport/{w.patient.id}/export", headers=auth(w.family))
    ).status_code == 200
    assert (
        await api.get(f"/passport/{w.patient.id}/export", headers=auth(w.viewer))
    ).status_code == 403
    for user in (w.stranger, w.caregiver, w.ops, w.admin):
        assert (
            await api.get(f"/passport/{w.patient.id}/export", headers=auth(user))
        ).status_code == 404


async def test_empty_passport_exports(api, w):
    response = await api.get(f"/passport/{w.child.id}/export", headers=auth(w.guardian))
    assert response.status_code == 200 and page_count(response.content) == 1
    assert response.headers["x-entries-total"] == "0"


# ================================================================== renderer units
def _input(entries, **extra):
    return pdf.ExportInput(
        full_name="Ụzọamaka Okafor",
        date_of_birth=date(2016, 3, 2),
        sex=None,
        genotype="HbSS",
        blood_group=None,
        entries=entries,
        exported_at=datetime.now(UTC),
        exported_for="Patient",
        **extra,
    )


def test_layout_widths_add_up_to_the_content_width():
    assert pdf.CONTENT_W == pytest.approx(pdf.PAGE_W - 2 * pdf.MARGIN_X)
    for cols in (pdf.ENTRY_COLS, pdf.PATIENT_COLS, pdf.PROFILE_COLS):
        assert sum(cols) == pytest.approx(pdf.CONTENT_W)


def test_renderer_handles_hostile_and_huge_input():
    now = datetime.now(UTC)
    nasty = [
        pdf.ExportEntry(
            now - timedelta(hours=i),
            "crisis",
            "<para><font size=99>" + "A" * 3000 + "&amp;</para>\x00",
            "<b>" * 40,
        )
        for i in range(50)
    ]
    result = pdf.render(_input(nasty, care_instructions="</para>" * 500, allergies="&" * 900))
    assert result.pages == 1 and page_count(result.pdf) == 1
    assert 0 < result.entries_shown <= 50 and result.entries_total == 50


def test_renderer_without_dejavu_strips_accents_instead_of_failing(monkeypatch):
    monkeypatch.setattr(pdf, "_DEJAVU_DIRS", ())
    pdf.fonts.cache_clear()
    try:
        result = pdf.render(_input([pdf.ExportEntry(datetime.now(UTC), "visit", "Ọ́kụ ₦ test")]))
        assert result.pages == 1 and result.unicode_font is False
        assert pdf._plain("Ụzọamaka", False) == "Uzoamaka"
    finally:
        pdf.fonts.cache_clear()


def test_stats():
    now = datetime.now(UTC)
    entries = [
        pdf.ExportEntry(now - timedelta(days=10), "crisis", "a"),
        pdf.ExportEntry(now - timedelta(days=400), "crisis", "old"),
        pdf.ExportEntry(now - timedelta(days=20), "transfusion", "b"),
        pdf.ExportEntry(now - timedelta(days=5), "visit", "c"),
    ]
    stats = pdf._stats(entries, now)
    assert stats["Crises, last 12 months"] == "1"
    assert stats["Transfusions, last 12 months"] == "1"
    assert stats["Last hospital visit"] == pdf._when(now - timedelta(days=5))
    assert pdf._stats([], now)["Last hospital visit"] == "None recorded"
