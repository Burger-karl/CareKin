"""Tests: Patients, guardians, emergency contacts, care profile, family links (ERD 2.2).

End-to-end tests run against real Postgres/PostGIS through the API, with users
created by the auth flow. Unit tests at the top need no database.
"""

import struct
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from geoalchemy2.elements import WKBElement
from pydantic import ValidationError
from sqlalchemy import select, text

from app.core import encryption
from app.core.audit import AuditLog
from app.core.permissions import UserRole
from app.core.security import create_access_token
from app.domains.auth.models import User
from app.domains.patients import models, schemas, service


@pytest.fixture(autouse=True)
def fernet(monkeypatch):
    """Tests don't set FIELD_ENCRYPTION_KEY; use a throwaway key."""
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_fernet", lambda: key)
    return key


def _dob(age_years: int) -> str:
    """Date of birth for someone who turned `age_years` a few days ago."""
    day = datetime.now(UTC).date() - timedelta(days=3)
    try:
        return day.replace(year=day.year - age_years).isoformat()
    except ValueError:  # 29 February
        return day.replace(year=day.year - age_years, day=28).isoformat()


def _patient(age_years: int) -> models.Patient:
    return models.Patient(
        full_name="Test", date_of_birth=datetime.fromisoformat(_dob(age_years)).date()
    )


# ================================================================ unit tests
def test_encrypted_text_round_trip():
    column = models.EncryptedText()
    stored = column.process_bind_param("SS", None)
    assert stored != b"SS"
    assert column.process_result_value(stored, None) == "SS"
    assert column.process_bind_param(None, None) is None


def test_encrypted_text_wrong_key_fails(monkeypatch):
    stored = models.EncryptedText().process_bind_param("SS", None)
    other = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_fernet", lambda: other)
    with pytest.raises(RuntimeError, match="decrypt"):
        models.EncryptedText().process_result_value(stored, None)


@pytest.mark.parametrize(
    "raw",
    [
        struct.pack("<BIdd", 1, 1, 3.35, 6.52),  # WKB
        struct.pack("<BIIdd", 1, 0x20000001, 4326, 3.35, 6.52),  # EWKB
        WKBElement(struct.pack("<BIIdd", 1, 0x20000001, 4326, 3.35, 6.52).hex()),
    ],
)
def test_point_from_wkb(raw):
    point = service._from_wkb(raw)
    assert (point.latitude, point.longitude) == (6.52, 3.35)


@pytest.mark.parametrize(
    ("schema", "data"),
    [
        (schemas.PatientCreate, {"full_name": "A", "date_of_birth": "2099-01-01"}),
        (
            schemas.PatientCreate,
            {"full_name": "A", "date_of_birth": "2015-01-01", "for_self": False},
        ),
        (
            schemas.PatientCreate,
            {"full_name": "A", "date_of_birth": "2000-01-01", "guardian_relationship": "x"},
        ),
        (schemas.PatientCreate, {"full_name": "A", "date_of_birth": "2000-01-01", "x": 1}),
        (schemas.PatientUpdate, {"full_name": None}),
        (
            schemas.EmergencyContactCreate,
            {"name": "M", "phone": "08012345678", "relationship": "mother"},
        ),
        (schemas.FamilyLinkCreate, {"user_id": str(uuid.uuid4()), "share_scope": {"bank": True}}),
    ],
)
def test_invalid_input_rejected(schema, data):
    with pytest.raises(ValidationError):
        schema(**data)


def test_minor_status_is_derived():
    assert _patient(10).is_minor
    assert not _patient(30).is_minor
    assert _patient(30).age == 30


def test_access_rules():
    adult, kid = _patient(30), _patient(10)
    assert service.PatientAccess(adult, "self").can_manage_access
    assert not service.PatientAccess(kid, "self").can_manage_access
    assert not service.PatientAccess(kid, "guardian").can_manage_access
    assert service.PatientAccess(kid, "guardian", is_primary_guardian=True).can_manage_access
    family = service.PatientAccess(adult, "family", share_scope={"location": True})
    assert not family.can_edit
    assert not family.sees_health_data
    assert family.sees_location


# ================================================================ end-to-end
async def login(api, latest_code, phone, role="patient", consent=True):
    """Register and verify a user; returns (auth headers, user id)."""
    r = await api.post(
        "/auth/register", json={"phone": phone, "full_name": "Ada Obi", "role": role}
    )
    assert r.status_code == 201, r.text
    e164 = r.json()["user"]["phone"]
    r = await api.post("/auth/otp/verify", json={"phone": e164, "code": latest_code(e164)})
    assert r.status_code == 200, r.text
    tokens = r.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    if consent:
        r = await api.post(
            "/auth/consent", json={"consent_type": "health_data", "granted": True}, headers=headers
        )
        assert r.status_code == 201, r.text
    return headers, uuid.UUID(tokens["user"]["id"])


