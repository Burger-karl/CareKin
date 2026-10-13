"""Tests: Patients, guardians, emergency contacts, care profile, family links.

These run without a database: the service layer is stubbed at the HTTP boundary.
"""

import asyncio
import struct
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from geoalchemy2.elements import WKBElement
from jose import jwt
from pydantic import ValidationError

from app.config import settings
from app.core.audit import AuditContext, AuditLog
from app.database import get_db
from app.domains.patients import models, schemas, service
from app.main import app

API = f"{settings.API_V1_PREFIX}/patients"


@pytest.fixture
def fernet(monkeypatch):
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(models, "_fernet", lambda: key)
    return key


@pytest.fixture
def no_db():
    async def _no_db():
        yield None

    app.dependency_overrides[get_db] = _no_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def user_id():
    return uuid.uuid4()


@pytest.fixture
def auth(user_id):
    token = jwt.encode(
        {"sub": str(user_id)},
        settings.SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


def _patient(age_years: int) -> models.Patient:
    """A patient who turned `age_years` a few days ago."""
    today = datetime.now(UTC).date() - timedelta(days=3)
    try:
        dob = today.replace(year=today.year - age_years)
    except ValueError:  # 29 February
        dob = today.replace(year=today.year - age_years, day=28)
    return models.Patient(full_name="Test", date_of_birth=dob)


class FakeSession:
    """Collects what a service adds, without a database."""

    def __init__(self, scalar=None):
        self.added = []
        self.commits = 0
        self._scalar = scalar

    def add(self, obj):
        self.added.append(obj)

    async def scalar(self, query):
        return self._scalar

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass

    def audit_entries(self):
        return [obj for obj in self.added if isinstance(obj, AuditLog)]


# ---------------------------------------------------------------- encryption
def test_encrypted_text_round_trip(fernet):
    column = models.EncryptedText()
    stored = column.process_bind_param("SS", None)
    assert stored != b"SS"
    assert column.process_result_value(stored, None) == "SS"
    assert column.process_bind_param(None, None) is None


def test_encrypted_text_wrong_key_fails(fernet, monkeypatch):
    stored = models.EncryptedText().process_bind_param("SS", None)
    other = Fernet(Fernet.generate_key())
    monkeypatch.setattr(models, "_fernet", lambda: other)
    with pytest.raises(RuntimeError, match="decrypt"):
        models.EncryptedText().process_result_value(stored, None)


# ---------------------------------------------------------------- location
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


# ---------------------------------------------------------------- schemas
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


def test_patient_create_normalises():
    data = schemas.PatientCreate(full_name=" Ada ", date_of_birth="2000-01-01", genotype="ss")
    assert data.full_name == "Ada"
    assert data.genotype == "SS"


def test_patient_update_allows_clearing_optional_fields():
    data = schemas.PatientUpdate(genotype=None)
    assert data.model_dump(exclude_unset=True) == {"genotype": None}


# ---------------------------------------------------------------- access rules
def test_minor_status_is_derived():
    assert _patient(10).is_minor
    assert not _patient(30).is_minor
    assert _patient(30).age == 30


def test_adult_self_manages_access_but_minor_does_not():
    assert service.PatientAccess(_patient(30), "self").can_manage_access
    assert not service.PatientAccess(_patient(10), "self").can_manage_access


def test_only_primary_guardian_manages_access():
    patient = _patient(10)
    assert service.PatientAccess(patient, "guardian").can_edit
    assert not service.PatientAccess(patient, "guardian").can_manage_access
    assert service.PatientAccess(patient, "guardian", is_primary_guardian=True).can_manage_access


def test_family_sees_only_what_is_shared():
    patient = _patient(30)
    nothing = service.PatientAccess(patient, "family")
    assert not nothing.can_edit
    assert not nothing.sees_health_data
    assert not nothing.sees_location
    shared = service.PatientAccess(patient, "family", share_scope={"health_data": True})
    assert shared.sees_health_data
    assert not shared.sees_location


def test_family_read_hides_unshared_fields(fernet):
    patient = _patient(30)
    patient.genotype = "SS"
    patient.blood_group = "O+"
    patient.created_at = date.today()
    read = service.to_read(service.PatientAccess(patient, "family"))
    assert read.genotype is None
    assert read.blood_group is None
    assert read.access == "family"
    own = service.to_read(service.PatientAccess(patient, "self"))
    assert own.genotype == "SS"


# ---------------------------------------------------------------- HTTP
def test_requires_authentication(client, no_db):
    assert client.get(API).status_code == 401
    assert client.get(API, headers={"Authorization": "Bearer junk"}).status_code == 401


def test_token_user_is_passed_to_service(client, no_db, auth, user_id, monkeypatch):
    seen = {}

    async def fake(db, actor, patient_id):
        seen["actor"] = actor
        raise service.PatientNotFound("Patient not found")

    monkeypatch.setattr(service, "get_patient", fake)
    resp = client.get(f"{API}/{uuid.uuid4()}", headers={**auth, "User-Agent": "carekin-test"})
    assert resp.status_code == 404
    assert seen["actor"].user_id == user_id
    assert seen["actor"].user_agent == "carekin-test"
    assert seen["actor"].ip_address is None  # TestClient's host "testclient" isn't an IP


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (service.PatientNotFound, 404),
        (service.PatientAccessDenied, 403),
        (service.PatientConflict, 409),
        (service.PatientRuleViolation, 422),
    ],
)
def test_service_errors_map_to_http(client, no_db, auth, monkeypatch, error, code):
    async def fake(*args):
        raise error("nope")

    monkeypatch.setattr(service, "remove_guardian", fake)
    resp = client.delete(f"{API}/{uuid.uuid4()}/guardians/{uuid.uuid4()}", headers=auth)
    assert resp.status_code == code
    assert resp.json() == {"detail": "nope"}


