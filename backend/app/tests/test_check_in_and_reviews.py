"""Tests for Day 12: booking-level check-in, check-out and review, and rating averages.

Bookings and shifts are inserted directly (the booking flow has its own tests) and each
test then drives the real endpoints with real access tokens.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.permissions import UserRole
from app.core.security import create_access_token
from app.domains.auth.models import User
from app.domains.bookings.models import (
    Booking,
    BookingStatus,
    BookingType,
    CheckIn,
    Review,
    Shift,
    ShiftStatus,
)
from app.domains.caregivers.models import Caregiver, CaregiverStatus, CaregiverTier
from app.domains.patients.models import Patient

ENUGU = (6.4413, 7.4988)  # the booking's service location
NEARBY = (6.4414, 7.4989)  # about 15 m away: inside the 200 m geofence
FAR = (6.4513, 7.4988)  # about 1.1 km away: outside it


# ----------------------------------------------------------------- builders
def point(lat: float, lng: float) -> WKTElement:
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


def auth(user: User) -> dict[str, str]:
    token, _ = create_access_token(user.id, user.role.value)
    return {"Authorization": f"Bearer {token}"}


async def make_user(db: AsyncSession, role: UserRole, name: str = "Test User") -> User:
    phone = "+234803" + "".join(random.choices("0123456789", k=7))
    user = User(phone=phone, full_name=name, role=role, phone_verified_at=datetime.now(UTC))
    db.add(user)
    await db.commit()
    return user


class Scene:
    """One paid booking with a caregiver, the patient who booked, and its shifts."""

    def __init__(self, caregiver_user, caregiver, patient_user, patient, booking, shifts):
        self.caregiver_user: User = caregiver_user
        self.caregiver: Caregiver = caregiver
        self.patient_user: User = patient_user
        self.patient: Patient = patient
        self.booking: Booking = booking
        self.shifts: list[Shift] = shifts

    @property
    def id(self) -> uuid.UUID:
        return self.booking.id


def around_now(minutes_from: int, minutes_to: int) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now + timedelta(minutes=minutes_from), now + timedelta(minutes=minutes_to)


async def make_scene(
    db: AsyncSession,
    *,
    windows: list[tuple[int, int]] | None = None,
    status: BookingStatus = BookingStatus.PAID,
    caregiver: tuple[User, Caregiver] | None = None,
) -> Scene:
    """`windows` are shifts as (minutes from now to start, minutes from now to end)."""
    windows = windows or [(-30, 120)]  # one shift that started half an hour ago
    if caregiver is None:
        caregiver_user = await make_user(db, UserRole.CAREGIVER, "Chi Okeke")
        caregiver_row = Caregiver(
            user_id=caregiver_user.id,
            tier=CaregiverTier.COMPANION,
            status=CaregiverStatus.APPROVED,
        )
        db.add(caregiver_row)
        await db.commit()
    else:
        caregiver_user, caregiver_row = caregiver
    patient_user = await make_user(db, UserRole.PATIENT, "Chidi Okafor")
    patient = Patient(
        user_id=patient_user.id,
        full_name="Chidi Okafor",
        date_of_birth=date(1996, 5, 17),
        created_by=patient_user.id,
    )
    db.add(patient)
    await db.commit()

    starts_at = datetime.now(UTC) + timedelta(minutes=min(w[0] for w in windows))
    ends_at = datetime.now(UTC) + timedelta(minutes=max(w[1] for w in windows))
    booking = Booking(
        patient_id=patient.id,
        caregiver_id=caregiver_row.id,
        booked_by=patient_user.id,
        booking_type=BookingType.HOURLY,
        status=status,
        starts_at=starts_at,
        ends_at=ends_at,
        service_location=point(*ENUGU),
        service_address="12 Ogui Road, Enugu",
        rate_minor=250_000,
        total_minor=500_000,
        currency="NGN",
    )
    db.add(booking)
    await db.flush()
    shifts = []
    for start_offset, end_offset in windows:
        shift = Shift(
            booking_id=booking.id,
            scheduled_start=datetime.now(UTC) + timedelta(minutes=start_offset),
            scheduled_end=datetime.now(UTC) + timedelta(minutes=end_offset),
        )
        db.add(shift)
        shifts.append(shift)
    await db.commit()
    return Scene(caregiver_user, caregiver_row, patient_user, patient, booking, shifts)


def gps(where: tuple[float, float] = NEARBY, *, minutes_ago: float = 1, **extra) -> dict:
    stamp = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    body = {
        "location": {"latitude": where[0], "longitude": where[1]},
        "accuracy_m": 8,
        "client_timestamp": stamp.isoformat(),
        "device_id": "phone-1",
    }
    body.update(extra)
    return body


def code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


async def check_in(api, scene: Scene, **kwargs) -> httpx.Response:
    return await api.post(
        f"/bookings/{scene.id}/checkin", json=gps(**kwargs), headers=auth(scene.caregiver_user)
    )


async def check_out(api, scene: Scene, **kwargs) -> httpx.Response:
    return await api.post(
        f"/bookings/{scene.id}/checkout", json=gps(**kwargs), headers=auth(scene.caregiver_user)
    )


async def completed_scene(api, db) -> Scene:
    """A booking whose only shift has been checked in and out."""
    scene = await make_scene(db)
    assert (await check_in(api, scene, minutes_ago=20)).status_code == 201
    assert (await check_out(api, scene, minutes_ago=2)).status_code == 201
    return scene


async def booking_status(api, scene: Scene) -> str:
    response = await api.get(f"/bookings/{scene.id}", headers=auth(scene.caregiver_user))
    return response.json()["status"]


async def audit_actions(db: AsyncSession) -> list[str]:
    return list(await db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


async def rating_summary(db: AsyncSession, caregiver_id: uuid.UUID):
    row = (
        await db.execute(
            select(Caregiver.avg_rating, Caregiver.rating_count)
            .where(Caregiver.id == caregiver_id)
            .execution_options(populate_existing=True)
        )
    ).one()
    return row.avg_rating, row.rating_count


# ------------------------------------------------------------------ check-in
async def test_caregiver_checks_in_at_the_service_location(api, db) -> None:
    scene = await make_scene(db)
    response = await check_in(api, scene)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "check_in"
    assert body["within_geofence"] is True
    assert body["distance_m"] < 200
    assert body["shift_id"] == str(scene.shifts[0].id)
    assert await booking_status(api, scene) == "in_progress"
    assert "booking.check_in" in await audit_actions(db)


async def test_check_in_far_from_the_service_location_is_recorded_and_flagged(api, db) -> None:
    scene = await make_scene(db)
    response = await check_in(api, scene, where=FAR)
    assert response.status_code == 201
    assert response.json()["within_geofence"] is False
    ops = await make_user(db, UserRole.OPS)
    flagged = await api.get("/bookings/ops/flagged-check-ins", headers=auth(ops))
    assert [c["id"] for c in flagged.json()] == [response.json()["id"]]


async def test_offline_check_in_keeps_the_device_time(api, db) -> None:
    scene = await make_scene(db)
    response = await check_in(api, scene, minutes_ago=25)  # the phone was offline
    body = response.json()
    sent = datetime.fromisoformat(body["client_timestamp"])
    received = datetime.fromisoformat(body["received_at"])
    assert received - sent > timedelta(minutes=20)  # both times are kept


async def test_resending_a_check_in_returns_the_same_record(api, db) -> None:
    scene = await make_scene(db)
    first = await check_in(api, scene, minutes_ago=5)
    again = await api.post(
        f"/bookings/{scene.id}/checkin",
        json=gps(minutes_ago=5, client_timestamp=first.json()["client_timestamp"]),
        headers=auth(scene.caregiver_user),
    )
    assert again.json()["id"] == first.json()["id"]
    assert await db.scalar(select(func.count(CheckIn.id))) == 1


async def test_check_in_with_a_future_device_time_is_rejected(api, db) -> None:
    scene = await make_scene(db)
    response = await check_in(api, scene, minutes_ago=-30)
    assert response.status_code == 422


async def test_check_in_before_the_window_opens_is_rejected(api, db) -> None:
    scene = await make_scene(db, windows=[(180, 300)])  # starts in 3 hours; opens in 2
    response = await check_in(api, scene)
    assert response.status_code == 422
    assert code(response) == "no_shift_in_window"


async def test_check_in_needs_a_paid_booking(api, db) -> None:
    scene = await make_scene(db, status=BookingStatus.ACCEPTED)
    response = await check_in(api, scene)
    assert response.status_code == 409
    assert code(response) == "booking_invalid_status"


async def test_only_the_booked_caregiver_can_check_in(api, db) -> None:
    scene = await make_scene(db)
    body = gps()
    patient = await api.post(
        f"/bookings/{scene.id}/checkin", json=body, headers=auth(scene.patient_user)
    )
    assert patient.status_code == 403
    other = await make_user(db, UserRole.CAREGIVER)
    db.add(Caregiver(user_id=other.id, tier=CaregiverTier.COMPANION))
    await db.commit()
    stranger = await api.post(f"/bookings/{scene.id}/checkin", json=body, headers=auth(other))
    assert stranger.status_code == 404


async def test_check_in_needs_a_login(api, db) -> None:
    scene = await make_scene(db)
    response = await api.post(f"/bookings/{scene.id}/checkin", json=gps())
    assert response.status_code == 401


# ----------------------------------------------------------------- check-out
async def test_check_out_completes_the_shift_and_the_booking(api, db) -> None:
    scene = await make_scene(db)
    await check_in(api, scene, minutes_ago=20)
    response = await check_out(api, scene, minutes_ago=2)
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "check_out"
    assert await booking_status(api, scene) == "completed"
    shift = await db.scalar(
        select(Shift).where(Shift.id == scene.shifts[0].id).execution_options(populate_existing=True)
    )
    assert shift.status is ShiftStatus.COMPLETED
    assert shift.actual_end is not None
    assert "booking.check_out" in await audit_actions(db)


async def test_check_out_needs_a_check_in_first(api, db) -> None:
    scene = await make_scene(db)
    response = await check_out(api, scene)
    assert response.status_code == 422
    assert code(response) == "no_shift_in_progress"


async def test_resending_a_check_out_returns_the_same_record(api, db) -> None:
    scene = await make_scene(db)
    await check_in(api, scene, minutes_ago=20)
    first = await check_out(api, scene, minutes_ago=3)
    again = await api.post(
        f"/bookings/{scene.id}/checkout",
        json=gps(client_timestamp=first.json()["client_timestamp"]),
        headers=auth(scene.caregiver_user),
    )
    assert again.status_code == 201
    assert again.json()["id"] == first.json()["id"]


async def test_check_out_cannot_come_before_the_check_in(api, db) -> None:
    scene = await make_scene(db)
    await check_in(api, scene, minutes_ago=10)
    response = await check_out(api, scene, minutes_ago=15)
    assert response.status_code == 422


async def test_each_shift_of_a_multi_shift_booking_is_checked_in_by_time(api, db) -> None:
    # Shift 1 ended 3 hours ago; shift 2 starts in 30 minutes (check-in opens in -30).
    scene = await make_scene(db, windows=[(-240, -180), (30, 180)])
    first_in = await check_in(api, scene, minutes_ago=210)
    assert first_in.json()["shift_id"] == str(scene.shifts[0].id)
    await check_out(api, scene, minutes_ago=190)
    assert await booking_status(api, scene) == "in_progress"  # shift 2 is still ahead
    second_in = await check_in(api, scene, minutes_ago=0)
    assert second_in.json()["shift_id"] == str(scene.shifts[1].id)


# -------------------------------------------------------------------- review
async def test_family_rates_the_caregiver_after_check_out(api, db) -> None:
    scene = await completed_scene(api, db)
    response = await api.post(
        f"/bookings/{scene.id}/review",
        json={"rating": 5, "comment": "Kind and on time"},
        headers=auth(scene.patient_user),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["direction"] == "family_to_caregiver"
    assert body["reviewee_id"] == str(scene.caregiver_user.id)
    assert body["shift_id"] == str(scene.shifts[0].id)
    assert "review.create" in await audit_actions(db)


async def test_caregiver_rates_the_family(api, db) -> None:
    scene = await completed_scene(api, db)
    response = await api.post(
        f"/bookings/{scene.id}/review", json={"rating": 4}, headers=auth(scene.caregiver_user)
    )
    assert response.status_code == 201
    assert response.json()["direction"] == "caregiver_to_family"
    assert response.json()["reviewee_id"] == str(scene.patient_user.id)
    # Only ratings of the caregiver feed their average.
    assert await rating_summary(db, scene.caregiver.id) == (None, 0)


async def test_rating_updates_the_caregivers_average(api, db) -> None:
    first = await completed_scene(api, db)
    await api.post(
        f"/bookings/{first.id}/review", json={"rating": 5}, headers=auth(first.patient_user)
    )
    assert await rating_summary(db, first.caregiver.id) == (5, 1)

    second = await make_scene(db, caregiver=(first.caregiver_user, first.caregiver))
    await check_in(api, second, minutes_ago=20)
    await check_out(api, second, minutes_ago=2)
    await api.post(
        f"/bookings/{second.id}/review", json={"rating": 4}, headers=auth(second.patient_user)
    )
    average, count = await rating_summary(db, first.caregiver.id)
    assert (float(average), count) == (4.5, 2)


async def test_the_shift_route_updates_the_average_too(api, db) -> None:
    scene = await completed_scene(api, db)
    response = await api.post(
        f"/bookings/{scene.id}/shifts/{scene.shifts[0].id}/reviews",
        json={"rating": 3},
        headers=auth(scene.patient_user),
    )
    assert response.status_code == 201
    average, count = await rating_summary(db, scene.caregiver.id)
    assert (float(average), count) == (3.0, 1)


async def test_cannot_review_before_check_out(api, db) -> None:
    scene = await make_scene(db)
    await check_in(api, scene)
    response = await api.post(
        f"/bookings/{scene.id}/review", json={"rating": 5}, headers=auth(scene.patient_user)
    )
    assert response.status_code == 422


async def test_cannot_review_twice(api, db) -> None:
    scene = await completed_scene(api, db)
    url = f"/bookings/{scene.id}/review"
    await api.post(url, json={"rating": 5}, headers=auth(scene.patient_user))
    again = await api.post(url, json={"rating": 1}, headers=auth(scene.patient_user))
    assert again.status_code == 409
    assert await rating_summary(db, scene.caregiver.id) == (5, 1)
    assert await db.scalar(select(func.count(Review.id))) == 1


async def test_review_can_name_the_shift(api, db) -> None:
    scene = await completed_scene(api, db)
    response = await api.post(
        f"/bookings/{scene.id}/review",
        json={"rating": 4, "shift_id": str(scene.shifts[0].id)},
        headers=auth(scene.patient_user),
    )
    assert response.status_code == 201


@pytest.mark.parametrize("rating", [0, 6, -1])
async def test_rating_must_be_between_one_and_five(api, db, rating) -> None:
    scene = await completed_scene(api, db)
    response = await api.post(
        f"/bookings/{scene.id}/review", json={"rating": rating}, headers=auth(scene.patient_user)
    )
    assert response.status_code == 422


async def test_ops_cannot_review(api, db) -> None:
    scene = await completed_scene(api, db)
    ops = await make_user(db, UserRole.OPS)
    response = await api.post(
        f"/bookings/{scene.id}/review", json={"rating": 5}, headers=auth(ops)
    )
    assert response.status_code == 403


async def test_a_stranger_cannot_review(api, db) -> None:
    scene = await completed_scene(api, db)
    stranger = await make_user(db, UserRole.PATIENT)
    response = await api.post(
        f"/bookings/{scene.id}/review", json={"rating": 5}, headers=auth(stranger)
    )
    assert response.status_code == 404