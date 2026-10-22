"""Tests for Day 14: deterministic caregiver matching (POST /caregivers/match).

The availability and scoring rules are unit-tested as plain functions. The endpoint
tests insert caregivers, patients and bookings directly, then call the real route
with real access tokens, so the PostGIS filters, access rules and audit run for real.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import httpx
import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.permissions import UserRole
from app.core.security import create_access_token
from app.domains.auth.models import User
from app.domains.bookings.models import Booking, BookingStatus, BookingType
from app.domains.bookings.service import LAGOS
from app.domains.caregivers.matching import is_available, score
from app.domains.caregivers.models import (
    AvailabilityWindow,
    Caregiver,
    CaregiverStatus,
    CaregiverTier,
)
from app.domains.patients.models import FamilyLink, Guardian, Patient

ENUGU = (6.4413, 7.4988)  # the patient's home
KM = 1 / 111.2  # degrees of latitude in one kilometre (close enough for tests)


# ----------------------------------------------------------------- builders
def point(lat: float, lng: float) -> WKTElement:
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


def north_of_enugu(km: float) -> tuple[float, float]:
    return ENUGU[0] + km * KM, ENUGU[1]


def auth(user: User) -> dict[str, str]:
    token, _ = create_access_token(user.id, user.role.value)
    return {"Authorization": f"Bearer {token}"}


def window(day: int, start: str, end: str, **dates: date) -> AvailabilityWindow:
    return AvailabilityWindow(
        day_of_week=day,
        start_time=time.fromisoformat(start),
        end_time=time.fromisoformat(end),
        **dates,
    )


def every_day() -> list[AvailabilityWindow]:
    return [window(day, "00:00", "23:59") for day in range(7)]


def slot(days_ahead: int = 2, start_hour: int = 9, hours: int = 4) -> tuple[datetime, datetime]:
    day = datetime.now(LAGOS).date() + timedelta(days=days_ahead)
    starts_at = datetime.combine(day, time(start_hour), LAGOS)
    return starts_at, starts_at + timedelta(hours=hours)


async def make_user(db: AsyncSession, role: UserRole, name: str = "Test User") -> User:
    phone = "+234803" + "".join(random.choices("0123456789", k=7))
    user = User(phone=phone, full_name=name, role=role, phone_verified_at=datetime.now(UTC))
    db.add(user)
    await db.commit()
    return user


async def make_caregiver(
    db: AsyncSession,
    *,
    name: str = "Chi Okeke",
    km: float = 2,
    radius_km: int = 20,
    tier: CaregiverTier = CaregiverTier.COMPANION,
    status: CaregiverStatus = CaregiverStatus.APPROVED,
    rate: int | None = 250_000,
    languages: list[str] | None = None,
    years: int | None = 3,
    rating: tuple[str, int] | None = None,
    windows: list[AvailabilityWindow] | None = None,
) -> Caregiver:
    user = await make_user(db, UserRole.CAREGIVER, name)
    caregiver = Caregiver(
        user_id=user.id,
        tier=tier,
        status=status,
        hourly_rate_minor=rate,
        base_location=point(*north_of_enugu(km)),
        service_radius_km=radius_km,
        languages=languages or ["english"],
        years_experience=years,
        avg_rating=Decimal(rating[0]) if rating else None,
        rating_count=rating[1] if rating else 0,
        availability_windows=every_day() if windows is None else windows,
    )
    db.add(caregiver)
    await db.commit()
    return caregiver


async def make_patient(
    db: AsyncSession, *, minor: bool = False, home: bool = True
) -> tuple[User | None, Patient]:
    """An adult patient with their own login, or a minor without one."""
    user = None if minor else await make_user(db, UserRole.PATIENT, "Chidi Okafor")
    born = date.today().replace(year=date.today().year - (10 if minor else 30))
    patient = Patient(
        user_id=user.id if user else None,
        full_name="Chidi Okafor",
        date_of_birth=born,
        home_location=point(*ENUGU) if home else None,
        created_by=user.id if user else None,
    )
    db.add(patient)
    await db.commit()
    return user, patient


async def make_guardian(db: AsyncSession, patient: Patient, *, can_approve: bool = True) -> User:
    user = await make_user(db, UserRole.GUARDIAN, "Ngozi Okafor")
    db.add(
        Guardian(
            patient_id=patient.id,
            user_id=user.id,
            relationship="parent",
            is_primary=True,
            can_approve_caregivers=can_approve,
        )
    )
    await db.commit()
    return user


def request_body(patient: Patient, **overrides: object) -> dict:
    starts_at, ends_at = slot()
    body: dict = {
        "patient_id": str(patient.id),
        "booking_type": "hourly",
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
    }
    body.update(overrides)
    return body


async def match(
    api: httpx.AsyncClient, user: User, patient: Patient, **overrides
) -> httpx.Response:
    return await api.post(
        "/caregivers/match", json=request_body(patient, **overrides), headers=auth(user)
    )


def names(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [m["full_name"] for m in response.json()["results"]]


def code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


# ------------------------------------------------------- availability units
MONDAY = date(2026, 11, 2)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), LAGOS)


def test_a_window_covering_the_slot_is_available() -> None:
    assert is_available([window(0, "08:00", "17:00")], at(MONDAY, 9), at(MONDAY, 13))


def test_a_slot_running_past_the_window_is_not_available() -> None:
    assert not is_available([window(0, "08:00", "12:00")], at(MONDAY, 9), at(MONDAY, 13))


def test_back_to_back_windows_join_up() -> None:
    windows = [window(0, "08:00", "12:00"), window(0, "12:00", "17:00")]
    assert is_available(windows, at(MONDAY, 9), at(MONDAY, 16))


def test_a_gap_between_windows_breaks_availability() -> None:
    windows = [window(0, "08:00", "11:00"), window(0, "12:00", "17:00")]
    assert not is_available(windows, at(MONDAY, 9), at(MONDAY, 16))


def test_an_overnight_slot_needs_both_evenings_and_mornings() -> None:
    late = window(0, "18:00", "23:59")  # Monday evening, reaching midnight
    early = window(1, "00:00", "08:00")  # Tuesday morning
    tuesday = MONDAY + timedelta(days=1)
    assert is_available([late, early], at(MONDAY, 20), at(tuesday, 7))
    assert not is_available([late], at(MONDAY, 20), at(tuesday, 7))


def test_windows_only_count_on_their_valid_dates() -> None:
    expired = window(0, "08:00", "17:00", valid_to=MONDAY - timedelta(days=1))
    assert not is_available([expired], at(MONDAY, 9), at(MONDAY, 13))


def test_slots_are_read_in_lagos_time() -> None:
    # 08:00 UTC is 09:00 in Lagos: inside a 09:00-12:00 Lagos window.
    starts_at = datetime.combine(MONDAY, time(8), UTC)
    assert is_available([window(0, "09:00", "12:00")], starts_at, starts_at + timedelta(hours=2))


def test_no_windows_means_not_available() -> None:
    assert not is_available([], at(MONDAY, 9), at(MONDAY, 13))


# ---------------------------------------------------------- scoring units
def fit(**overrides):
    args = {
        "distance_km": 0.0,
        "avg_rating": None,
        "rating_count": 0,
        "caregiver_languages": ["english"],
        "wanted_languages": [],
        "years_experience": 0,
    }
    args.update(overrides)
    return score(**args)


def test_distance_factor_halves_every_five_km() -> None:
    assert fit(distance_km=0).factors.distance == 1.0
    assert fit(distance_km=5).factors.distance == 0.5


def test_a_new_caregiver_starts_near_four_stars() -> None:
    assert fit().factors.rating == 0.8


def test_many_good_reviews_beat_one_perfect_review() -> None:
    one_perfect = fit(avg_rating=5.0, rating_count=1)
    many_good = fit(avg_rating=4.8, rating_count=40)
    assert many_good.factors.rating > one_perfect.factors.rating


def test_language_factor_counts_the_languages_spoken() -> None:
    result = fit(caregiver_languages=["english", "igbo"], wanted_languages=["igbo", "pidgin"])
    assert result.factors.language == 0.5
    assert result.languages_matched == ["igbo"]


def test_no_language_preference_is_neutral() -> None:
    assert fit(wanted_languages=[]).factors.language == 1.0


def test_experience_is_capped_at_ten_years() -> None:
    assert fit(years_experience=25).factors.experience == 1.0


# ------------------------------------------------------------- endpoint
async def test_ranks_bookable_caregivers_and_explains_the_score(api, db) -> None:
    patient_user, patient = await make_patient(db)
    await make_caregiver(db, name="Near Good", km=1, rating=("4.9", 20), years=8)
    await make_caregiver(db, name="Far New", km=12)
    response = await match(api, patient_user, patient)

    assert names(response) == ["Near Good", "Far New"]
    best = response.json()["results"][0]
    assert 0.9 < best["distance_km"] < 1.1
    assert set(best["factors"]) == {"distance", "rating", "language", "experience"}
    assert best["estimated_total_minor"] == 4 * 250_000  # four hours at the hourly rate
    assert response.json()["guardian_approval_required"] is False
    assert "base_location" not in best and "user_id" not in best


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": CaregiverStatus.VERIFIED},  # not yet approved by ops (TS-1)
        {"rate": None},  # can't be booked without a rate
        {"km": 30, "radius_km": 10},  # the patient is outside their service radius
        {"windows": []},  # hasn't said when they're available
        {"windows": [window(d, "14:00", "20:00") for d in range(7)]},  # afternoons only
    ],
)
async def test_unbookable_caregivers_are_left_out(api, db, overrides) -> None:
    patient_user, patient = await make_patient(db)
    await make_caregiver(db, name="Excluded", **overrides)
    await make_caregiver(db, name="Included")
    assert names(await match(api, patient_user, patient)) == ["Included"]


async def test_a_caregiver_with_an_overlapping_booking_is_left_out(api, db) -> None:
    patient_user, patient = await make_patient(db)
    busy = await make_caregiver(db, name="Busy")
    await make_caregiver(db, name="Free")
    starts_at, ends_at = slot()
    db.add(
        Booking(
            patient_id=patient.id,
            caregiver_id=busy.id,
            booked_by=patient_user.id,
            booking_type=BookingType.HOURLY,
            status=BookingStatus.ACCEPTED,
            starts_at=starts_at + timedelta(hours=1),
            ends_at=ends_at + timedelta(hours=1),
            service_location=point(*ENUGU),
            service_address="12 Ogui Road, Enugu",
            rate_minor=250_000,
            total_minor=1_000_000,
            currency="NGN",
        )
    )
    await db.commit()
    assert names(await match(api, patient_user, patient)) == ["Free"]


async def test_language_preference_changes_the_order(api, db) -> None:
    patient_user, patient = await make_patient(db)
    await make_caregiver(db, name="English Only", km=1, languages=["english"])
    await make_caregiver(db, name="Speaks Igbo", km=3, languages=["english", "igbo"])
    assert names(await match(api, patient_user, patient)) == ["English Only", "Speaks Igbo"]
    response = await match(api, patient_user, patient, languages=["Igbo"])
    assert names(response) == ["Speaks Igbo", "English Only"]
    assert response.json()["results"][0]["languages_matched"] == ["igbo"]


async def test_a_minor_is_only_offered_trained_carers_or_nurses(api, db) -> None:
    _, patient = await make_patient(db, minor=True)
    guardian = await make_guardian(db, patient)
    await make_caregiver(db, name="Companion", tier=CaregiverTier.COMPANION)
    await make_caregiver(db, name="Trained", tier=CaregiverTier.TRAINED_CARER)
    await make_caregiver(db, name="Nurse", tier=CaregiverTier.LICENSED_NURSE, km=5)
    response = await match(api, guardian, patient)
    assert set(names(response)) == {"Trained", "Nurse"}
    assert response.json()["min_tier_applied"] == "trained_carer"


async def test_min_tier_can_be_raised_for_an_adult(api, db) -> None:
    patient_user, patient = await make_patient(db)
    await make_caregiver(db, name="Companion", tier=CaregiverTier.COMPANION)
    await make_caregiver(db, name="Nurse", tier=CaregiverTier.LICENSED_NURSE)
    response = await match(api, patient_user, patient, min_tier="licensed_nurse")
    assert names(response) == ["Nurse"]


@pytest.mark.parametrize(("can_approve", "required"), [(True, False), (False, True)])
async def test_guardian_approval_flag_follows_the_booking_rule(
    api, db, can_approve, required
) -> None:
    _, patient = await make_patient(db, minor=True)
    guardian = await make_guardian(db, patient, can_approve=can_approve)
    response = await match(api, guardian, patient)
    assert response.status_code == 200, response.text
    assert response.json()["guardian_approval_required"] is required


async def test_service_location_overrides_the_home(api, db) -> None:
    patient_user, patient = await make_patient(db, home=False)
    await make_caregiver(db, name="Near Clinic", km=40, radius_km=5)
    clinic = north_of_enugu(41)
    response = await match(
        api, patient_user, patient, service_location={"latitude": clinic[0], "longitude": clinic[1]}
    )
    assert names(response) == ["Near Clinic"]


async def test_no_location_at_all_is_rejected(api, db) -> None:
    patient_user, patient = await make_patient(db, home=False)
    response = await match(api, patient_user, patient)
    assert response.status_code == 400
    assert code(response) == "service_location_required"


async def test_a_slot_in_the_past_is_rejected(api, db) -> None:
    patient_user, patient = await make_patient(db)
    starts_at = datetime.now(LAGOS) - timedelta(hours=3)
    response = await match(
        api,
        patient_user,
        patient,
        starts_at=starts_at.isoformat(),
        ends_at=(starts_at + timedelta(hours=2)).isoformat(),
    )
    assert response.status_code == 400
    assert code(response) == "slot_in_past"


async def test_results_are_limited(api, db) -> None:
    patient_user, patient = await make_patient(db)
    for km in (1, 2, 3):
        await make_caregiver(db, name=f"Caregiver {km}", km=km)
    assert len(names(await match(api, patient_user, patient, limit=2))) == 2


async def test_the_caller_is_never_matched_with_themself(api, db) -> None:
    patient_user, patient = await make_patient(db)
    caregiver = await make_caregiver(db, name="Self")
    caregiver.user_id = patient_user.id  # the patient is also a caregiver (edge case)
    await db.commit()
    assert names(await match(api, patient_user, patient)) == []


async def test_ops_can_match(api, db) -> None:
    _, patient = await make_patient(db)
    ops = await make_user(db, UserRole.OPS)
    await make_caregiver(db, name="Chi")
    assert names(await match(api, ops, patient)) == ["Chi"]


async def test_family_abroad_cannot_match(api, db) -> None:
    _, patient = await make_patient(db)
    relative = await make_user(db, UserRole.FAMILY_ABROAD)
    db.add(FamilyLink(patient_id=patient.id, user_id=relative.id, share_scope={}))
    await db.commit()
    response = await match(api, relative, patient)
    assert response.status_code == 403
    assert code(response) == "role_forbidden"


async def test_a_guardian_of_someone_else_gets_not_found(api, db) -> None:
    _, patient = await make_patient(db)
    _, other_patient = await make_patient(db)
    stranger = await make_guardian(db, other_patient)
    response = await match(api, stranger, patient)
    assert response.status_code == 404


async def test_caregivers_cannot_match(api, db) -> None:
    _, patient = await make_patient(db)
    caregiver_user = await make_user(db, UserRole.CAREGIVER)
    response = await match(api, caregiver_user, patient)
    assert response.status_code == 403


async def test_matching_needs_a_login(api, db) -> None:
    _, patient = await make_patient(db)
    response = await api.post("/caregivers/match", json=request_body(patient))
    assert response.status_code == 401


async def test_each_match_is_audited(api, db) -> None:
    patient_user, patient = await make_patient(db)
    await make_caregiver(db)
    await match(api, patient_user, patient)
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "caregiver.match"))
    assert entry is not None
    assert entry.actor_id == patient_user.id
    assert entry.entity_id == patient.id
    assert entry.meta["results"] == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"ends_at": None},  # filled in below: ends before it starts
        {"limit": 0},
        {"unknown_field": True},
    ],
)
async def test_bad_requests_are_rejected(api, db, overrides) -> None:
    patient_user, patient = await make_patient(db)
    if overrides.get("ends_at", 1) is None:
        starts_at, _ = slot()
        overrides = {"ends_at": (starts_at - timedelta(hours=1)).isoformat()}
    response = await match(api, patient_user, patient, **overrides)
    assert response.status_code == 422