async def family_user(db):
    """family_abroad can't self-register, so create one directly."""
    user = User(phone="+2348039999999", full_name="Uche Abroad", role=UserRole.FAMILY_ABROAD)
    db.add(user)
    await db.commit()
    token, _ = create_access_token(user.id, UserRole.FAMILY_ABROAD.value)
    return {"Authorization": f"Bearer {token}"}, user.id


async def actions(db):
    return list((await db.scalars(select(AuditLog.action).order_by(AuditLog.id))).all())


async def test_requires_login(api):
    r = await api.get("/patients")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "not_authenticated"


async def test_patient_registers_self(api, latest_code, db):
    h, user_id = await login(api, latest_code, "08031230001")
    r = await api.post(
        "/patients",
        json={
            "full_name": "Ada Obi",
            "date_of_birth": _dob(25),
            "genotype": "ss",
            "blood_group": "O+",
            "home_location": {"latitude": 6.5244, "longitude": 3.3792},
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user_id"] == str(user_id)
    assert (body["access"], body["genotype"], body["age"], body["is_minor"]) == (
        "self",
        "SS",
        25,
        False,
    )
    assert body["home_location"] == {"latitude": 6.5244, "longitude": 3.3792}

    # Ciphertext at rest, not the genotype.
    raw = await db.scalar(text("SELECT genotype FROM patients"))
    assert raw is not None and b"SS" not in bytes(raw)

    listed = (await api.get("/patients", headers=h)).json()
    assert [p["id"] for p in listed] == [body["id"]]

    r = await api.post(
        "/patients", json={"full_name": "Again", "date_of_birth": _dob(25)}, headers=h
    )
    assert r.status_code == 409
    assert "patient.create" in await actions(db)
    assert "patient.view" in await actions(db)


async def test_health_data_needs_consent(api, latest_code):
    h, _ = await login(api, latest_code, "08031230002", consent=False)
    r = await api.post(
        "/patients", json={"full_name": "A", "date_of_birth": _dob(30), "genotype": "SS"}, headers=h
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "consent_required"
    r = await api.post("/patients", json={"full_name": "A", "date_of_birth": _dob(30)}, headers=h)
    assert r.status_code == 201
    pid = r.json()["id"]
    r = await api.put(f"/patients/{pid}/care-profile", json={"allergies": "x"}, headers=h)
    assert r.status_code == 403


async def test_minor_cannot_register_self(api, latest_code):
    h, _ = await login(api, latest_code, "08031230003")
    r = await api.post("/patients", json={"full_name": "Kid", "date_of_birth": _dob(12)}, headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "patient_rule_violation"


async def test_roles_decide_who_registers_whom(api, latest_code):
    patient_h, _ = await login(api, latest_code, "08031230004", role="patient")
    guardian_h, _ = await login(api, latest_code, "08031230005", role="guardian")
    minor = {"full_name": "Kid", "date_of_birth": _dob(10), "for_self": False}
    minor["guardian_relationship"] = "parent"
    assert (await api.post("/patients", json=minor, headers=patient_h)).status_code == 403
    adult = {"full_name": "Me", "date_of_birth": _dob(40)}
    assert (await api.post("/patients", json=adult, headers=guardian_h)).status_code == 403


async def test_guardian_registers_minor_and_keeps_a_primary(api, latest_code):
    h, guardian_id = await login(api, latest_code, "08031230006", role="guardian")
    second_h, second_id = await login(api, latest_code, "08031230007", role="guardian")
    r = await api.post(
        "/patients",
        json={
            "full_name": "Chidi Obi",
            "date_of_birth": _dob(9),
            "for_self": False,
            "guardian_relationship": "Parent",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    child = r.json()
    assert (child["user_id"], child["access"], child["is_minor"]) == (None, "guardian", True)
    pid = child["id"]

    [primary] = (await api.get(f"/patients/{pid}/guardians", headers=h)).json()
    assert (primary["user_id"], primary["is_primary"], primary["relationship"]) == (
        str(guardian_id),
        True,
        "parent",
    )

    # The only primary guardian of a minor can't be removed or demoted.
    r = await api.delete(f"/patients/{pid}/guardians/{primary['id']}", headers=h)
    assert r.status_code == 422
    r = await api.patch(
        f"/patients/{pid}/guardians/{primary['id']}", json={"is_primary": False}, headers=h
    )
    assert r.status_code == 422

    r = await api.post(
        f"/patients/{pid}/guardians",
        json={"user_id": str(second_id), "relationship": "aunt"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    second = r.json()
    assert (
        await api.post(
            f"/patients/{pid}/guardians",
            json={"user_id": str(second_id), "relationship": "aunt"},
            headers=h,
        )
    ).status_code == 409

    # A non-primary guardian can read and edit, but not manage access.
    assert (await api.get(f"/patients/{pid}", headers=second_h)).status_code == 200
    r = await api.delete(f"/patients/{pid}/guardians/{primary['id']}", headers=second_h)
    assert r.status_code == 403

    # Promoting the second guardian demotes the first; then the first can be removed.
    r = await api.patch(
        f"/patients/{pid}/guardians/{second['id']}", json={"is_primary": True}, headers=h
    )
    assert r.json()["is_primary"] is True
    guardians = {
        g["id"]: g for g in (await api.get(f"/patients/{pid}/guardians", headers=h)).json()
    }
    assert guardians[primary["id"]]["is_primary"] is False
    r = await api.delete(f"/patients/{pid}/guardians/{primary['id']}", headers=second_h)
    assert r.status_code == 204


async def test_strangers_get_404(api, latest_code):
    h, _ = await login(api, latest_code, "08031230008")
    stranger_h, _ = await login(api, latest_code, "08031230009")
    pid = (
        await api.post("/patients", json={"full_name": "A", "date_of_birth": _dob(30)}, headers=h)
    ).json()["id"]
    r = await api.get(f"/patients/{pid}", headers=stranger_h)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "patient_not_found"
    assert (await api.get(f"/patients/{uuid.uuid4()}", headers=h)).status_code == 404


async def test_care_profile_is_encrypted_and_audited_without_values(api, latest_code, db):
    h, user_id = await login(api, latest_code, "08031230010")
    pid = (
        await api.post("/patients", json={"full_name": "A", "date_of_birth": _dob(30)}, headers=h)
    ).json()["id"]
    assert (await api.get(f"/patients/{pid}/care-profile", headers=h)).status_code == 404

    r = await api.put(
        f"/patients/{pid}/care-profile",
        json={"known_triggers": "cold weather", "allergies": "penicillin"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["allergies"] == "penicillin"
    assert r.json()["updated_by"] == str(user_id)
    r = await api.get(f"/patients/{pid}/care-profile", headers=h)
    assert r.json()["known_triggers"] == "cold weather"

    raw = await db.scalar(text("SELECT allergies FROM care_profiles"))
    assert b"penicillin" not in bytes(raw)
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "care_profile.update"))
    assert entry.meta["fields"] == ["allergies", "known_triggers"]
    assert "penicillin" not in str(entry.meta)
    assert "care_profile.view" in await actions(db)


async def test_family_link_sees_only_what_is_shared(api, latest_code, db):
    h, _ = await login(api, latest_code, "08031230011")
    family_h, family_id = await family_user(db)
    pid = (
        await api.post(
            "/patients",
            json={"full_name": "A", "date_of_birth": _dob(30), "genotype": "SC"},
            headers=h,
        )
    ).json()["id"]
    r = await api.post(f"/patients/{pid}/family-links", json={"user_id": str(family_id)}, headers=h)
    assert r.status_code == 201, r.text
    link_id = r.json()["id"]

    r = await api.get(f"/patients/{pid}", headers=family_h)
    assert r.status_code == 200, r.text
    seen = r.json()
    assert (seen["access"], seen["genotype"], seen["full_name"]) == ("family", None, "A")
    assert (await api.get(f"/patients/{pid}/care-profile", headers=family_h)).status_code == 403
    assert (
        await api.patch(f"/patients/{pid}", json={"full_name": "B"}, headers=family_h)
    ).status_code == 403

    await api.patch(
        f"/patients/{pid}/family-links/{link_id}",
        json={"share_scope": {"health_data": True}},
        headers=h,
    )
    assert (await api.get(f"/patients/{pid}", headers=family_h)).json()["genotype"] == "SC"


async def test_emergency_contacts(api, latest_code):
    h, _ = await login(api, latest_code, "08031230012")
    pid = (
        await api.post("/patients", json={"full_name": "A", "date_of_birth": _dob(30)}, headers=h)
    ).json()["id"]
    base = f"/patients/{pid}/emergency-contacts"
    second = {"name": "Bola", "phone": "+2348011111111", "relationship": "friend", "priority": 2}
    first = {"name": "Mama", "phone": "+2348022222222", "relationship": "Mother"}
    assert (await api.post(base, json=second, headers=h)).status_code == 201
    r = await api.post(base, json=first, headers=h)
    assert r.status_code == 201
    assert (r.json()["channel"], r.json()["relationship"]) == ("sms", "mother")
    names = [c["name"] for c in (await api.get(base, headers=h)).json()]
    assert names == ["Mama", "Bola"]

    bad = {**first, "phone": "08022222222"}
    assert (await api.post(base, json=bad, headers=h)).status_code == 422
    contact_id = r.json()["id"]
    r = await api.patch(f"{base}/{contact_id}", json={"channel": "whatsapp"}, headers=h)
    assert r.json()["channel"] == "whatsapp"
    assert (await api.delete(f"{base}/{contact_id}", headers=h)).status_code == 204
    assert len((await api.get(base, headers=h)).json()) == 1
