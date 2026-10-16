"""Tests: facility finder routes and who may change a facility (ERD 2.4).

End-to-end against real Postgres/PostGIS through the API. Reads are public;
writes need a facility admin (owner of that facility) or ops.
"""

import itertools
import uuid

import pytest

from app.core.permissions import UserRole
from app.core.security import create_access_token
from app.domains.auth.models import User
from app.domains.facilities import service
from app.domains.facilities.models import Facility, VerificationStatus

_phones = itertools.count(1)

LAGOS = {"latitude": 6.5244, "longitude": 3.3792}
FACILITY = {"name": "Lagos Island General", "address": "1 Broad Street, Lagos", "phone_number": "+2348011111111", **LAGOS}


async def make_user(db, role: UserRole):
    """Create a user directly and return (auth headers, user id)."""
    user = User(phone=f"+23480{next(_phones):08d}", full_name="Test User", role=role)
    db.add(user)
    await db.commit()
    token, _ = create_access_token(user.id, role.value)
    return {"Authorization": f"Bearer {token}"}, user.id


async def register(api, headers, **overrides):
    r = await api.post("/facilities", json={**FACILITY, **overrides}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def create_search_facility(api, owner, *, name, latitude, longitude, service_types):
    body = await register(api, owner, name=name, latitude=latitude, longitude=longitude)
    facility_id = uuid.UUID(body["id"])
    service_ids = []

    for service_type in service_types:
        r = await api.post(
            f"/facilities/{facility_id}/service",
            json={"service_type": service_type},
            headers=owner,
        )
        assert r.status_code == 201, r.text
        service_ids.append(uuid.UUID(r.json()["id"]))

    return facility_id, service_ids


async def verify_search_facility(db, facility_id, service_ids):
    await service.set_facility_verification(db, facility_id, VerificationStatus.VERIFIED)
    await service.verify_facility_location(db, facility_id)
    for service_id in service_ids:
        await service.set_service_verification(
            db, facility_id, service_id, VerificationStatus.VERIFIED
        )


# ================================================================ registering
async def test_register_needs_login(api):
    r = await api.post("/facilities", json=FACILITY)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "not_authenticated"


@pytest.mark.parametrize("role", [UserRole.PATIENT, UserRole.GUARDIAN, UserRole.CAREGIVER])
async def test_register_refuses_other_roles(api, db, role):
    headers, _ = await make_user(db, role)
    r = await api.post("/facilities", json=FACILITY, headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "role_forbidden"


async def test_facility_admin_owns_what_they_register(api, db):
    headers, admin_id = await make_user(db, UserRole.FACILITY_ADMIN)
    body = await register(api, headers)

    assert body["verification_status"] == "pending"
    assert (body["latitude"], body["longitude"]) == (LAGOS["latitude"], LAGOS["longitude"])
    facility = await db.get(Facility, uuid.UUID(body["id"]))
    assert facility.claimed_by == admin_id


async def test_ops_registers_unclaimed(api, db):
    headers, _ = await make_user(db, UserRole.OPS)
    body = await register(api, headers)
    facility = await db.get(Facility, uuid.UUID(body["id"]))
    assert facility.claimed_by is None


async def test_claimed_by_cannot_be_supplied(api, db):
    headers, admin_id = await make_user(db, UserRole.FACILITY_ADMIN)
    body = await register(api, headers, claimed_by=str(uuid.uuid4()))
    facility = await db.get(Facility, uuid.UUID(body["id"]))
    assert facility.claimed_by == admin_id


# ================================================================ managing
async def test_owner_and_ops_can_update(api, db):
    owner, _ = await make_user(db, UserRole.FACILITY_ADMIN)
    ops, _ = await make_user(db, UserRole.OPS)
    facility_id = (await register(api, owner))["id"]

    r = await api.patch(f"/facilities/{facility_id}", json={"name": "LIGH"}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "LIGH"

    r = await api.patch(f"/facilities/{facility_id}", json={"name": "LIGH Annex"}, headers=ops)
    assert r.status_code == 200, r.text


async def test_other_admin_cannot_manage(api, db):
    owner, _ = await make_user(db, UserRole.FACILITY_ADMIN)
    other, _ = await make_user(db, UserRole.FACILITY_ADMIN)
    facility_id = (await register(api, owner))["id"]
    service_id = (
        await api.post(
            f"/facilities/{facility_id}/service", json={"service_type": "haematology"}, headers=owner
        )
    ).json()["id"]

    attempts = [
        api.patch(f"/facilities/{facility_id}", json={"name": "Hijacked"}, headers=other),
        api.post(f"/facilities/{facility_id}/service", json={"service_type": "x-ray"}, headers=other),
        api.patch(
            f"/facilities/{facility_id}/service/{service_id}",
            json={"availability": "temporarily_unavailable"},
            headers=other,
        ),
    ]
    for attempt in attempts:
        r = await attempt
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "facility_not_owned"


async def test_manage_needs_login(api, db):
    owner, _ = await make_user(db, UserRole.FACILITY_ADMIN)
    facility_id = (await register(api, owner))["id"]
    r = await api.patch(f"/facilities/{facility_id}", json={"name": "Anon"})
    assert r.status_code == 401


async def test_manage_missing_facility_is_404(api, db):
    admin, _ = await make_user(db, UserRole.FACILITY_ADMIN)
    ops, _ = await make_user(db, UserRole.OPS)
    for headers in (admin, ops):
        r = await api.patch(f"/facilities/{uuid.uuid4()}", json={"name": "Ghost"}, headers=headers)
        assert r.status_code == 404, r.text


async def test_owner_manages_services(api, db):
    owner, _ = await make_user(db, UserRole.FACILITY_ADMIN)
    facility_id = (await register(api, owner))["id"]

    r = await api.post(
        f"/facilities/{facility_id}/service", json={"service_type": "  Haematology "}, headers=owner
    )
    assert r.status_code == 201, r.text
    service_id = r.json()["id"]
    assert r.json()["service_type"] == "haematology"

    r = await api.post(
        f"/facilities/{facility_id}/service", json={"service_type": "HAEMATOLOGY"}, headers=owner
    )
    assert r.status_code == 409

    r = await api.patch(
        f"/facilities/{facility_id}/service/{service_id}",
        json={"availability": "temporarily_unavailable"},
        headers=owner,
    )
    assert r.status_code == 200, r.text
    assert r.json()["availability"] == "temporarily_unavailable"


# ================================================================ public reads
async def test_reads_are_public(api, db):
    owner, _ = await make_user(db, UserRole.FACILITY_ADMIN)
    facility_id = (await register(api, owner))["id"]
    await api.post(f"/facilities/{facility_id}/service", json={"service_type": "x-ray"}, headers=owner)

    r = await api.get(f"/facilities/{facility_id}")
    assert r.status_code == 200, r.text
    assert "claimed_by" not in r.json()

    r = await api.get(f"/facilities/{facility_id}/service")
    assert r.status_code == 200, r.text
    assert [s["service_type"] for s in r.json()] == ["x-ray"]

    r = await api.get(f"/facilities/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_nearby_is_public_and_shows_only_verified(api, db):
    owner, _ = await make_user(db, UserRole.FACILITY_ADMIN)
    facility_id = uuid.UUID((await register(api, owner))["id"])
    r = await api.post(
        f"/facilities/{facility_id}/service", json={"service_type": "haematology"}, headers=owner
    )
    service_id = uuid.UUID(r.json()["id"])

    r = await api.get("/facilities/nearby", params=LAGOS)
    assert r.status_code == 200, r.text
    assert r.json() == []

    await service.set_facility_verification(db, facility_id, VerificationStatus.VERIFIED)
    await service.verify_facility_location(db, facility_id)
    await service.set_service_verification(db, facility_id, service_id, VerificationStatus.VERIFIED)

    r = await api.get("/facilities/nearby", params={**LAGOS, "service_type": "haematology"})
    assert r.status_code == 200, r.text
    [result] = r.json()
    assert result["facility"]["id"] == str(facility_id)
    assert result["distance_km"] < 1


async def test_nearby_respects_search_radius(api, db):
    owner, _ = await make_user(db, UserRole.FACILITY_ADMIN)
    latitude, longitude = LAGOS["latitude"], LAGOS["longitude"]
    near_id, near_services = await create_search_facility(
        api,
        owner,
        name="Near Facility",
        latitude=latitude + 3 / 111.195,
        longitude=longitude,
        service_types=["haematology"],
    )
    far_id, far_services = await create_search_facility(
        api,
        owner,
        name="Far Facility",
        latitude=latitude + 8 / 111.195,
        longitude=longitude,
        service_types=["haematology"],
    )
    await verify_search_facility(db, near_id, near_services)
    await verify_search_facility(db, far_id, far_services)

    r = await api.get(
        "/facilities/nearby",
        params={**LAGOS, "radius_km": 5, "service_type": "haematology"},
    )

    assert r.status_code == 200, r.text
    results = r.json()
    assert [result["facility"]["id"] for result in results] == [str(near_id)]
    assert 2.9 < results[0]["distance_km"] < 3.1


async def test_nearby_excludes_unverified_and_unavailable_facilities(api, db):
    owner, _ = await make_user(db, UserRole.FACILITY_ADMIN)
    candidates = {}
    for name in (
        "Unverified Facility",
        "Unverified Location",
        "Unverified Service",
        "Unavailable Service",
        "Eligible Facility",
    ):
        candidates[name] = await create_search_facility(
            api,
            owner,
            name=name,
            latitude=LAGOS["latitude"],
            longitude=LAGOS["longitude"],
            service_types=["haematology"],
        )

    unverified_facility, unverified_facility_services = candidates["Unverified Facility"]
    await service.verify_facility_location(db, unverified_facility)
    await service.set_service_verification(
        db, unverified_facility, unverified_facility_services[0], VerificationStatus.VERIFIED
    )

    unverified_location, unverified_location_services = candidates["Unverified Location"]
    await service.set_facility_verification(
        db, unverified_location, VerificationStatus.VERIFIED
    )
    await service.set_service_verification(
        db, unverified_location, unverified_location_services[0], VerificationStatus.VERIFIED
    )

    unverified_service, unverified_service_services = candidates["Unverified Service"]
    await service.set_facility_verification(db, unverified_service, VerificationStatus.VERIFIED)
    await service.verify_facility_location(db, unverified_service)

    unavailable_service, unavailable_service_ids = candidates["Unavailable Service"]
    await verify_search_facility(db, unavailable_service, unavailable_service_ids)
    r = await api.patch(
        f"/facilities/{unavailable_service}/service/{unavailable_service_ids[0]}",
        json={"availability": "temporarily_unavailable"},
        headers=owner,
    )
    assert r.status_code == 200, r.text

    eligible_facility, eligible_services = candidates["Eligible Facility"]
    await verify_search_facility(db, eligible_facility, eligible_services)

    r = await api.get(
        "/facilities/nearby",
        params={**LAGOS, "service_type": "haematology"},
    )

    assert r.status_code == 200, r.text
    assert [result["facility"]["id"] for result in r.json()] == [str(eligible_facility)]


async def test_nearby_sorts_by_distance_without_duplicate_facilities(api, db):
    owner, _ = await make_user(db, UserRole.FACILITY_ADMIN)
    latitude, longitude = LAGOS["latitude"], LAGOS["longitude"]
    facilities = []
    for name, distance_km, service_types in (
        ("Near Facility", 1, ["haematology", "x-ray"]),
        ("Middle Facility", 2, ["haematology"]),
        ("Far Facility", 4, ["haematology", "x-ray"]),
    ):
        facility_id, service_ids = await create_search_facility(
            api,
            owner,
            name=name,
            latitude=latitude + distance_km / 111.195,
            longitude=longitude,
            service_types=service_types,
        )
        await verify_search_facility(db, facility_id, service_ids)
        facilities.append((facility_id, service_types))

    r = await api.get("/facilities/nearby", params=LAGOS)

    assert r.status_code == 200, r.text
    results = r.json()
    result_ids = [result["facility"]["id"] for result in results]
    assert result_ids == [str(facility_id) for facility_id, _ in facilities]
    assert len(result_ids) == len(set(result_ids))
    distances = [result["distance_km"] for result in results]
    assert distances == sorted(distances)
    assert [s["service_type"] for s in results[0]["matching_services"]] == [
        "haematology",
        "x-ray",
    ]
