"""Tests: the patients endpoints test_patients.py doesn't reach (ERD 2.2).

- PATCH /patients/{id}: who may edit, health-data consent, the minor rule, validation.
- Family links: list, add, update and remove, and what each one unlocks.
- Guardians, emergency contacts and the care profile: the access and conflict edges.

Users are created directly with real access tokens, so each test sets up only what it
needs. test_patients.py covers the sign-up flow end to end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.permissions import UserRole
from app.domains.auth.models import User
from app.domains.patients.models import FamilyLink, Guardian, Patient
from app.tests.crisis_factories import (
    add_family_link,
    add_guardian,
    auth,
    grant_consent,
    make_patient,
    make_user,
)
from app.tests.payments_fixtures import encryption_key_fixture  # noqa: F401


def years_ago(years: int, days: int = 3) -> date:
    """The date someone turned `years` a few days ago."""
    day = datetime.now(UTC).date() - timedelta(days=days)
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


async def audit_rows(db: AsyncSession, action: str) -> list[AuditLog]:
    return list(await db.scalars(select(AuditLog).where(AuditLog.action == action)))


async def fresh(db: AsyncSession, model, row_id):
    return await db.get(model, row_id, populate_existing=True)


class People:
    """An adult patient with their own login, and everyone around them."""

    def __init__(self, patient_user, patient, guardian_user, guardian, family, stranger):
        self.patient_user: User = patient_user
        self.patient: Patient = patient
        self.guardian_user: User = guardian_user  # non-primary guardian
        self.guardian: Guardian = guardian
        self.family: User = family  # family link, nothing shared
        self.stranger: User = stranger

    @property
    def url(self) -> str:
        return f"/patients/{self.patient.id}"

    @property
    def me(self) -> dict[str, str]:
        return auth(self.patient_user)


@pytest.fixture(name="people")
async def people_fixture(db: AsyncSession) -> People:
    patient_user = await make_user(db, UserRole.PATIENT, "Chidi Okafor")
    await grant_consent(db, patient_user)
    patient = await make_patient(db, user=patient_user, created_by=patient_user)
    guardian_user = await make_user(db, UserRole.GUARDIAN, "Mama Chidi")
    guardian = await add_guardian(db, patient, guardian_user)
    family = await make_user(db, UserRole.FAMILY_ABROAD, "Ada in London")
    await add_family_link(db, patient, family, {})
    stranger = await make_user(db, UserRole.PATIENT, "Stranger")
    return People(patient_user, patient, guardian_user, guardian, family, stranger)


async def minor_with_guardian(db: AsyncSession, *, primary: bool = True):
    """A child with no login, registered by a guardian."""
    parent = await make_user(db, UserRole.GUARDIAN, "Parent")
    child = Patient(full_name="Kelechi", date_of_birth=years_ago(9), created_by=parent.id)
    db.add(child)
    await db.commit()
    guardian = await add_guardian(db, child, parent, primary=primary)
    return parent, child, guardian


# ================================================================ PATCH /patients/{id}
async def test_patient_edits_their_own_record(api: httpx.AsyncClient, db, people) -> None:
    r = await api.patch(
        people.url,
        json={
            "full_name": "  Chidi N. Okafor ",
            "sex": "Male",
            "genotype": "ss",
            "blood_group": "O+",
            "home_location": {"latitude": 6.4413, "longitude": 7.4988},
        },
        headers=people.me,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["full_name"], body["sex"], body["genotype"], body["blood_group"]) == (
        "Chidi N. Okafor",
        "male",
        "SS",
        "O+",
    )
    assert body["home_location"] == {"latitude": 6.4413, "longitude": 7.4988}
    assert body["access"] == "self"

    # Health fields are ciphertext at rest.
    raw = await db.scalar(text("SELECT genotype FROM patients"))
    assert raw is not None and b"SS" not in bytes(raw)

    # The audit names the fields, never the values.
    [entry] = await audit_rows(db, "patient.update")
    assert entry.actor_id == people.patient_user.id
    assert entry.meta["fields"] == ["blood_group", "full_name", "genotype", "home_location", "sex"]
    assert "SS" not in str(entry.meta) and "O+" not in str(entry.meta)

    # The change is stored, not just echoed.
    again = (await api.get(people.url, headers=people.me)).json()
    assert again["genotype"] == "SS" and again["full_name"] == "Chidi N. Okafor"


async def test_patch_is_partial_and_null_clears_optional_fields(api, db, people) -> None:
    await api.patch(
        people.url,
        json={"sex": "female", "home_location": {"latitude": 6.5, "longitude": 7.5}},
        headers=people.me,
    )
    r = await api.patch(people.url, json={"home_location": None}, headers=people.me)
    assert r.status_code == 200, r.text
    assert r.json()["home_location"] is None
    assert r.json()["sex"] == "female"  # untouched
    assert r.json()["full_name"] == "Chidi Okafor"


async def test_empty_patch_changes_nothing(api, db, people) -> None:
    r = await api.patch(people.url, json={}, headers=people.me)
    assert r.status_code == 200
    assert r.json()["full_name"] == "Chidi Okafor"
    [entry] = await audit_rows(db, "patient.update")
    assert entry.meta["fields"] == []


async def test_health_fields_need_consent_but_other_fields_dont(api, db) -> None:
    user = await make_user(db, UserRole.PATIENT)
    patient = await make_patient(db, user=user, created_by=user)
    url, h = f"/patients/{patient.id}", auth(user)

    for field in ({"genotype": "AS"}, {"blood_group": "A+"}):
        r = await api.patch(url, json=field, headers=h)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "consent_required"
        assert r.json()["error"]["details"]["next"] == "/auth/consent"

    r = await api.patch(url, json={"full_name": "New Name"}, headers=h)
    assert r.status_code == 200
    # Clearing a health field is still writing health data.
    assert (await api.patch(url, json={"genotype": None}, headers=h)).status_code == 403


async def test_guardian_can_edit_even_when_not_primary(api, db, people) -> None:
    await grant_consent(db, people.guardian_user)
    h = auth(people.guardian_user)
    r = await api.patch(people.url, json={"genotype": "SC"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["access"] == "guardian"
    assert r.json()["genotype"] == "SC"
    [entry] = await audit_rows(db, "patient.update")
    assert entry.actor_id == people.guardian_user.id


async def test_family_cannot_edit_even_with_health_data_shared(api, db, people) -> None:
    link = await db.scalar(select(FamilyLink).where(FamilyLink.user_id == people.family.id))
    link.share_scope = {"health_data": True, "location": True}
    await db.commit()
    r = await api.patch(people.url, json={"full_name": "X"}, headers=auth(people.family))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "patient_forbidden"
    assert (await fresh(db, Patient, people.patient.id)).full_name == "Chidi Okafor"


async def test_strangers_and_unknown_patients_get_404_on_patch(api, db, people) -> None:
    r = await api.patch(people.url, json={"full_name": "X"}, headers=auth(people.stranger))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "patient_not_found"
    r = await api.patch(f"/patients/{uuid.uuid4()}", json={"full_name": "X"}, headers=people.me)
    assert r.status_code == 404
    assert (await api.patch(people.url, json={"full_name": "X"})).status_code == 401


async def test_dob_change_making_patient_a_minor_needs_a_primary_guardian(api, db, people) -> None:
    # people.guardian is not primary, so this adult has no primary guardian.
    r = await api.patch(
        people.url, json={"date_of_birth": years_ago(15).isoformat()}, headers=people.me
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "patient_rule_violation"
    # Rolled back: nothing changed and nothing was audited.
    assert (await fresh(db, Patient, people.patient.id)).date_of_birth == date(1996, 5, 17)
    assert await audit_rows(db, "patient.update") == []

    # With a primary guardian in place, the correction goes through.
    people.guardian.is_primary = True
    await db.commit()
    r = await api.patch(
        people.url, json={"date_of_birth": years_ago(15).isoformat()}, headers=people.me
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_minor"] is True
    assert r.json()["age"] == 15


async def test_dob_change_that_keeps_an_adult_needs_no_guardian(api, db, people) -> None:
    r = await api.patch(
        people.url, json={"date_of_birth": years_ago(40).isoformat()}, headers=people.me
    )
    assert r.status_code == 200
    assert (r.json()["age"], r.json()["is_minor"]) == (40, False)


async def test_guardian_edits_a_minor_with_no_login(api, db) -> None:
    parent, child, _ = await minor_with_guardian(db)
    r = await api.patch(
        f"/patients/{child.id}",
        json={"full_name": "Kelechi Obi", "date_of_birth": years_ago(10).isoformat()},
        headers=auth(parent),
    )
    assert r.status_code == 200, r.text
    assert (r.json()["full_name"], r.json()["age"]) == ("Kelechi Obi", 10)


@pytest.mark.parametrize(
    "payload",
    [
        {"full_name": None},
        {"full_name": "   "},
        {"full_name": "x" * 201},
        {"date_of_birth": None},
        {"date_of_birth": "2999-01-01"},
        {"date_of_birth": "1850-01-01"},
        {"date_of_birth": "not-a-date"},
        {"blood_group": "C+"},
        {"genotype": "S"},
        {"home_location": {"latitude": 91, "longitude": 0}},
        {"home_location": {"latitude": 0, "longitude": -181}},
        {"user_id": str(uuid.uuid4())},
        {"created_by": str(uuid.uuid4())},
    ],
)
async def test_patch_rejects_bad_input(api, db, people, payload) -> None:
    r = await api.patch(people.url, json=payload, headers=people.me)
    assert r.status_code == 422, r.text
    assert (await fresh(db, Patient, people.patient.id)).full_name == "Chidi Okafor"


# ================================================================ family links
async def test_patient_lists_family_links_oldest_first(api, db, people) -> None:
    second = await make_user(db, UserRole.FAMILY_ABROAD, "Uncle in Houston")
    r = await api.post(
        f"{people.url}/family-links",
        json={"user_id": str(second.id), "share_scope": {"location": True}, "can_fund": True},
        headers=people.me,
    )
    assert r.status_code == 201, r.text

    r = await api.get(f"{people.url}/family-links", headers=people.me)
    assert r.status_code == 200, r.text
    links = r.json()
    assert [link["user_id"] for link in links] == [str(people.family.id), str(second.id)]
    assert links[1]["share_scope"] == {"location": True}
    assert links[1]["can_fund"] is True
    assert links[0]["can_fund"] is False
    assert all(link["patient_id"] == str(people.patient.id) for link in links)


async def test_only_access_managers_list_family_links(api, db, people) -> None:
    url = f"{people.url}/family-links"
    # A non-primary guardian and the family member themselves can't.
    for user in (people.guardian_user, people.family):
        r = await api.get(url, headers=auth(user))
        assert r.status_code == 403, user.full_name
        assert r.json()["error"]["code"] == "patient_forbidden"
    # A stranger doesn't learn the patient exists.
    assert (await api.get(url, headers=auth(people.stranger))).status_code == 404
    # A primary guardian can.
    people.guardian.is_primary = True
    await db.commit()
    assert (await api.get(url, headers=auth(people.guardian_user))).status_code == 200


async def test_primary_guardian_manages_a_minors_family_links(api, db) -> None:
    parent, child, _ = await minor_with_guardian(db)
    aunt = await make_user(db, UserRole.FAMILY_ABROAD, "Aunt")
    url = f"/patients/{child.id}/family-links"
    r = await api.post(url, json={"user_id": str(aunt.id)}, headers=auth(parent))
    assert r.status_code == 201, r.text
    assert len((await api.get(url, headers=auth(parent))).json()) == 1
    r = await api.delete(f"{url}/{r.json()['id']}", headers=auth(parent))
    assert r.status_code == 204
    assert (await api.get(url, headers=auth(parent))).json() == []


async def test_non_primary_guardian_of_a_minor_cannot_manage_links(api, db) -> None:
    parent, child, _ = await minor_with_guardian(db, primary=False)
    aunt = await make_user(db, UserRole.FAMILY_ABROAD, "Aunt")
    r = await api.post(
        f"/patients/{child.id}/family-links", json={"user_id": str(aunt.id)}, headers=auth(parent)
    )
    assert r.status_code == 403


async def test_adding_a_family_link_edges(api, db, people) -> None:
    url = f"{people.url}/family-links"
    # The same person twice.
    r = await api.post(url, json={"user_id": str(people.family.id)}, headers=people.me)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "patient_conflict"
    # The patient themselves.
    r = await api.post(url, json={"user_id": str(people.patient_user.id)}, headers=people.me)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "patient_rule_violation"
    # An account that doesn't exist.
    r = await api.post(url, json={"user_id": str(uuid.uuid4())}, headers=people.me)
    assert r.status_code == 409
    # A scope key that isn't allowed, and an extra field.
    bad = {"user_id": str(people.stranger.id), "share_scope": {"bank": True}}
    assert (await api.post(url, json=bad, headers=people.me)).status_code == 422
    bad = {"user_id": str(people.stranger.id), "role": "ops"}
    assert (await api.post(url, json=bad, headers=people.me)).status_code == 422
    # None of those left a row behind.
    count = await db.scalar(text("SELECT count(*) FROM family_links"))
    assert count == 1


async def test_family_link_add_is_audited_with_its_scope(api, db, people) -> None:
    r = await api.post(
        f"{people.url}/family-links",
        json={"user_id": str(people.stranger.id), "share_scope": {"health_data": True}},
        headers=people.me,
    )
    assert r.status_code == 201
    [entry] = await audit_rows(db, "family_link.add")
    assert entry.entity_id == uuid.UUID(r.json()["id"])
    assert entry.meta == {
        "patient_id": str(people.patient.id),
        "share_scope": {"health_data": True},
    }


async def test_new_link_grants_access_and_scope_decides_what_is_seen(api, db, people) -> None:
    await api.patch(
        people.url,
        json={"genotype": "SS", "home_location": {"latitude": 6.44, "longitude": 7.49}},
        headers=people.me,
    )
    viewer = auth(people.stranger)
    assert (await api.get(people.url, headers=viewer)).status_code == 404

    r = await api.post(
        f"{people.url}/family-links",
        json={"user_id": str(people.stranger.id), "share_scope": {"location": True}},
        headers=people.me,
    )
    link_id = r.json()["id"]
    seen = (await api.get(people.url, headers=viewer)).json()
    assert (seen["access"], seen["genotype"], seen["home_location"]) == (
        "family",
        None,
        {"latitude": 6.44, "longitude": 7.49},
    )
    # No health data shared: no care profile, and not in the audit as a health read.
    assert (await api.get(f"{people.url}/care-profile", headers=viewer)).status_code == 403

    # The family member appears in their own patient list too.
    listed = (await api.get("/patients", headers=viewer)).json()
    assert [(p["id"], p["access"]) for p in listed] == [(str(people.patient.id), "family")]

    # Widening the scope shows health data; narrowing hides location again.
    r = await api.patch(
        f"{people.url}/family-links/{link_id}",
        json={"share_scope": {"health_data": True}},
        headers=people.me,
    )
    assert r.status_code == 200
    assert r.json()["share_scope"] == {"health_data": True}
    seen = (await api.get(people.url, headers=viewer)).json()
    assert (seen["genotype"], seen["home_location"]) == ("SS", None)


async def test_update_family_link_can_fund_only(api, db, people) -> None:
    link = await db.scalar(select(FamilyLink).where(FamilyLink.user_id == people.family.id))
    r = await api.patch(
        f"{people.url}/family-links/{link.id}", json={"can_fund": True}, headers=people.me
    )
    assert r.status_code == 200, r.text
    assert (r.json()["can_fund"], r.json()["share_scope"]) == (True, {})
    [entry] = await audit_rows(db, "family_link.update")
    assert entry.meta == {"patient_id": str(people.patient.id), "can_fund": True}


async def test_removing_a_family_link_revokes_access(api, db, people) -> None:
    link = await db.scalar(select(FamilyLink).where(FamilyLink.user_id == people.family.id))
    viewer = auth(people.family)
    assert (await api.get(people.url, headers=viewer)).status_code == 200

    r = await api.delete(f"{people.url}/family-links/{link.id}", headers=people.me)
    assert r.status_code == 204
    assert r.content == b""

    assert (await api.get(people.url, headers=viewer)).status_code == 404
    assert (await api.get("/patients", headers=viewer)).json() == []
    assert (await api.get(f"{people.url}/family-links", headers=people.me)).json() == []
    [entry] = await audit_rows(db, "family_link.remove")
    assert (entry.entity_id, entry.actor_id) == (link.id, people.patient_user.id)

    # Removing it again: it's gone.
    r = await api.delete(f"{people.url}/family-links/{link.id}", headers=people.me)
    assert r.status_code == 404


async def test_family_link_routes_check_the_link_belongs_to_the_patient(api, db, people) -> None:
    other_user = await make_user(db, UserRole.PATIENT, "Other Patient")
    other = await make_patient(db, user=other_user, created_by=other_user)
    foreign = await add_family_link(db, other, people.family, {})
    url = f"{people.url}/family-links/{foreign.id}"
    assert (await api.delete(url, headers=people.me)).status_code == 404
    r = await api.patch(url, json={"can_fund": True}, headers=people.me)
    assert r.status_code == 404
    # The other patient's link is untouched.
    assert (await fresh(db, FamilyLink, foreign.id)).can_fund is False
    unknown = f"{people.url}/family-links/{uuid.uuid4()}"
    assert (await api.delete(unknown, headers=people.me)).status_code == 404


async def test_who_may_remove_a_family_link(api, db, people) -> None:
    link = await db.scalar(select(FamilyLink).where(FamilyLink.user_id == people.family.id))
    url = f"{people.url}/family-links/{link.id}"
    assert (await api.delete(url, headers=auth(people.guardian_user))).status_code == 403
    assert (await api.delete(url, headers=auth(people.family))).status_code == 403
    assert (await api.delete(url, headers=auth(people.stranger))).status_code == 404
    assert await fresh(db, FamilyLink, link.id) is not None


# ================================================================ guardians
async def test_adult_patient_adds_a_primary_guardian_which_demotes_others(api, db, people) -> None:
    people.guardian.is_primary = True
    await db.commit()
    newcomer = await make_user(db, UserRole.GUARDIAN, "Big Brother")
    r = await api.post(
        f"{people.url}/guardians",
        json={"user_id": str(newcomer.id), "relationship": "Brother", "is_primary": True},
        headers=people.me,
    )
    assert r.status_code == 201, r.text
    assert (r.json()["relationship"], r.json()["is_primary"]) == ("brother", True)
    assert (await fresh(db, Guardian, people.guardian.id)).is_primary is False
    listed = (await api.get(f"{people.url}/guardians", headers=people.me)).json()
    assert listed[0]["user_id"] == str(newcomer.id)  # primary first


async def test_guardian_add_edges(api, db, people) -> None:
    url = f"{people.url}/guardians"
    me = str(people.patient_user.id)
    r = await api.post(url, json={"user_id": me, "relationship": "self"}, headers=people.me)
    assert r.status_code == 422
    r = await api.post(
        url, json={"user_id": str(uuid.uuid4()), "relationship": "aunt"}, headers=people.me
    )
    assert r.status_code == 409
    r = await api.post(
        url,
        json={"user_id": str(people.guardian_user.id), "relationship": "aunt"},
        headers=people.me,
    )
    assert r.status_code == 409
    # Family can't see guardians at all (no edit access); strangers get 404.
    assert (await api.get(url, headers=auth(people.family))).status_code == 403
    assert (await api.get(url, headers=auth(people.stranger))).status_code == 404


async def test_an_adult_with_a_login_can_remove_their_last_guardian(api, db, people) -> None:
    people.guardian.is_primary = True
    await db.commit()
    r = await api.delete(f"{people.url}/guardians/{people.guardian.id}", headers=people.me)
    assert r.status_code == 204
    # The former guardian loses access.
    assert (await api.get(people.url, headers=auth(people.guardian_user))).status_code == 404


async def test_guardian_routes_check_the_guardian_belongs_to_the_patient(api, db, people) -> None:
    _, child, foreign = await minor_with_guardian(db)
    url = f"{people.url}/guardians/{foreign.id}"
    assert (await api.delete(url, headers=people.me)).status_code == 404
    r = await api.patch(url, json={"relationship": "uncle"}, headers=people.me)
    assert r.status_code == 404
    assert (await fresh(db, Guardian, foreign.id)).patient_id == child.id


# ================================================================ emergency contacts
async def test_emergency_contacts_need_edit_access(api, db, people) -> None:
    url = f"{people.url}/emergency-contacts"
    contact = {"name": "Mama", "phone": "+2348022222222", "relationship": "mother"}
    for user in (people.family,):
        assert (await api.get(url, headers=auth(user))).status_code == 403
        assert (await api.post(url, json=contact, headers=auth(user))).status_code == 403
    assert (await api.get(url, headers=auth(people.stranger))).status_code == 404
    # A guardian (not primary) can manage them.
    r = await api.post(url, json=contact, headers=auth(people.guardian_user))
    assert r.status_code == 201, r.text


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "M", "phone": "+2348022222222", "relationship": "x", "priority": 0},
        {"name": "M", "phone": "+2348022222222", "relationship": "x", "priority": 100},
        {"name": "M", "phone": "+2348022222222", "relationship": "x", "channel": "pigeon"},
        {"name": "", "phone": "+2348022222222", "relationship": "x"},
        {"name": "M", "phone": "+0348022222222", "relationship": "x"},
    ],
)
async def test_emergency_contact_validation(api, db, people, payload) -> None:
    r = await api.post(f"{people.url}/emergency-contacts", json=payload, headers=people.me)
    assert r.status_code == 422


async def test_emergency_contact_of_another_patient_is_404(api, db, people) -> None:
    other_user = await make_user(db, UserRole.PATIENT)
    await grant_consent(db, other_user)
    other = await make_patient(db, user=other_user, created_by=other_user)
    r = await api.post(
        f"/patients/{other.id}/emergency-contacts",
        json={"name": "Papa", "phone": "+2348033333333", "relationship": "father"},
        headers=auth(other_user),
    )
    contact_id = r.json()["id"]
    url = f"{people.url}/emergency-contacts/{contact_id}"
    assert (await api.patch(url, json={"name": "X"}, headers=people.me)).status_code == 404
    assert (await api.delete(url, headers=people.me)).status_code == 404


# ================================================================ care profile
async def test_care_profile_put_replaces_the_whole_profile(api, db, people) -> None:
    url = f"{people.url}/care-profile"
    r = await api.put(
        url, json={"known_triggers": "cold", "allergies": "penicillin"}, headers=people.me
    )
    assert r.status_code == 200
    r = await api.put(url, json={"care_instructions": "fluids every hour"}, headers=people.me)
    assert r.status_code == 200
    body = r.json()
    assert body["care_instructions"] == "fluids every hour"
    assert (body["known_triggers"], body["allergies"]) == (None, None)
    # One profile per patient, however many PUTs.
    assert await db.scalar(text("SELECT count(*) FROM care_profiles")) == 1


async def test_care_profile_guardian_writes_and_shared_family_reads(api, db, people) -> None:
    await grant_consent(db, people.guardian_user)
    url = f"{people.url}/care-profile"
    r = await api.put(url, json={"allergies": "sulfa"}, headers=auth(people.guardian_user))
    assert r.status_code == 200, r.text
    assert r.json()["updated_by"] == str(people.guardian_user.id)

    family = auth(people.family)
    assert (await api.get(url, headers=family)).status_code == 403
    assert (await api.put(url, json={"allergies": "x"}, headers=family)).status_code == 403
    link = await db.scalar(select(FamilyLink).where(FamilyLink.user_id == people.family.id))
    link.share_scope = {"health_data": True}
    await db.commit()
    r = await api.get(url, headers=family)
    assert r.status_code == 200
    assert r.json()["allergies"] == "sulfa"
    # Still read-only for family.
    assert (await api.put(url, json={"allergies": "x"}, headers=family)).status_code == 403
    views = await audit_rows(db, "care_profile.view")
    assert [v.actor_id for v in views] == [people.family.id]


async def test_care_profile_rejects_unknown_facility_and_bad_input(api, db, people) -> None:
    url = f"{people.url}/care-profile"
    r = await api.put(url, json={"primary_facility_id": str(uuid.uuid4())}, headers=people.me)
    assert r.status_code == 409
    assert (
        await api.put(url, json={"allergies": "x" * 5001}, headers=people.me)
    ).status_code == 422
    assert (await api.put(url, json={"dosage": "2 tabs"}, headers=people.me)).status_code == 422
    assert (await api.get(url, headers=people.me)).status_code == 404  # nothing was stored
    assert (await api.get(url, headers=auth(people.stranger))).status_code == 404