def test_create_validates_body(client, no_db, auth):
    assert client.post(API, json={"full_name": "A"}, headers=auth).status_code == 422


def test_all_routes_registered(client):
    paths = {p for p in client.get("/openapi.json").json()["paths"] if p.startswith(API)}
    assert paths == {
        API,
        f"{API}/{{patient_id}}",
        f"{API}/{{patient_id}}/guardians",
        f"{API}/{{patient_id}}/guardians/{{guardian_id}}",
        f"{API}/{{patient_id}}/emergency-contacts",
        f"{API}/{{patient_id}}/emergency-contacts/{{contact_id}}",
        f"{API}/{{patient_id}}/care-profile",
        f"{API}/{{patient_id}}/family-links",
        f"{API}/{{patient_id}}/family-links/{{link_id}}",
    }


# ---------------------------------------------------------------- audit log
@pytest.fixture
def ctx(user_id):
    return AuditContext(user_id=user_id, ip_address="102.89.1.10", user_agent="test")


def test_audit_context_keeps_only_valid_ips(user_id):
    assert AuditContext.from_request(user_id, "102.89.1.10", None).ip_address == "102.89.1.10"
    assert AuditContext.from_request(user_id, "testclient", None).ip_address is None
    assert AuditContext.from_request(user_id, None, "x" * 900).user_agent == "x" * 500


def _stub_access(monkeypatch, access):
    async def fake(db, patient_id, actor_id):
        return access

    monkeypatch.setattr(service, "get_access", fake)


def test_viewing_health_data_is_audited(monkeypatch, fernet, ctx):
    patient = _patient(30)
    patient.created_at = datetime.now(UTC)
    _stub_access(monkeypatch, service.PatientAccess(patient, "guardian"))
    db = FakeSession()
    asyncio.run(service.get_patient(db, ctx, patient.id))
    [entry] = db.audit_entries()
    assert (entry.action, entry.entity_type, entry.entity_id) == (
        "patient.view",
        "patient",
        patient.id,
    )
    assert (entry.actor_id, entry.ip_address, entry.user_agent) == (
        ctx.user_id,
        "102.89.1.10",
        "test",
    )
    assert db.commits == 1


def test_viewing_without_health_data_is_not_audited(monkeypatch, fernet, ctx):
    patient = _patient(30)
    patient.created_at = datetime.now(UTC)
    _stub_access(monkeypatch, service.PatientAccess(patient, "family"))
    db = FakeSession()
    asyncio.run(service.get_patient(db, ctx, patient.id))
    assert db.audit_entries() == []


def test_care_profile_update_audits_fields_not_values(monkeypatch, fernet, ctx):
    patient = _patient(30)
    _stub_access(monkeypatch, service.PatientAccess(patient, "self"))
    db = FakeSession(scalar=None)  # no existing profile
    data = schemas.CareProfileWrite(allergies="penicillin", known_triggers="cold")
    profile = asyncio.run(service.put_care_profile(db, ctx, patient.id, data))
    [entry] = db.audit_entries()
    assert entry.action == "care_profile.update"
    assert entry.entity_id == profile.id
    assert entry.metadata_["fields"] == ["allergies", "known_triggers"]
    assert "penicillin" not in str(entry.metadata_)
    assert profile.updated_by == ctx.user_id


def test_audit_log_table_shape():
    table = AuditLog.__table__
    assert table.name == "audit_logs"
    assert {c.name for c in table.columns} == {
        "id",
        "actor_id",
        "action",
        "entity_type",
        "entity_id",
        "ip_address",
        "user_agent",
        "metadata",
        "created_at",
    }
    assert any([c.name for c in ix.columns] == ["entity_type", "entity_id"] for ix in table.indexes)
