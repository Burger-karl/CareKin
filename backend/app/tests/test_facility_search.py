"""Tests: GET /facilities, the facility finder (PRD FF-1, FF-2, FF-3), and accepted HMOs.

FF-1: filter by haematologist, 24-hour emergency, blood transfusion, blood bank, genotype
testing and accepted HMOs. Every filter given must match, and only what ops verified
counts: a pending facility, or a service that is unverified or temporarily unavailable,
must never show up as an answer.

Facilities are inserted directly (the registration flow has its own tests), except in the
end-to-end journey at the bottom, which goes through registration and the ops console.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import UserRole
from app.domains.facilities.models import (
    Facility,
    FacilityService,
    ServiceAvailability,
    VerificationStatus,
)
from app.domains.facilities.schemas import FinderService
from app.domains.facilities.service import FINDER_SERVICE_NAMES, normalize_service_type
from app.tests.crisis_factories import auth, make_user, point

# Enugu: Ogui Road, and places north of it. 0.01 degrees of latitude is about 1.11 km.
ENUGU = (6.4413, 7.4988)
_phones = itertools.count(1)


async def place(
    db: AsyncSession,
    name: str,
    *,
    at: tuple[float, float] | None = ENUGU,
    services: tuple[str | tuple[str, str, str], ...] = (),
    status: VerificationStatus = VerificationStatus.VERIFIED,
    location_verified: bool = True,
    hmos: tuple[str, ...] = (),
    address: str | None = None,
) -> Facility:
    """A facility as ops would leave it. A service is a name (verified and available)
    or (name, verification_status, availability)."""
    facility = Facility(
        name=name,
        address=address or f"{name}, Enugu",
        phone_number=f"+23480{next(_phones):08d}",
        location=point(*at) if at else None,
        verification_status=status,
        location_verified=location_verified and at is not None,
        accepted_hmos=list(hmos),
        last_verified_at=datetime.now(UTC) if status is VerificationStatus.VERIFIED else None,
    )
    db.add(facility)
    await db.flush()
    for entry in services:
        name_, verified, available = (
            (entry, "verified", "available") if isinstance(entry, str) else entry
        )
        db.add(
            FacilityService(
                facility_id=facility.id,
                service_type=normalize_service_type(name_),
                verification_status=VerificationStatus(verified),
                availability=ServiceAvailability(available),
            )
        )
    await db.commit()
    return facility


async def search(api: httpx.AsyncClient, query: str = "", expect: int = 200) -> list[dict]:
    r = await api.get(f"/facilities{query}")
    assert r.status_code == expect, r.text
    return r.json()


async def names(api: httpx.AsyncClient, query: str = "") -> list[str]:
    return [item["facility"]["name"] for item in await search(api, query)]


# ================================================================ basics
async def test_finder_is_public_and_lists_only_verified_facilities(api, db) -> None:
    await place(db, "Verified Clinic")
    await place(db, "Pending Clinic", status=VerificationStatus.PENDING)
    await place(db, "Rejected Clinic", status=VerificationStatus.REJECTED)
    await place(db, "No Location Clinic", at=None)

    r = await api.get("/facilities")  # no token
    assert r.status_code == 200, r.text
    assert [i["facility"]["name"] for i in r.json()] == ["No Location Clinic", "Verified Clinic"]
    assert r.headers["X-Total-Count"] == "2"


async def test_an_empty_finder(api, db) -> None:
    r = await api.get("/facilities?service=blood_bank")
    assert (r.status_code, r.json(), r.headers["X-Total-Count"]) == (200, [], "0")


async def test_result_shape(api, db) -> None:
    await place(
        db,
        "UNTH Ituku-Ozalla",
        services=("haematologist", "blood bank", ("transfusion", "pending", "available")),
        hmos=("Hygeia HMO", "NHIA"),
    )
    [item] = await search(api)
    assert set(item) == {"facility", "distance_km", "services"}
    facility = item["facility"]
    assert facility["accepted_hmos"] == ["Hygeia HMO", "NHIA"]
    assert facility["last_verified_at"] is not None  # FF-3
    assert (facility["latitude"], facility["longitude"]) == pytest.approx(ENUGU)
    assert item["distance_km"] is None  # no location sent
    # Only what a family can rely on: the pending transfusion service isn't listed.
    assert [s["service_type"] for s in item["services"]] == ["blood bank", "haematologist"]
    assert {s["verification_status"] for s in item["services"]} == {"verified"}
    # Nothing about who manages or verified the listing.
    assert "claimed_by" not in facility and "verified_by" not in facility


async def test_sorted_by_name_ignoring_case_without_a_location(api, db) -> None:
    for name in ("mercy clinic", "Annunciation Hospital", "Bethel Hospital"):
        await place(db, name)
    assert await names(api) == ["Annunciation Hospital", "Bethel Hospital", "mercy clinic"]


# ================================================================ FF-1 service filters
@pytest.mark.parametrize(
    ("kind", "stored"),
    [
        ("haematologist", "haematologist"),
        ("emergency_24h", "emergency_24h"),
        ("transfusion", "transfusion"),
        ("blood_bank", "blood_bank"),
        ("genotype_testing", "genotype_testing"),
    ],
)
async def test_each_service_filter(api, db, kind, stored) -> None:
    await place(db, "Has It", services=(stored,))
    await place(db, "Has Something Else", services=("physiotherapy",))
    await place(db, "Has Nothing")
    assert await names(api, f"?service={kind}") == ["Has It"]


@pytest.mark.parametrize(
    ("kind", "stored"),
    [
        ("haematologist", "Haematology"),
        ("haematologist", "Hematologist"),
        ("emergency_24h", "24-hour emergency"),
        ("emergency_24h", "24h  Emergency"),
        ("emergency_24h", "24/7 emergency"),
        ("transfusion", "Blood transfusion"),
        ("transfusion", "blood_transfusion"),
        ("blood_bank", "Blood-Bank"),
        ("blood_bank", "BLOOD BANK"),
        ("genotype_testing", "Hb electrophoresis"),
        ("genotype_testing", "genotyping"),
    ],
)
async def test_service_filters_accept_the_names_facilities_use(api, db, kind, stored) -> None:
    await place(db, "Clinic", services=(stored,))
    assert await names(api, f"?service={kind}") == ["Clinic"]


@pytest.mark.parametrize(
    ("kind", "stored"),
    [
        ("emergency_24h", "emergency"),  # not said to be 24 hours
        ("emergency_24h", "emergency daytime"),
        ("haematologist", "haematology referral letters"),
        ("blood_bank", "blood"),
        ("transfusion", "transfusion referral"),
        ("genotype_testing", "genotype counselling"),
    ],
)
async def test_similar_names_dont_count(api, db, kind, stored) -> None:
    await place(db, "Clinic", services=(stored,))
    assert await names(api, f"?service={kind}") == []


def test_every_filter_has_names_and_the_canonical_name_first() -> None:
    assert set(FINDER_SERVICE_NAMES) == set(FinderService)
    for kind, accepted in FINDER_SERVICE_NAMES.items():
        assert accepted, kind
        assert all(n == n.lower().strip() and "_" not in n and "-" not in n for n in accepted)
    assert FINDER_SERVICE_NAMES[FinderService.BLOOD_BANK][0] == "blood bank"


@pytest.mark.parametrize(
    "service",
    [
        ("blood_bank", "pending", "available"),
        ("blood_bank", "rejected", "available"),
        ("blood_bank", "verified", "temporarily_unavailable"),
    ],
)
async def test_unverified_or_unavailable_services_never_match(api, db, service) -> None:
    await place(db, "Clinic", services=(service,))
    assert await names(api, "?service=blood_bank") == []
    # The facility is still listed without the filter, but not with that service.
    [item] = await search(api)
    assert item["services"] == []


async def test_a_service_on_an_unverified_facility_never_matches(api, db) -> None:
    await place(db, "Pending", status=VerificationStatus.PENDING, services=("blood_bank",))
    assert await names(api, "?service=blood_bank") == []


async def test_several_services_must_all_match(api, db) -> None:
    await place(db, "All Three", services=("haematologist", "blood bank", "transfusion"))
    await place(db, "Two", services=("haematologist", "blood bank"))
    await place(db, "One", services=("haematologist",))
    await place(
        db,
        "Two Plus Unavailable",
        services=(
            "haematologist",
            "blood bank",
            ("transfusion", "verified", "temporarily_unavailable"),
        ),
    )
    assert await names(api, "?service=haematologist") == [
        "All Three",
        "One",
        "Two",
        "Two Plus Unavailable",
    ]
    assert await names(api, "?service=haematologist&service=blood_bank") == [
        "All Three",
        "Two",
        "Two Plus Unavailable",
    ]
    three = "?service=haematologist&service=blood_bank&service=transfusion"
    assert await names(api, three) == ["All Three"]
    # Asking twice for the same service changes nothing.
    assert await names(api, "?service=haematologist&service=haematologist") == await names(
        api, "?service=haematologist"
    )


async def test_two_synonyms_dont_double_count(api, db) -> None:
    """A facility listing "haematology" and "haematologist" appears once."""
    await place(db, "Clinic", services=("haematology", "haematologist", "blood bank"))
    r = await api.get("/facilities?service=haematologist")
    assert [i["facility"]["name"] for i in r.json()] == ["Clinic"]
    assert r.headers["X-Total-Count"] == "1"
    assert len(r.json()[0]["services"]) == 3


# ================================================================ FF-1 HMO filter
async def test_hmo_filter_ignores_case_and_spacing(api, db) -> None:
    await place(db, "Takes Hygeia", hmos=("Hygeia HMO", "NHIA"))
    await place(db, "Takes Others", hmos=("Reliance HMO",))
    await place(db, "Takes None")
    for query in ("Hygeia HMO", "hygeia hmo", "HYGEIA%20%20HMO", "%20Hygeia%20HMO%20"):
        assert await names(api, f"?hmo={query}") == ["Takes Hygeia"], query
    assert await names(api, "?hmo=nhia") == ["Takes Hygeia"]


async def test_hmo_must_match_the_whole_name(api, db) -> None:
    await place(db, "Clinic", hmos=("Hygeia HMO",))
    for query in ("Hygeia", "HMO", "Hygeia HMO Plus", "%25%25", "__", "_ygeia HMO"):
        assert await names(api, f"?hmo={query}") == [], query


async def test_hmo_and_services_together(api, db) -> None:
    await place(db, "Both", services=("blood_bank",), hmos=("AXA Mansard",))
    await place(db, "Service Only", services=("blood_bank",))
    await place(db, "HMO Only", hmos=("AXA Mansard",))
    assert await names(api, "?service=blood_bank&hmo=axa%20mansard") == ["Both"]


# ================================================================ text search
async def test_text_search_on_name_and_address(api, db) -> None:
    await place(db, "Parklane Specialist Hospital", address="GRA, Enugu")
    await place(db, "Mother of Christ", address="Ogui Road, Enugu")
    await place(db, "Faraway", address="Nsukka")
    assert await names(api, "?q=parklane") == ["Parklane Specialist Hospital"]
    assert await names(api, "?q=OGUI") == ["Mother of Christ"]
    assert await names(api, "?q=enugu") == ["Mother of Christ", "Parklane Specialist Hospital"]
    assert await names(api, "?q=%20%20specialist%20%20hospital%20") == [
        "Parklane Specialist Hospital"
    ]


async def test_text_search_treats_wildcards_literally(api, db) -> None:
    await place(db, "100% Care Clinic")
    await place(db, "St_Luke Hospital")
    await place(db, "Ordinary Clinic")
    # As wildcards, "%%" and "__" would match every facility.
    assert await names(api, "?q=%25%25") == []
    assert await names(api, "?q=__") == []
    assert await names(api, "?q=0%25") == ["100% Care Clinic"]  # "0%"
    assert await names(api, "?q=t_l") == ["St_Luke Hospital"]
    assert await names(api, "?q=%5C%5C") == []  # a backslash pair
    assert await names(api, "?q='%3B%20drop%20table%20facilities%3B--") == []
    assert len(await names(api)) == 3  # still there


# ================================================================ FF-2 distance
async def test_near_a_point_nearest_first_with_distances(api, db) -> None:
    await place(db, "Far", at=(ENUGU[0] + 0.09, ENUGU[1]))  # about 10 km
    await place(db, "Here", at=ENUGU)
    await place(db, "Near", at=(ENUGU[0] + 0.02, ENUGU[1]))  # about 2.2 km
    items = await search(api, f"?latitude={ENUGU[0]}&longitude={ENUGU[1]}")
    assert [i["facility"]["name"] for i in items] == ["Here", "Near", "Far"]
    assert items[0]["distance_km"] == 0
    assert items[1]["distance_km"] == pytest.approx(2.21, abs=0.05)
    assert items[2]["distance_km"] == pytest.approx(9.96, abs=0.1)


async def test_radius_limits_the_search(api, db) -> None:
    await place(db, "Here", at=ENUGU)
    await place(db, "Near", at=(ENUGU[0] + 0.02, ENUGU[1]))
    await place(db, "Far", at=(ENUGU[0] + 0.09, ENUGU[1]))
    near = f"?latitude={ENUGU[0]}&longitude={ENUGU[1]}"
    assert await names(api, f"{near}&radius_km=5") == ["Here", "Near"]
    assert await names(api, f"{near}&radius_km=1") == ["Here"]
    assert await names(api, f"{near}&radius_km=500") == ["Here", "Near", "Far"]


async def test_near_search_needs_a_confirmed_location(api, db) -> None:
    """A distance from an unchecked location would mislead, so those are left out."""
    await place(db, "Confirmed", at=ENUGU)
    await place(db, "Unconfirmed", at=ENUGU, location_verified=False)
    await place(db, "No Location", at=None)
    near = f"?latitude={ENUGU[0]}&longitude={ENUGU[1]}"
    assert await names(api, near) == ["Confirmed"]
    # Without a location, all three verified listings are found.
    assert await names(api) == ["Confirmed", "No Location", "Unconfirmed"]


async def test_equal_distances_sort_by_name(api, db) -> None:
    for name in ("Zion Clinic", "Alpha Clinic", "Mercy Clinic"):
        await place(db, name, at=ENUGU)
    assert await names(api, f"?latitude={ENUGU[0]}&longitude={ENUGU[1]}") == [
        "Alpha Clinic",
        "Mercy Clinic",
        "Zion Clinic",
    ]


async def test_everything_at_once(api, db) -> None:
    """A guardian in Enugu at night: haematologist and blood bank, Hygeia, within 5 km."""
    await place(
        db,
        "Right One",
        at=(ENUGU[0] + 0.01, ENUGU[1]),
        services=("haematologist", "blood bank"),
        hmos=("Hygeia HMO",),
    )
    await place(
        db,
        "Too Far",
        at=(ENUGU[0] + 0.2, ENUGU[1]),
        services=("haematologist", "blood bank"),
        hmos=("Hygeia HMO",),
    )
    await place(
        db,
        "Wrong HMO",
        at=ENUGU,
        services=("haematologist", "blood bank"),
        hmos=("NHIA",),
    )
    await place(db, "Missing Service", at=ENUGU, services=("haematologist",), hmos=("Hygeia HMO",))
    query = (
        f"?latitude={ENUGU[0]}&longitude={ENUGU[1]}&radius_km=5"
        "&service=haematologist&service=blood_bank&hmo=hygeia%20hmo"
    )
    items = await search(api, query)
    assert [i["facility"]["name"] for i in items] == ["Right One"]
    assert items[0]["distance_km"] == pytest.approx(1.11, abs=0.05)


# ================================================================ paging
async def test_paging_with_a_total(api, db) -> None:
    for i in range(7):
        await place(db, f"Clinic {i}", services=("blood bank",))
    await place(db, "No Blood Bank")
    pages = []
    for offset in (0, 3, 6):
        r = await api.get(f"/facilities?service=blood_bank&limit=3&offset={offset}")
        assert r.headers["X-Total-Count"] == "7"
        pages.append([i["facility"]["name"] for i in r.json()])
    assert pages == [
        ["Clinic 0", "Clinic 1", "Clinic 2"],
        ["Clinic 3", "Clinic 4", "Clinic 5"],
        ["Clinic 6"],
    ]
    r = await api.get("/facilities?service=blood_bank&offset=50")
    assert (r.json(), r.headers["X-Total-Count"]) == ([], "7")
    assert len(await search(api)) == 8  # default limit is 20


# ================================================================ validation
@pytest.mark.parametrize(
    "query",
    [
        "?service=dialysis",  # not an FF-1 filter
        "?service=",
        "?latitude=6.4",  # half a location
        "?longitude=7.5",
        "?radius_km=5",  # a radius needs a location
        "?latitude=6.4&longitude=7.5&radius_km=0",
        "?latitude=6.4&longitude=7.5&radius_km=501",
        "?latitude=91&longitude=7.5",
        "?latitude=6.4&longitude=181",
        "?latitude=north&longitude=7.5",
        "?limit=0",
        "?limit=101",
        "?offset=-1",
        "?offset=10001",
        "?q=a",
        "?q=" + "x" * 101,
        "?hmo=a",
        "?servce=blood_bank",  # a typo is an error, not a silent "no filter"
        "?service_type=blood_bank",
    ],
)
async def test_bad_queries_are_refused(api, db, query) -> None:
    r = await api.get(f"/facilities{query}")
    assert r.status_code == 422, (query, r.text)


async def test_too_many_service_filters_are_refused(api, db) -> None:
    six = "&".join(f"service={k.value}" for k in FinderService) + "&service=blood_bank"
    assert (await api.get(f"/facilities?{six}")).status_code == 422
    five = "&".join(f"service={k.value}" for k in FinderService)
    assert (await api.get(f"/facilities?{five}")).status_code == 200


# ================================================================ HMOs on a listing
async def test_registering_a_facility_with_hmos(api, db) -> None:
    admin = await make_user(db, UserRole.FACILITY_ADMIN, "Facility Admin")
    r = await api.post(
        "/facilities",
        json={
            "name": "Mercy Specialist",
            "address": "5 Zik Avenue, Enugu",
            "phone_number": "+2348031110000",
            "accepted_hmos": ["  Hygeia   HMO ", "NHIA", "hygeia hmo", "Reliance HMO", "NHIA"],
        },
        headers=auth(admin),
    )
    assert r.status_code == 201, r.text
    # Trimmed, spaces collapsed, repeats dropped (ignoring case), order kept.
    assert r.json()["accepted_hmos"] == ["Hygeia HMO", "NHIA", "Reliance HMO"]
    again = await api.get(f"/facilities/{r.json()['id']}")
    assert again.json()["accepted_hmos"] == ["Hygeia HMO", "NHIA", "Reliance HMO"]


async def test_hmos_default_to_none(api, db) -> None:
    ops = await make_user(db, UserRole.OPS, "Ops")
    r = await api.post(
        "/facilities",
        json={"name": "Plain", "address": "1 Plain Road", "phone_number": "+2348031110001"},
        headers=auth(ops),
    )
    assert r.json()["accepted_hmos"] == []


async def test_updating_hmos_replaces_the_list(api, db) -> None:
    ops = await make_user(db, UserRole.OPS, "Ops")
    facility = await place(db, "Clinic", hmos=("Hygeia HMO", "NHIA"))
    url, h = f"/facilities/{facility.id}", auth(ops)

    r = await api.patch(url, json={"accepted_hmos": ["AXA Mansard"]}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["accepted_hmos"] == ["AXA Mansard"]
    assert await names(api, "?hmo=nhia") == []
    assert await names(api, "?hmo=axa%20mansard") == ["Clinic"]

    # Changing other fields leaves the HMOs alone.
    r = await api.patch(url, json={"name": "Clinic Renamed"}, headers=h)
    assert r.json()["accepted_hmos"] == ["AXA Mansard"]

    # [] clears them; null is refused.
    r = await api.patch(url, json={"accepted_hmos": []}, headers=h)
    assert r.json()["accepted_hmos"] == []
    r = await api.patch(url, json={"accepted_hmos": None}, headers=h)
    assert r.status_code == 422


async def test_only_the_owner_or_ops_changes_hmos(api, db) -> None:
    owner = await make_user(db, UserRole.FACILITY_ADMIN, "Owner")
    other = await make_user(db, UserRole.FACILITY_ADMIN, "Other Admin")
    patient = await make_user(db, UserRole.PATIENT, "Patient")
    r = await api.post(
        "/facilities",
        json={"name": "Owned", "address": "2 Owned Road", "phone_number": "+2348031110002"},
        headers=auth(owner),
    )
    url = f"/facilities/{r.json()['id']}"
    body = {"accepted_hmos": ["Fake HMO"]}
    assert (await api.patch(url, json=body, headers=auth(other))).status_code == 403
    assert (await api.patch(url, json=body, headers=auth(patient))).status_code == 403
    assert (await api.patch(url, json=body)).status_code == 401
    assert (await api.patch(url, json=body, headers=auth(owner))).status_code == 200


@pytest.mark.parametrize(
    "hmos",
    [
        ["H"],  # too short
        ["  x  "],
        ["x" * 101],
        [f"HMO {i}" for i in range(51)],  # more than 50
        "Hygeia HMO",  # not a list
        [None],
        [42],
    ],
)
async def test_bad_hmo_lists_are_refused(api, db, hmos) -> None:
    ops = await make_user(db, UserRole.OPS, "Ops")
    body = {"name": "Clinic", "address": "1 Clinic Road", "phone_number": "+2348031110003"}
    r = await api.post("/facilities", json={**body, "accepted_hmos": hmos}, headers=auth(ops))
    assert r.status_code == 422, r.text
    facility = await place(db, "Existing")
    r = await api.patch(
        f"/facilities/{facility.id}", json={"accepted_hmos": hmos}, headers=auth(ops)
    )
    assert r.status_code == 422


async def test_fifty_hmos_is_the_limit(api, db) -> None:
    ops = await make_user(db, UserRole.OPS, "Ops")
    body = {
        "name": "Clinic",
        "address": "1 Clinic Road",
        "phone_number": "+2348031110004",
        "accepted_hmos": [f"HMO {i}" for i in range(50)],
    }
    r = await api.post("/facilities", json=body, headers=auth(ops))
    assert r.status_code == 201
    assert len(r.json()["accepted_hmos"]) == 50


async def test_database_refuses_more_than_fifty_hmos(db) -> None:
    facility = await place(db, "Clinic")
    facility.accepted_hmos = [f"HMO {i}" for i in range(51)]
    with pytest.raises(IntegrityError, match="accepted_hmos_limit"):
        await db.commit()
    await db.rollback()


async def test_existing_rows_get_an_empty_list(db) -> None:
    await db.execute(
        text(
            "INSERT INTO facilities (id, name, address, phone_number) "
            "VALUES (gen_random_uuid(), 'Raw', 'Raw Road', '+2348000000000')"
        )
    )
    await db.commit()
    assert await db.scalar(select(Facility.accepted_hmos).where(Facility.name == "Raw")) == []


async def test_nearby_still_works_and_shows_hmos(api, db) -> None:
    await place(db, "Clinic", services=("blood bank",), hmos=("NHIA",))
    r = await api.get(
        f"/facilities/nearby?latitude={ENUGU[0]}&longitude={ENUGU[1]}&service_type=blood%20bank"
    )
    assert r.status_code == 200, r.text
    [item] = r.json()
    assert item["facility"]["accepted_hmos"] == ["NHIA"]


# ================================================================ end to end
async def test_a_listing_reaches_the_finder_only_after_ops_verifies_it(api, db) -> None:
    admin = await make_user(db, UserRole.FACILITY_ADMIN, "Facility Admin")
    ops = await make_user(db, UserRole.OPS, "Ops")
    r = await api.post(
        "/facilities",
        json={
            "name": "Annunciation Specialist Hospital",
            "address": "Emene, Enugu",
            "phone_number": "+2348031110005",
            "latitude": ENUGU[0],
            "longitude": ENUGU[1],
            "accepted_hmos": ["Hygeia HMO"],
        },
        headers=auth(admin),
    )
    facility_id = r.json()["id"]
    for service_type in ("Haematologist", "Blood Bank"):
        r = await api.post(
            f"/facilities/{facility_id}/service",
            json={"service_type": service_type},
            headers=auth(admin),
        )
        assert r.status_code == 201, r.text

    query = "?service=haematologist&service=blood_bank&hmo=hygeia%20hmo"
    assert await names(api, query) == []  # pending

    r = await api.post(
        f"/ops/facilities/{facility_id}/verify", json={"decision": "verified"}, headers=auth(ops)
    )
    assert r.status_code == 200, r.text
    items = await search(api, f"{query}&latitude={ENUGU[0]}&longitude={ENUGU[1]}&radius_km=2")
    assert [i["facility"]["id"] for i in items] == [facility_id]
    assert items[0]["facility"]["last_verified_at"] is not None

    # The blood bank runs out: the facility drops out of a blood-bank search at once.
    services = (await api.get(f"/facilities/{facility_id}/service")).json()
    blood_bank = next(s for s in services if s["service_type"] == "blood bank")
    r = await api.patch(
        f"/facilities/{facility_id}/service/{blood_bank['id']}",
        json={"availability": "temporarily_unavailable"},
        headers=auth(admin),
    )
    assert r.status_code == 200, r.text
    assert await names(api, query) == []
    assert await names(api, "?service=haematologist") == ["Annunciation Specialist Hospital"]


async def test_route_order_keeps_nearby_and_detail_working(api, db) -> None:
    facility = await place(db, "Clinic")
    assert (await api.get(f"/facilities/{facility.id}")).status_code == 200
    assert (await api.get(f"/facilities/{uuid.uuid4()}")).status_code == 404
    assert (await api.get("/facilities/not-a-uuid")).status_code == 422
