"""Tests: bookings core (PRD BK-1 to BK-4, TS-3, TS-4; R1 to R7 in BOOKINGS_LOG.md).

Covers the routes the payments and Day 12 tests don't drive:
- create, list, read, accept, decline, cancel (pricing, shift split, clashes, the lock);
- guardian approval for a minor (TS-3) and the overnight rule (TS-4);
- the shift routes: list, GPS check-in and check-out, care notes;
- the reviews list, and ops housekeeping (expiry, missed shifts, no-shows).

Money paths (hold, release, refund, disputes) are in test_payments.py; booking-level
check-in, check-out and review are in test_check_in_and_reviews.py.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.permissions import UserRole
from app.domains.auth.models import User
from app.domains.bookings import service
from app.domains.bookings.models import (
    ApprovalDecision,
    Booking,
    BookingStatus,
    BookingType,
    CareNote,
    GuardianApproval,
    Shift,
    ShiftStatus,
)
from app.domains.bookings.notifications import Notifier
from app.domains.bookings.router import get_notifier
from app.domains.caregivers.models import Caregiver, CaregiverStatus
from app.domains.patients.models import Guardian, Patient
from app.domains.patients.schemas import GeoPoint
from app.integrations.sms import MockSMSClient
from app.main import app
from app.tests.crisis_factories import (
    ENUGU,
    add_family_link,
    add_guardian,
    auth,
    make_patient,
    make_user,
)
from app.tests.payments_fixtures import (  # noqa: F401
    RATE,
    complete_booking,
    encryption_key_fixture,
    make_booking,
    make_caregiver,
)

LAGOS = service.LAGOS
FAR = (6.4513, 7.4988)  # about 1.1 km north of ENUGU: outside the 200 m geofence
NEARBY = (6.4414, 7.4989)  # about 15 m away


# ================================================================ helpers
def slot(days: int = 2, hour: int = 9, hours: float = 3) -> tuple[datetime, datetime]:
    """A Lagos-time window `days` from today, starting at `hour`."""
    day = (datetime.now(LAGOS) + timedelta(days=days)).date()
    start = datetime.combine(day, time(hour), LAGOS)
    return start, start + timedelta(hours=hours)


def booking_body(
    patient_id: uuid.UUID,
    caregiver_id: uuid.UUID,
    *,
    when: tuple[datetime, datetime] | None = None,
    booking_type: str = "hourly",
    **extra,
) -> dict:
    starts, ends = when or slot()
    return {
        "patient_id": str(patient_id),
        "caregiver_id": str(caregiver_id),
        "booking_type": booking_type,
        "starts_at": starts.isoformat(),
        "ends_at": ends.isoformat(),
        "service_location": {"latitude": ENUGU[0], "longitude": ENUGU[1]},
        "service_address": "12 Ogui Road, Enugu",
        **extra,
    }


def years_ago(years: int) -> date:
    day = datetime.now(UTC).date() - timedelta(days=3)
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def fix(lat_lng: tuple[float, float], at: datetime | None = None, **extra) -> dict:
    """A check-in or check-out body (kind added by the caller)."""
    return {
        "location": {"latitude": lat_lng[0], "longitude": lat_lng[1]},
        "client_timestamp": (at or datetime.now(UTC)).isoformat(),
        "accuracy_m": 8,
        **extra,
    }


async def actions(db: AsyncSession, action: str) -> list[AuditLog]:
    return list(
        await db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
    )


async def booking_row(db: AsyncSession, booking_id) -> Booking:
    row = await db.get(Booking, uuid.UUID(str(booking_id)), populate_existing=True)
    assert row is not None
    return row


async def shifts_of(db: AsyncSession, booking_id) -> list[Shift]:
    return list(
        await db.scalars(
            select(Shift)
            .where(Shift.booking_id == uuid.UUID(str(booking_id)))
            .order_by(Shift.scheduled_start)
            .execution_options(populate_existing=True)
        )
    )


def texts_to(sms: MockSMSClient, phone: str) -> list[str]:
    return [m.body for m in sms.outbox if m.to == phone]


@pytest.fixture(name="notices")
def notices_fixture(sms: MockSMSClient) -> Iterator[MockSMSClient]:
    """Booking notices go to the test's SMS outbox."""
    app.dependency_overrides[get_notifier] = lambda: Notifier(sms)
    yield sms
    app.dependency_overrides.pop(get_notifier, None)


class Cast:
    """An adult patient with a login, two approved caregivers, ops, family and a stranger."""

    patient_user: User
    patient: Patient
    carer_user: User
    carer: Caregiver
    other_carer_user: User
    other_carer: Caregiver
    ops: User
    family: User  # linked, nothing shared
    close_family: User  # linked, location and health data shared
    stranger: User
    sms: MockSMSClient

    @property
    def me(self) -> dict[str, str]:
        return auth(self.patient_user)

    @property
    def cg(self) -> dict[str, str]:
        return auth(self.carer_user)

    def body(self, **kwargs) -> dict:
        return booking_body(self.patient.id, self.carer.id, **kwargs)


@pytest.fixture(name="cast")
async def cast_fixture(db: AsyncSession, notices: MockSMSClient) -> Cast:
    c = Cast()
    c.sms = notices
    c.patient_user = await make_user(db, UserRole.PATIENT, "Chidi Okafor")
    c.patient = await make_patient(db, user=c.patient_user, created_by=c.patient_user)
    c.carer_user, c.carer = await make_caregiver(db)
    c.other_carer_user, c.other_carer = await make_caregiver(db, rate=150_000)
    c.ops = await make_user(db, UserRole.OPS, "Ops Desk")
    c.family = await make_user(db, UserRole.FAMILY_ABROAD, "Cousin")
    await add_family_link(db, c.patient, c.family, {})
    c.close_family = await make_user(db, UserRole.FAMILY_ABROAD, "Ada in London")
    await add_family_link(db, c.patient, c.close_family, {"location": True, "health_data": True})
    c.stranger = await make_user(db, UserRole.PATIENT, "Stranger")
    return c


async def book(api: httpx.AsyncClient, cast: Cast, headers=None, **kwargs) -> dict:
    r = await api.post("/bookings", json=cast.body(**kwargs), headers=headers or cast.me)
    assert r.status_code == 201, r.text
    return r.json()


class Minor:
    """A child with no login. `booker` is a guardian who may not approve caregivers;
    `approver` is one who may."""

    child: Patient
    booker_user: User
    booker: Guardian
    approver_user: User
    approver: Guardian


@pytest.fixture(name="minor")
async def minor_fixture(db: AsyncSession, cast: Cast) -> Minor:
    m = Minor()
    m.booker_user = await make_user(db, UserRole.GUARDIAN, "Papa Kelechi")
    m.child = Patient(full_name="Kelechi", date_of_birth=years_ago(9), created_by=m.booker_user.id)
    db.add(m.child)
    await db.commit()
    m.booker = await add_guardian(db, m.child, m.booker_user, primary=True)
    m.booker.can_approve_caregivers = False
    m.approver_user = await make_user(db, UserRole.GUARDIAN, "Mama Kelechi")
    m.approver = await add_guardian(db, m.child, m.approver_user)
    await db.commit()
    return m


def minor_body(cast: Cast, minor: Minor, **kwargs) -> dict:
    return booking_body(minor.child.id, cast.carer.id, **kwargs)


# ================================================================ pure rules
def test_price_rounds_up_to_the_next_kobo() -> None:
    start = datetime(2026, 11, 2, 8, tzinfo=UTC)
    assert service.price_minor(200_000, start, start + timedelta(hours=3)) == 600_000
    assert service.price_minor(100_001, start, start + timedelta(minutes=90)) == 150_002
    assert service.price_minor(1, start, start + timedelta(seconds=1)) == 1
    assert service.price_minor(0, start, start + timedelta(hours=5)) == 0


@pytest.mark.parametrize(
    ("booking_type", "hours", "expected"),
    [
        (BookingType.HOURLY, 30, [30]),
        (BookingType.OVERNIGHT, 10, [10]),
        (BookingType.DAILY, 24, [24]),
        (BookingType.DAILY, 60, [24, 24, 12]),
        (BookingType.LIVE_IN, 72, [24, 24, 24]),
        (BookingType.LIVE_IN, 1, [1]),
    ],
)
def test_shift_split(booking_type, hours, expected) -> None:
    start = datetime(2026, 11, 2, 8, tzinfo=UTC)
    shifts = list(service.split_into_shifts(booking_type, start, start + timedelta(hours=hours)))
    assert [(e - s) / timedelta(hours=1) for s, e in shifts] == expected
    assert shifts[0][0] == start and shifts[-1][1] == start + timedelta(hours=hours)
    assert all(a[1] == b[0] for a, b in itertools.pairwise(shifts))  # no gaps


@pytest.mark.parametrize(
    ("start_hour", "hours", "night"),
    [
        (9, 3, False),  # 09:00 to 12:00
        (6, 16, False),  # 06:00 to 22:00 exactly: touches neither edge
        (21, 1.5, True),  # 21:00 to 22:30
        (5, 2, True),  # 05:00 to 07:00
        (23, 1, True),  # 23:00 to 00:00
        (14, 24, True),  # spans a night
    ],
)
def test_covers_night_in_lagos_time(start_hour, hours, night) -> None:
    start = datetime.combine(date(2026, 11, 2), time(start_hour), LAGOS)
    assert service.covers_night(start, start + timedelta(hours=hours)) is night


def test_covers_night_uses_lagos_not_utc() -> None:
    # 21:30 to 22:30 UTC is 22:30 to 23:30 in Lagos.
    start = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)
    assert service.covers_night(start, start + timedelta(hours=1))
    # 20:00 to 21:00 UTC is 21:00 to 22:00 in Lagos: ends exactly as night starts.
    start = datetime(2026, 11, 2, 20, tzinfo=UTC)
    assert not service.covers_night(start, start + timedelta(hours=1))


def test_distance() -> None:
    here = GeoPoint(latitude=ENUGU[0], longitude=ENUGU[1])
    assert service.distance_m(here, here) == 0
    assert 1100 <= service.distance_m(here, GeoPoint(latitude=FAR[0], longitude=FAR[1])) <= 1120
    assert service.distance_m(here, GeoPoint(latitude=NEARBY[0], longitude=NEARBY[1])) < 20


# ================================================================ create
async def test_adult_patient_books_an_hourly_caregiver(api, db, cast) -> None:
    starts, ends = slot(hours=3)
    r = await api.post("/bookings", json=cast.body(when=(starts, ends)), headers=cast.me)
    assert r.status_code == 201, r.text
    b = r.json()
    assert (b["status"], b["booking_type"], b["access"]) == ("requested", "hourly", "patient_side")
    assert (b["rate_minor"], b["total_minor"], b["currency"]) == (RATE, RATE * 3, "NGN")
    assert b["booked_by"] == str(cast.patient_user.id)
    assert b["service_location"] == {"latitude": ENUGU[0], "longitude": ENUGU[1]}
    assert datetime.fromisoformat(b["starts_at"]) == starts
    assert b["guardian_present"] is False

    [shift] = await shifts_of(db, b["id"])
    assert (shift.scheduled_start, shift.scheduled_end, shift.status) == (
        starts,
        ends,
        ShiftStatus.SCHEDULED,
    )
    [entry] = await actions(db, "booking.create")
    assert entry.meta["status"] == "requested" and entry.actor_id == cast.patient_user.id
    # The caregiver is told, without names or addresses.
    [notice] = texts_to(cast.sms, cast.carer_user.phone)
    assert "new hourly booking request" in notice
    assert "Chidi" not in notice and "Ogui" not in notice
    # An adult's own booking needs no guardian.
    assert await db.scalar(select(GuardianApproval.id)) is None


async def test_rate_is_fixed_at_booking_time(api, db, cast) -> None:
    b = await book(api, cast)
    cast.carer.hourly_rate_minor = 999_999
    await db.commit()
    again = (await api.get(f"/bookings/{b['id']}", headers=cast.me)).json()
    assert (again["rate_minor"], again["total_minor"]) == (RATE, RATE * 3)


async def test_price_rounds_up_through_the_api(api, db, cast) -> None:
    cast.carer.hourly_rate_minor = 100_001
    await db.commit()
    b = await book(api, cast, when=slot(hours=1.5))
    assert b["total_minor"] == 150_002


@pytest.mark.parametrize(
    ("booking_type", "hours", "shifts"),
    [("daily", 60, 3), ("live_in", 48, 2), ("overnight", 8, 1), ("hourly", 1, 1)],
)
async def test_bookings_are_split_into_shifts(api, db, cast, booking_type, hours, shifts) -> None:
    when = slot(hour=22 if booking_type == "overnight" else 9, hours=hours)
    b = await book(api, cast, booking_type=booking_type, when=when)
    rows = await shifts_of(db, b["id"])
    assert len(rows) == shifts
    assert rows[0].scheduled_start == when[0] and rows[-1].scheduled_end == when[1]
    assert b["total_minor"] == RATE * hours
    listed = (await api.get(f"/bookings/{b['id']}/shifts", headers=cast.me)).json()
    assert [s["id"] for s in listed] == [str(s.id) for s in rows]


async def test_longest_booking_is_31_days(api, db, cast) -> None:
    b = await book(api, cast, booking_type="live_in", when=slot(hours=31 * 24))
    assert len(await shifts_of(db, b["id"])) == 31


@pytest.mark.parametrize(
    "change",
    [
        {"ends_at": "start"},  # zero length
        {"hours": 0.5},  # shorter than an hour
        {"hours": -2},  # ends before it starts
        {"hours": 31 * 24 + 1},  # longer than 31 days
        {"naive": True},  # no timezone
        {"extra": {"total_minor": 1}},  # client can't set the price
        {"extra": {"status": "accepted"}},
        {"extra": {"booking_type": "weekly"}},
        {"extra": {"service_address": "here"}},  # under 5 characters
        {"extra": {"service_location": {"latitude": 100, "longitude": 7}}},
        {"drop": "service_location"},
        {"drop": "caregiver_id"},
    ],
)
async def test_create_rejects_bad_input(api, db, cast, change) -> None:
    starts, ends = slot()
    if "hours" in change:
        ends = starts + timedelta(hours=change["hours"])
    if change.get("ends_at") == "start":
        ends = starts
    body = cast.body(when=(starts, ends))
    if change.get("naive"):
        body["starts_at"] = starts.replace(tzinfo=None).isoformat()
        body["ends_at"] = ends.replace(tzinfo=None).isoformat()
    body.update(change.get("extra", {}))
    if "drop" in change:
        del body[change["drop"]]
    r = await api.post("/bookings", json=body, headers=cast.me)
    assert r.status_code == 422, r.text
    assert await db.scalar(select(Booking.id)) is None


async def test_booking_must_start_in_the_future(api, db, cast) -> None:
    starts = datetime.now(UTC) - timedelta(minutes=1)
    r = await api.post(
        "/bookings", json=cast.body(when=(starts, starts + timedelta(hours=2))), headers=cast.me
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "booking_rule_violation"


async def test_caregiver_must_exist_be_approved_and_have_a_rate(api, db, cast) -> None:
    r = await api.post(
        "/bookings",
        json=booking_body(cast.patient.id, uuid.uuid4()),
        headers=cast.me,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "caregiver_not_found"

    for status in (CaregiverStatus.VERIFIED, CaregiverStatus.SUSPENDED, CaregiverStatus.APPLIED):
        cast.carer.status = status
        await db.commit()
        r = await api.post("/bookings", json=cast.body(), headers=cast.me)
        assert r.status_code == 422, status
        assert "approved" in r.json()["error"]["message"]

    cast.carer.status = CaregiverStatus.APPROVED
    cast.carer.hourly_rate_minor = None
    await db.commit()
    r = await api.post("/bookings", json=cast.body(), headers=cast.me)
    assert r.status_code == 422
    assert "rate" in r.json()["error"]["message"]
    assert await db.scalar(select(Booking.id)) is None


async def test_caregiver_cannot_book_themselves(api, db, cast) -> None:
    own = await make_patient(db, user=cast.carer_user, created_by=cast.carer_user)
    r = await api.post(
        "/bookings", json=booking_body(own.id, cast.carer.id), headers=auth(cast.carer_user)
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "booking_rule_violation"


async def test_only_the_patient_or_a_guardian_books(api, db, cast) -> None:
    for user in (cast.family, cast.close_family):
        r = await api.post("/bookings", json=cast.body(), headers=auth(user))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "booking_forbidden"
    # Ops, caregivers and strangers don't learn the patient exists.
    for user in (cast.ops, cast.other_carer_user, cast.stranger):
        r = await api.post("/bookings", json=cast.body(), headers=auth(user))
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "patient_not_found"
    r = await api.post("/bookings", json=booking_body(uuid.uuid4(), cast.carer.id), headers=cast.me)
    assert r.status_code == 404
    assert (await api.post("/bookings", json=cast.body())).status_code == 401


async def test_a_guardian_of_an_adult_books_without_approval_rows(api, db, cast) -> None:
    helper = await make_user(db, UserRole.GUARDIAN, "Sister")
    await add_guardian(db, cast.patient, helper)
    b = await book(api, cast, headers=auth(helper))
    assert b["status"] == "requested"
    assert b["booked_by"] == str(helper.id)
    assert await db.scalar(select(GuardianApproval.id)) is None


# ================================================================ clashes
async def test_an_accepted_booking_blocks_the_caregivers_time(api, db, cast) -> None:
    starts, ends = slot(hours=4)
    first = await book(api, cast, when=(starts, ends))
    assert (await api.post(f"/bookings/{first['id']}/accept", headers=cast.cg)).status_code == 200

    overlap = (starts + timedelta(hours=1), ends + timedelta(hours=1))
    r = await api.post("/bookings", json=cast.body(when=overlap), headers=cast.me)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "caregiver_unavailable"
    inside = (starts + timedelta(hours=1), starts + timedelta(hours=2))
    assert (
        await api.post("/bookings", json=cast.body(when=inside), headers=cast.me)
    ).status_code == 409

    # Back to back is fine, and another caregiver is free.
    await book(api, cast, when=(ends, ends + timedelta(hours=2)))
    other = booking_body(cast.patient.id, cast.other_carer.id, when=overlap)
    assert (await api.post("/bookings", json=other, headers=cast.me)).status_code == 201


async def test_requests_and_closed_bookings_dont_block(api, db, cast) -> None:
    when = slot()
    await book(api, cast, when=when)  # requested
    for status in (BookingStatus.CANCELLED, BookingStatus.DECLINED, BookingStatus.COMPLETED):
        b = await make_booking(
            db,
            patient_id=cast.patient.id,
            caregiver=cast.carer,
            booked_by=cast.patient_user.id,
            status=status,
            starts_in=when[0] - datetime.now(UTC),
        )
        assert b.status is status
    await book(api, cast, when=when)
    assert await db.scalar(text("SELECT count(*) FROM bookings WHERE status = 'requested'")) == 2


@pytest.mark.parametrize("status", [BookingStatus.PAID, BookingStatus.IN_PROGRESS])
async def test_paid_and_running_bookings_block_too(api, db, cast, status) -> None:
    existing = await make_booking(
        db,
        patient_id=cast.patient.id,
        caregiver=cast.carer,
        booked_by=cast.patient_user.id,
        status=status,
        starts_in=timedelta(days=2),
    )
    when = (existing.starts_at + timedelta(minutes=30), existing.ends_at + timedelta(hours=1))
    r = await api.post("/bookings", json=cast.body(when=when), headers=cast.me)
    assert r.status_code == 409


async def test_accept_rechecks_clashes(api, db, cast) -> None:
    b = await book(api, cast)
    starts = datetime.fromisoformat(b["starts_at"])
    await make_booking(
        db,
        patient_id=cast.patient.id,
        caregiver=cast.carer,
        booked_by=cast.patient_user.id,
        status=BookingStatus.ACCEPTED,
        starts_in=starts - datetime.now(UTC) + timedelta(hours=1),
    )
    r = await api.post(f"/bookings/{b['id']}/accept", headers=cast.cg)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "caregiver_unavailable"
    assert (await booking_row(db, b["id"])).status is BookingStatus.REQUESTED


async def test_two_overlapping_requests_accepted_at_once_only_one_wins(api, db, cast) -> None:
    """The advisory lock serialises accepts for one caregiver, so both can't pass."""
    when = slot(hours=3)
    a = await book(api, cast, when=when)
    later = (when[0] + timedelta(hours=1), when[1] + timedelta(hours=1))
    b = await book(api, cast, when=later)
    results = await asyncio.gather(
        api.post(f"/bookings/{a['id']}/accept", headers=cast.cg),
        api.post(f"/bookings/{b['id']}/accept", headers=cast.cg),
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    statuses = sorted(
        [(await booking_row(db, a["id"])).status, (await booking_row(db, b["id"])).status]
    )
    assert statuses == [BookingStatus.ACCEPTED, BookingStatus.REQUESTED]


async def test_cancelling_frees_the_caregivers_time(api, db, cast) -> None:
    when = slot()
    first = await book(api, cast, when=when)
    await api.post(f"/bookings/{first['id']}/accept", headers=cast.cg)
    assert (
        await api.post("/bookings", json=cast.body(when=when), headers=cast.me)
    ).status_code == 409
    await api.post(f"/bookings/{first['id']}/cancel", headers=cast.me)
    second = await book(api, cast, when=when)
    assert (await api.post(f"/bookings/{second['id']}/accept", headers=cast.cg)).status_code == 200


# ================================================================ TS-3 guardian approval
async def test_minors_booking_waits_for_an_approving_guardian(api, db, cast, minor) -> None:
    r = await api.post("/bookings", json=minor_body(cast, minor), headers=auth(minor.booker_user))
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "pending_guardian"
    # Only guardians who may approve are asked; the caregiver hears nothing yet.
    assert texts_to(cast.sms, minor.approver_user.phone)
    assert "approval" in texts_to(cast.sms, minor.approver_user.phone)[0]
    assert texts_to(cast.sms, minor.booker_user.phone) == []
    assert texts_to(cast.sms, cast.carer_user.phone) == []
    # The caregiver can't accept it yet.
    r = await api.post(f"/bookings/{b['id']}/accept", headers=cast.cg)
    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"status": "pending_guardian", "allowed": ["requested"]}

    url = f"/bookings/{b['id']}/guardian-approvals"
    assert (await api.get(url, headers=auth(minor.approver_user))).json() == []
    r = await api.post(url, json={"decision": "approved"}, headers=auth(minor.approver_user))
    assert r.status_code == 201, r.text
    approval = r.json()
    assert (approval["decision"], approval["guardian_id"]) == ("approved", str(minor.approver.id))
    assert (await booking_row(db, b["id"])).status is BookingStatus.REQUESTED
    assert "new hourly booking request" in texts_to(cast.sms, cast.carer_user.phone)[0]
    [entry] = await actions(db, "booking.guardian_approve")
    assert entry.actor_id == minor.approver_user.id

    # Both guardians can read the decision; the caregiver can't.
    for user in (minor.booker_user, minor.approver_user):
        listed = (await api.get(url, headers=auth(user))).json()
        assert [a["id"] for a in listed] == [approval["id"]]
    r = await api.get(url, headers=cast.cg)
    assert r.status_code == 403

    # Deciding again is refused, and now the caregiver can accept.
    r = await api.post(url, json={"decision": "rejected"}, headers=auth(minor.approver_user))
    assert r.status_code == 409
    assert (await api.post(f"/bookings/{b['id']}/accept", headers=cast.cg)).status_code == 200


async def test_guardian_rejection_declines_and_cancels_shifts(api, db, cast, minor) -> None:
    b = (
        await api.post(
            "/bookings",
            json=minor_body(
                cast, minor, booking_type="daily", when=slot(hours=48), guardian_present=True
            ),
            headers=auth(minor.booker_user),
        )
    ).json()
    assert b["status"] == "pending_guardian"
    r = await api.post(
        f"/bookings/{b['id']}/guardian-approvals",
        json={"decision": "rejected", "reason": "  We want a nurse  "},
        headers=auth(minor.approver_user),
    )
    assert r.status_code == 201, r.text
    assert (r.json()["decision"], r.json()["reason"]) == ("rejected", "We want a nurse")
    assert (await booking_row(db, b["id"])).status is BookingStatus.DECLINED
    assert {s.status for s in await shifts_of(db, b["id"])} == {ShiftStatus.CANCELLED}
    assert "did not approve" in texts_to(cast.sms, minor.booker_user.phone)[-1]
    assert texts_to(cast.sms, cast.carer_user.phone) == []
    assert len(await actions(db, "booking.guardian_reject")) == 1


async def test_approving_guardian_who_books_approves_it_too(api, db, cast, minor) -> None:
    r = await api.post("/bookings", json=minor_body(cast, minor), headers=auth(minor.approver_user))
    assert r.status_code == 201
    assert r.json()["status"] == "requested"
    [approval] = list(await db.scalars(select(GuardianApproval)))
    assert (approval.guardian_id, approval.decision) == (
        minor.approver.id,
        ApprovalDecision.APPROVED,
    )
    assert texts_to(cast.sms, cast.carer_user.phone)  # straight to the caregiver


async def test_adult_without_a_login_also_needs_a_guardian(api, db, cast) -> None:
    son = await make_user(db, UserRole.GUARDIAN, "Son abroad")
    grandma = await make_patient(db, user=None, created_by=son, name="Grandma Nneka")
    g = await add_guardian(db, grandma, son, primary=True)
    g.can_approve_caregivers = False
    await db.commit()
    r = await api.post("/bookings", json=booking_body(grandma.id, cast.carer.id), headers=auth(son))
    assert r.status_code == 201
    assert r.json()["status"] == "pending_guardian"


async def test_who_may_decide_a_guardian_approval(api, db, cast, minor) -> None:
    b = (
        await api.post("/bookings", json=minor_body(cast, minor), headers=auth(minor.booker_user))
    ).json()
    url = f"/bookings/{b['id']}/guardian-approvals"
    decision = {"decision": "approved"}
    # A guardian who may not approve, the caregiver, and ops can see the booking but not decide.
    for user in (minor.booker_user, cast.carer_user, cast.ops):
        r = await api.post(url, json=decision, headers=auth(user))
        assert r.status_code == 403, user.full_name
        assert r.json()["error"]["code"] == "booking_forbidden"
    # Family linked to the child can't either.
    aunt = await make_user(db, UserRole.FAMILY_ABROAD, "Aunt")
    await add_family_link(db, minor.child, aunt, {"health_data": True})
    assert (await api.post(url, json=decision, headers=auth(aunt))).status_code == 403
    # Strangers and the other caregiver don't learn it exists.
    for user in (cast.stranger, cast.other_carer_user):
        assert (await api.post(url, json=decision, headers=auth(user))).status_code == 404
        assert (await api.get(url, headers=auth(user))).status_code == 404
    assert (await booking_row(db, b["id"])).status is BookingStatus.PENDING_GUARDIAN


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "maybe"},
        {},
        {"decision": "approved", "reason": ""},
        {"decision": "approved", "reason": "x" * 1001},
        {"decision": "approved", "guardian_id": str(uuid.uuid4())},
    ],
)
async def test_guardian_approval_validation(api, db, cast, minor, payload) -> None:
    b = (
        await api.post("/bookings", json=minor_body(cast, minor), headers=auth(minor.booker_user))
    ).json()
    r = await api.post(
        f"/bookings/{b['id']}/guardian-approvals", json=payload, headers=auth(minor.approver_user)
    )
    assert r.status_code == 422


async def test_adults_booking_has_nothing_to_approve(api, db, cast) -> None:
    helper = await make_user(db, UserRole.GUARDIAN, "Sister")
    await add_guardian(db, cast.patient, helper)
    b = await book(api, cast)
    r = await api.post(
        f"/bookings/{b['id']}/guardian-approvals",
        json={"decision": "approved"},
        headers=auth(helper),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "booking_invalid_status"


# ================================================================ TS-4 overnight rule
async def test_minor_overnight_needs_a_guardian_in_the_first_month(api, db, cast, minor) -> None:
    h = auth(minor.approver_user)
    night = slot(hour=22, hours=8)
    r = await api.post(
        "/bookings", json=minor_body(cast, minor, booking_type="overnight", when=night), headers=h
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "guardian_presence_required"
    assert r.json()["error"]["details"] == {"first_completed_booking": None}
    # Calling it hourly doesn't get around it.
    evening = slot(hour=20, hours=3)
    r = await api.post("/bookings", json=minor_body(cast, minor, when=evening), headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "guardian_presence_required"
    # Live-in always counts, even starting in the morning.
    r = await api.post(
        "/bookings",
        json=minor_body(cast, minor, booking_type="live_in", when=slot(hours=10)),
        headers=h,
    )
    assert r.status_code == 422
    assert await db.scalar(select(Booking.id)) is None

    # With a guardian present it's allowed, and daytime care never needed one.
    r = await api.post(
        "/bookings",
        json=minor_body(cast, minor, booking_type="overnight", when=night, guardian_present=True),
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["guardian_present"] is True
    r = await api.post(
        "/bookings", json=minor_body(cast, minor, when=slot(days=3, hours=3)), headers=h
    )
    assert r.status_code == 201


async def test_overnight_rule_counts_from_the_first_completed_booking(api, db, cast, minor) -> None:
    h = auth(minor.approver_user)
    night = slot(hour=22, hours=8)
    body = minor_body(cast, minor, booking_type="overnight", when=night)

    recent = await make_booking(
        db,
        patient_id=minor.child.id,
        caregiver=cast.carer,
        booked_by=minor.approver_user.id,
        status=BookingStatus.COMPLETED,
        starts_in=-timedelta(days=10),
    )
    r = await api.post("/bookings", json=body, headers=h)
    assert r.status_code == 422
    first = datetime.fromisoformat(r.json()["error"]["details"]["first_completed_booking"])
    assert first == recent.starts_at

    # Another caregiver's history doesn't count for this one.
    await make_booking(
        db,
        patient_id=minor.child.id,
        caregiver=cast.other_carer,
        booked_by=minor.approver_user.id,
        status=BookingStatus.COMPLETED,
        starts_in=-timedelta(days=60),
    )
    assert (await api.post("/bookings", json=body, headers=h)).status_code == 422

    # A completed booking with this caregiver over a month before is enough.
    await make_booking(
        db,
        patient_id=minor.child.id,
        caregiver=cast.carer,
        booked_by=minor.approver_user.id,
        status=BookingStatus.COMPLETED,
        starts_in=-timedelta(days=40),
    )
    r = await api.post("/bookings", json=body, headers=h)
    assert r.status_code == 201, r.text


async def test_cancelled_history_doesnt_count_for_the_overnight_rule(api, db, cast, minor) -> None:
    await make_booking(
        db,
        patient_id=minor.child.id,
        caregiver=cast.carer,
        booked_by=minor.approver_user.id,
        status=BookingStatus.CANCELLED,
        starts_in=-timedelta(days=90),
    )
    body = minor_body(cast, minor, booking_type="overnight", when=slot(hour=22, hours=8))
    r = await api.post("/bookings", json=body, headers=auth(minor.approver_user))
    assert r.status_code == 422


async def test_adults_overnight_needs_no_guardian(api, db, cast) -> None:
    b = await book(api, cast, booking_type="overnight", when=slot(hour=22, hours=8))
    assert b["status"] == "requested"


# ================================================================ list and read
async def test_list_bookings_by_who_is_asking(api, db, cast) -> None:
    soon = await book(api, cast, when=slot(days=2))
    later = await make_booking(
        db,
        patient_id=cast.patient.id,
        caregiver=cast.other_carer,
        booked_by=cast.patient_user.id,
        status=BookingStatus.ACCEPTED,
        starts_in=timedelta(days=5),
    )
    other_user = await make_user(db, UserRole.PATIENT, "Other")
    other = await make_patient(db, user=other_user, created_by=other_user)
    elsewhere = await make_booking(
        db,
        patient_id=other.id,
        caregiver=cast.carer,
        booked_by=other_user.id,
        status=BookingStatus.REQUESTED,
        starts_in=timedelta(days=4),
    )

    async def ids(user, query: str = "") -> list[str]:
        r = await api.get(f"/bookings{query}", headers=auth(user))
        assert r.status_code == 200, r.text
        return [b["id"] for b in r.json()]

    # Patient side: newest start first, only their own.
    assert await ids(cast.patient_user) == [str(later.id), soon["id"]]
    assert await ids(cast.patient_user, "?status=accepted") == [str(later.id)]
    assert await ids(cast.patient_user, "?status=paid") == []
    assert await ids(cast.patient_user, "?limit=1") == [str(later.id)]
    assert await ids(cast.patient_user, "?limit=1&offset=1") == [soon["id"]]
    # Caregivers see only bookings with them.
    assert await ids(cast.carer_user) == [str(elsewhere.id), soon["id"]]
    assert await ids(cast.other_carer_user) == [str(later.id)]
    # Ops sees everything.
    assert set(await ids(cast.ops)) == {str(later.id), soon["id"], str(elsewhere.id)}
    # Family sees the patient's bookings; a stranger sees none.
    assert await ids(cast.family) == [str(later.id), soon["id"]]
    assert await ids(cast.stranger) == []
    # A caregiver account with no caregiver profile has none.
    bare = await make_user(db, UserRole.CAREGIVER, "Not onboarded")
    assert await ids(bare) == []


async def test_list_access_and_location_per_viewer(api, db, cast) -> None:
    await book(api, cast)
    seen = {}
    for name, user in (
        ("patient", cast.patient_user),
        ("carer", cast.carer_user),
        ("ops", cast.ops),
        ("family", cast.family),
        ("close", cast.close_family),
    ):
        [b] = (await api.get("/bookings", headers=auth(user))).json()
        seen[name] = (
            b["access"],
            b["service_address"] is not None,
            b["service_location"] is not None,
        )
    assert seen == {
        "patient": ("patient_side", True, True),
        "carer": ("caregiver", True, True),
        "ops": ("ops", True, True),
        "family": ("patient_side", False, False),  # location not shared
        "close": ("patient_side", True, True),
    }


@pytest.mark.parametrize("query", ["?status=bogus", "?limit=0", "?limit=101", "?offset=-1"])
async def test_list_rejects_bad_query(api, db, cast, query) -> None:
    assert (await api.get(f"/bookings{query}", headers=cast.me)).status_code == 422


async def test_read_one_booking(api, db, cast) -> None:
    b = await book(api, cast)
    url = f"/bookings/{b['id']}"
    family = (await api.get(url, headers=auth(cast.family))).json()
    assert (family["service_location"], family["service_address"]) == (None, None)
    assert (await api.get(url, headers=auth(cast.ops))).json()["access"] == "ops"
    assert (await api.get(url, headers=cast.cg)).json()["access"] == "caregiver"
    for user in (cast.stranger, cast.other_carer_user):
        r = await api.get(url, headers=auth(user))
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "booking_not_found"
    assert (await api.get(f"/bookings/{uuid.uuid4()}", headers=cast.me)).status_code == 404
    assert (await api.get("/bookings/not-a-uuid", headers=cast.me)).status_code == 422
    assert (await api.get(url)).status_code == 401


# ================================================================ accept, decline, cancel
async def test_caregiver_accepts(api, db, cast) -> None:
    b = await book(api, cast)
    cast.sms.outbox.clear()
    r = await api.post(f"/bookings/{b['id']}/accept", headers=cast.cg)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["access"]) == ("accepted", "caregiver")
    row = await booking_row(db, b["id"])
    assert row.accepted_at is not None
    assert abs(row.accepted_at - datetime.now(UTC)) < timedelta(minutes=1)
    assert "accepted" in texts_to(cast.sms, cast.patient_user.phone)[0]
    [entry] = await actions(db, "booking.accept")
    assert entry.actor_id == cast.carer_user.id

    r = await api.post(f"/bookings/{b['id']}/accept", headers=cast.cg)
    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"status": "accepted", "allowed": ["requested"]}


async def test_only_the_booked_caregiver_accepts_or_declines(api, db, cast) -> None:
    b = await book(api, cast)
    for verb in ("accept", "decline"):
        for user in (cast.patient_user, cast.ops, cast.close_family):
            r = await api.post(f"/bookings/{b['id']}/{verb}", headers=auth(user))
            assert r.status_code == 403, (verb, user.full_name)
            assert r.json()["error"]["code"] == "booking_forbidden"
        r = await api.post(f"/bookings/{b['id']}/{verb}", headers=auth(cast.other_carer_user))
        assert r.status_code == 404
    assert (await booking_row(db, b["id"])).status is BookingStatus.REQUESTED


async def test_cannot_accept_once_the_start_has_passed(api, db, cast) -> None:
    late = await make_booking(
        db,
        patient_id=cast.patient.id,
        caregiver=cast.carer,
        booked_by=cast.patient_user.id,
        status=BookingStatus.REQUESTED,
        starts_in=-timedelta(minutes=5),
    )
    r = await api.post(f"/bookings/{late.id}/accept", headers=cast.cg)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "booking_rule_violation"


async def test_caregiver_declines(api, db, cast) -> None:
    b = await book(api, cast, booking_type="daily", when=slot(hours=48))
    r = await api.post(f"/bookings/{b['id']}/decline", headers=cast.cg)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "declined"
    shifts = (await api.get(f"/bookings/{b['id']}/shifts", headers=cast.me)).json()
    assert [s["status"] for s in shifts] == ["cancelled", "cancelled"]
    assert "declined" in texts_to(cast.sms, cast.patient_user.phone)[-1]
    assert len(await actions(db, "booking.decline")) == 1
    # Declined is final.
    for verb in ("accept", "decline", "cancel"):
        h = cast.me if verb == "cancel" else cast.cg
        assert (await api.post(f"/bookings/{b['id']}/{verb}", headers=h)).status_code == 409


async def test_cannot_decline_after_accepting(api, db, cast) -> None:
    b = await book(api, cast)
    await api.post(f"/bookings/{b['id']}/accept", headers=cast.cg)
    r = await api.post(f"/bookings/{b['id']}/decline", headers=cast.cg)
    assert r.status_code == 409
    assert r.json()["error"]["details"]["status"] == "accepted"


async def test_patient_cancels_a_request(api, db, cast) -> None:
    b = await book(api, cast)
    cast.sms.outbox.clear()
    r = await api.post(f"/bookings/{b['id']}/cancel", headers=cast.me)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert {s.status for s in await shifts_of(db, b["id"])} == {ShiftStatus.CANCELLED}
    assert "cancelled" in texts_to(cast.sms, cast.carer_user.phone)[0]
    assert texts_to(cast.sms, cast.patient_user.phone) == []
    [entry] = await actions(db, "booking.cancel")
    assert entry.meta == {"previous_status": "requested", "by": "patient_side"}


async def test_caregiver_cancels_an_accepted_booking(api, db, cast) -> None:
    b = await book(api, cast)
    await api.post(f"/bookings/{b['id']}/accept", headers=cast.cg)
    cast.sms.outbox.clear()
    r = await api.post(f"/bookings/{b['id']}/cancel", headers=cast.cg)
    assert r.status_code == 200
    assert "cancelled" in texts_to(cast.sms, cast.patient_user.phone)[0]
    assert texts_to(cast.sms, cast.carer_user.phone) == []
    [entry] = await actions(db, "booking.cancel")
    assert entry.meta == {"previous_status": "accepted", "by": "caregiver"}


async def test_cancelling_a_pending_guardian_booking_doesnt_tell_the_caregiver(
    api, db, cast, minor
) -> None:
    b = (
        await api.post("/bookings", json=minor_body(cast, minor), headers=auth(minor.booker_user))
    ).json()
    r = await api.post(f"/bookings/{b['id']}/cancel", headers=auth(minor.booker_user))
    assert r.status_code == 200
    assert texts_to(cast.sms, cast.carer_user.phone) == []


async def test_who_may_cancel(api, db, cast) -> None:
    helper = await make_user(db, UserRole.GUARDIAN, "Sister")
    await add_guardian(db, cast.patient, helper)  # not primary
    b = await book(api, cast)
    url = f"/bookings/{b['id']}/cancel"
    for user in (cast.family, cast.close_family, cast.ops):
        r = await api.post(url, headers=auth(user))
        assert r.status_code == 403, user.full_name
    for user in (cast.stranger, cast.other_carer_user):
        assert (await api.post(url, headers=auth(user))).status_code == 404
    assert (await booking_row(db, b["id"])).status is BookingStatus.REQUESTED
    # Any guardian may cancel.
    assert (await api.post(url, headers=auth(helper))).status_code == 200


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.PAID,
        BookingStatus.IN_PROGRESS,
        BookingStatus.COMPLETED,
        BookingStatus.DISPUTED,
        BookingStatus.CANCELLED,
    ],
)
async def test_cancel_only_before_payment(api, db, cast, status) -> None:
    b = await make_booking(
        db,
        patient_id=cast.patient.id,
        caregiver=cast.carer,
        booked_by=cast.patient_user.id,
        status=status,
    )
    for h in (cast.me, cast.cg):
        r = await api.post(f"/bookings/{b.id}/cancel", headers=h)
        assert r.status_code == 409
        assert r.json()["error"]["details"]["allowed"] == [
            "pending_guardian",
            "requested",
            "accepted",
        ]
    assert (await booking_row(db, b.id)).status is status


async def test_booking_works_without_sms(api, db, cast) -> None:
    """Notices are best effort: with no SMS client a booking still goes through."""
    app.dependency_overrides[get_notifier] = lambda: Notifier(None)
    b = await book(api, cast)
    assert (await api.post(f"/bookings/{b['id']}/accept", headers=cast.cg)).status_code == 200
    assert cast.sms.outbox == []


# ================================================================ shifts and check-ins
async def paid_booking(
    db, cast, *, starts_in=timedelta(minutes=10), hours=3, shifts=1, status=None
):
    return await make_booking(
        db,
        patient_id=cast.patient.id,
        caregiver=cast.carer,
        booked_by=cast.patient_user.id,
        status=status or BookingStatus.PAID,
        starts_in=starts_in,
        hours=hours,
        shifts=shifts,
    )


async def test_shift_list_access(api, db, cast) -> None:
    b = await paid_booking(db, cast, shifts=3, starts_in=timedelta(days=1))
    url = f"/bookings/{b.id}/shifts"
    for user in (cast.patient_user, cast.carer_user, cast.ops, cast.family):
        r = await api.get(url, headers=auth(user))
        assert r.status_code == 200, user.full_name
        rows = r.json()
        assert len(rows) == 3
        starts = [s["scheduled_start"] for s in rows]
        assert starts == sorted(starts)
        assert {s["status"] for s in rows} == {"scheduled"}
    for user in (cast.stranger, cast.other_carer_user):
        assert (await api.get(url, headers=auth(user))).status_code == 404


async def test_check_in_and_out_through_the_shift_route(api, db, cast) -> None:
    b = await paid_booking(db, cast)
    [shift] = await shifts_of(db, b.id)
    url = f"/bookings/{b.id}/shifts/{shift.id}/check-ins"

    r = await api.post(
        url, json={"kind": "check_in", **fix(NEARBY, device_id="pixel-7")}, headers=cast.cg
    )
    assert r.status_code == 201, r.text
    check_in = r.json()
    assert (check_in["kind"], check_in["within_geofence"], check_in["device_id"]) == (
        "check_in",
        True,
        "pixel-7",
    )
    assert check_in["distance_m"] < 20
    assert (await booking_row(db, b.id)).status is BookingStatus.IN_PROGRESS
    [row] = await shifts_of(db, b.id)
    assert row.status is ShiftStatus.IN_PROGRESS and row.actual_start is not None

    # Care notes can go on a running shift (see the care-note tests).
    r = await api.post(url, json={"kind": "check_out", **fix(ENUGU)}, headers=cast.cg)
    assert r.status_code == 201, r.text
    assert r.json()["distance_m"] == 0
    [row] = await shifts_of(db, b.id)
    assert row.status is ShiftStatus.COMPLETED and row.actual_end is not None
    assert (await booking_row(db, b.id)).status is BookingStatus.COMPLETED

    listed = (await api.get(url, headers=cast.me)).json()
    assert [c["kind"] for c in listed] == ["check_in", "check_out"]
    assert len(await actions(db, "booking.check_in")) == 1
    assert len(await actions(db, "booking.check_out")) == 1
    # Inside the geofence: nobody is alarmed.
    assert cast.sms.outbox == []


async def test_check_in_replay_returns_the_original(api, db, cast) -> None:
    b = await paid_booking(db, cast)
    [shift] = await shifts_of(db, b.id)
    url = f"/bookings/{b.id}/shifts/{shift.id}/check-ins"
    first = (await api.post(url, json={"kind": "check_in", **fix(ENUGU)}, headers=cast.cg)).json()
    again = await api.post(url, json={"kind": "check_in", **fix(FAR)}, headers=cast.cg)
    assert again.status_code == 201
    assert again.json() == first
    assert await db.scalar(text("SELECT count(*) FROM check_ins")) == 1
    assert cast.sms.outbox == []  # the far replay wasn't treated as a new check-in


async def test_far_check_in_is_kept_flagged_and_family_told(api, db, cast) -> None:
    guardian = await make_user(db, UserRole.GUARDIAN, "Mama")
    await add_guardian(db, cast.patient, guardian)
    b = await paid_booking(db, cast)
    [shift] = await shifts_of(db, b.id)
    r = await api.post(
        f"/bookings/{b.id}/shifts/{shift.id}/check-ins",
        json={"kind": "check_in", **fix(FAR)},
        headers=cast.cg,
    )
    assert r.status_code == 201
    assert r.json()["within_geofence"] is False
    assert r.json()["distance_m"] > 1000
    for phone in (cast.patient_user.phone, guardian.phone):
        [notice] = texts_to(cast.sms, phone)
        assert "checked in about 1.1 km" in notice
    flagged = (await api.get("/bookings/ops/flagged-check-ins", headers=auth(cast.ops))).json()
    assert [f["id"] for f in flagged] == [r.json()["id"]]


async def test_check_in_list_follows_location_sharing(api, db, cast) -> None:
    b = await paid_booking(db, cast)
    [shift] = await shifts_of(db, b.id)
    url = f"/bookings/{b.id}/shifts/{shift.id}/check-ins"
    await api.post(url, json={"kind": "check_in", **fix(ENUGU)}, headers=cast.cg)
    for user in (cast.patient_user, cast.carer_user, cast.ops, cast.close_family):
        r = await api.get(url, headers=auth(user))
        assert r.status_code == 200 and len(r.json()) == 1, user.full_name
    r = await api.get(url, headers=auth(cast.family))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "booking_forbidden"
    assert (await api.get(url, headers=auth(cast.stranger))).status_code == 404


async def test_shift_must_belong_to_the_booking(api, db, cast) -> None:
    a = await paid_booking(db, cast)
    b = await paid_booking(db, cast, starts_in=timedelta(days=3))
    [foreign] = await shifts_of(db, b.id)
    base = f"/bookings/{a.id}/shifts/{foreign.id}"
    r = await api.post(
        f"{base}/check-ins", json={"kind": "check_in", **fix(ENUGU)}, headers=cast.cg
    )
    assert r.status_code == 404
    assert (await api.get(f"{base}/check-ins", headers=cast.me)).status_code == 404
    assert (await api.get(f"{base}/care-notes", headers=cast.me)).status_code == 404
    r = await api.post(f"{base}/care-notes", json={"body": "x"}, headers=cast.cg)
    assert r.status_code == 409  # a is paid, not started: the status is checked first
    assert (
        await api.get(f"/bookings/{a.id}/shifts/{uuid.uuid4()}/check-ins", headers=cast.me)
    ).status_code == 404


async def test_only_the_booked_caregiver_checks_in_on_a_paid_booking(api, db, cast) -> None:
    b = await paid_booking(db, cast)
    [shift] = await shifts_of(db, b.id)
    url = f"/bookings/{b.id}/shifts/{shift.id}/check-ins"
    body = {"kind": "check_in", **fix(ENUGU)}
    assert (await api.post(url, json=body, headers=cast.me)).status_code == 403
    assert (await api.post(url, json=body, headers=auth(cast.ops))).status_code == 403
    assert (await api.post(url, json=body, headers=auth(cast.other_carer_user))).status_code == 404

    unpaid = await paid_booking(
        db, cast, starts_in=timedelta(days=4), status=BookingStatus.ACCEPTED
    )
    [s] = await shifts_of(db, unpaid.id)
    r = await api.post(f"/bookings/{unpaid.id}/shifts/{s.id}/check-ins", json=body, headers=cast.cg)
    assert r.status_code == 409
    assert r.json()["error"]["details"]["allowed"] == ["paid", "in_progress"]


async def test_check_in_on_a_missed_shift(api, db, cast) -> None:
    b = await paid_booking(db, cast)
    [shift] = await shifts_of(db, b.id)
    shift.status = ShiftStatus.MISSED
    await db.commit()
    r = await api.post(
        f"/bookings/{b.id}/shifts/{shift.id}/check-ins",
        json={"kind": "check_in", **fix(ENUGU)},
        headers=cast.cg,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "shift_missed"


async def test_check_in_window(api, db, cast) -> None:
    b = await paid_booking(db, cast, starts_in=timedelta(hours=3))
    [shift] = await shifts_of(db, b.id)
    url = f"/bookings/{b.id}/shifts/{shift.id}/check-ins"
    # Two hours early is too early.
    r = await api.post(url, json={"kind": "check_in", **fix(ENUGU)}, headers=cast.cg)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "outside_shift_window"
    assert set(r.json()["error"]["details"]) == {"opens_at", "closes_at"}
    # A device clock more than five minutes ahead is refused.
    ahead = datetime.now(UTC) + timedelta(minutes=10)
    r = await api.post(url, json={"kind": "check_in", **fix(ENUGU, at=ahead)}, headers=cast.cg)
    assert r.status_code == 422
    assert "device clock" in r.json()["error"]["message"]
    assert await db.scalar(text("SELECT count(*) FROM check_ins")) == 0


async def test_check_out_window_and_order(api, db, cast) -> None:
    b = await paid_booking(
        db, cast, starts_in=-timedelta(hours=20), status=BookingStatus.IN_PROGRESS
    )
    [shift] = await shifts_of(db, b.id)
    url = f"/bookings/{b.id}/shifts/{shift.id}/check-ins"
    # Not checked in yet.
    r = await api.post(url, json={"kind": "check_out", **fix(ENUGU)}, headers=cast.cg)
    assert r.status_code == 422
    assert "Check in before" in r.json()["error"]["message"]

    shift.status = ShiftStatus.IN_PROGRESS
    shift.actual_start = shift.scheduled_start
    await db.commit()
    # The shift ended 17 hours ago: past the 12-hour grace.
    r = await api.post(url, json={"kind": "check_out", **fix(ENUGU)}, headers=cast.cg)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "outside_shift_window"
    # Before the check-in time.
    early = shift.scheduled_start - timedelta(minutes=1)
    r = await api.post(url, json={"kind": "check_out", **fix(ENUGU, at=early)}, headers=cast.cg)
    assert r.status_code == 422
    # An offline check-out stamped within the grace is accepted.
    ok = shift.scheduled_end + timedelta(hours=1)
    r = await api.post(url, json={"kind": "check_out", **fix(ENUGU, at=ok)}, headers=cast.cg)
    assert r.status_code == 201, r.text
    assert datetime.fromisoformat(r.json()["client_timestamp"]) == ok


async def test_multi_shift_booking_completes_after_the_last_shift(api, db, cast) -> None:
    b = await paid_booking(db, cast, starts_in=timedelta(minutes=10), hours=2, shifts=2)
    first, _second = await shifts_of(db, b.id)
    base = f"/bookings/{b.id}/shifts"
    await api.post(
        f"{base}/{first.id}/check-ins", json={"kind": "check_in", **fix(ENUGU)}, headers=cast.cg
    )
    await api.post(
        f"{base}/{first.id}/check-ins", json={"kind": "check_out", **fix(ENUGU)}, headers=cast.cg
    )
    # One shift still to run: in progress, not completed.
    assert (await booking_row(db, b.id)).status is BookingStatus.IN_PROGRESS


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "check_in", "location": {"latitude": 91, "longitude": 0}},
        {"kind": "arrive"},
        {"kind": "check_in", "accuracy_m": -1},
        {"kind": "check_in", "naive": True},
        {"kind": "check_in", "device_id": "x" * 129},
        {"kind": "check_in", "extra": True},
    ],
)
async def test_check_in_validation(api, db, cast, payload) -> None:
    b = await paid_booking(db, cast)
    [shift] = await shifts_of(db, b.id)
    body = {**fix(ENUGU), **payload}
    if body.pop("naive", False):
        body["client_timestamp"] = datetime.now(UTC).replace(tzinfo=None).isoformat()
    r = await api.post(f"/bookings/{b.id}/shifts/{shift.id}/check-ins", json=body, headers=cast.cg)
    assert r.status_code == 422


# ================================================================ care notes
async def running(db, cast, shifts=1):
    """A booking in progress whose first shift is checked in."""
    b = await paid_booking(db, cast, status=BookingStatus.IN_PROGRESS, shifts=shifts)
    rows = await shifts_of(db, b.id)
    rows[0].status = ShiftStatus.IN_PROGRESS
    rows[0].actual_start = rows[0].scheduled_start
    await db.commit()
    return b, rows


async def test_caregiver_writes_an_encrypted_care_note(api, db, cast) -> None:
    b, [shift] = await running(db, cast)
    url = f"/bookings/{b.id}/shifts/{shift.id}/care-notes"
    r = await api.post(
        url, json={"body": "  Drank 2 L of water. Pain 6/10 at noon.  "}, headers=cast.cg
    )
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["body"] == "Drank 2 L of water. Pain 6/10 at noon."
    assert note["author_id"] == str(cast.carer_user.id)

    raw = await db.scalar(text("SELECT body FROM care_notes"))
    assert b"Pain" not in bytes(raw)
    [entry] = await actions(db, "care_note.create")
    assert "Pain" not in str(entry.meta)

    r = await api.get(url, headers=cast.me)
    assert r.status_code == 200
    assert [n["body"] for n in r.json()] == [note["body"]]
    [view] = await actions(db, "care_note.view")
    assert (view.actor_id, view.meta["count"]) == (cast.patient_user.id, 1)


async def test_care_notes_are_for_the_caregiver_and_the_family(api, db, cast) -> None:
    b, [shift] = await running(db, cast)
    url = f"/bookings/{b.id}/shifts/{shift.id}/care-notes"
    await api.post(url, json={"body": "Slept well"}, headers=cast.cg)
    for user in (cast.carer_user, cast.patient_user, cast.close_family):
        r = await api.get(url, headers=auth(user))
        assert r.status_code == 200 and len(r.json()) == 1, user.full_name
    # Ops and family without health data shared can't read notes.
    for user in (cast.ops, cast.family):
        r = await api.get(url, headers=auth(user))
        assert r.status_code == 403, user.full_name
    for user in (cast.stranger, cast.other_carer_user):
        assert (await api.get(url, headers=auth(user))).status_code == 404
    # Only the caregiver writes them.
    for user in (cast.patient_user, cast.close_family, cast.ops):
        r = await api.post(url, json={"body": "x"}, headers=auth(user))
        assert r.status_code == 403
    assert await db.scalar(text("SELECT count(*) FROM care_notes")) == 1


async def test_empty_note_list_is_not_audited(api, db, cast) -> None:
    b, [shift] = await running(db, cast)
    r = await api.get(f"/bookings/{b.id}/shifts/{shift.id}/care-notes", headers=cast.me)
    assert r.json() == []
    assert await actions(db, "care_note.view") == []


async def test_notes_only_on_a_started_shift(api, db, cast) -> None:
    b, (_first, second) = await running(db, cast, shifts=2)
    r = await api.post(
        f"/bookings/{b.id}/shifts/{second.id}/care-notes", json={"body": "x"}, headers=cast.cg
    )
    assert r.status_code == 422
    paid = await paid_booking(db, cast, starts_in=timedelta(days=3))
    [s] = await shifts_of(db, paid.id)
    r = await api.post(
        f"/bookings/{paid.id}/shifts/{s.id}/care-notes", json={"body": "x"}, headers=cast.cg
    )
    assert r.status_code == 409
    assert r.json()["error"]["details"]["allowed"] == ["in_progress", "completed"]


async def test_notes_can_be_added_after_checkout(api, db, cast) -> None:
    b = await paid_booking(db, cast)
    await complete_booking(db, b.id)
    [shift] = await shifts_of(db, b.id)
    r = await api.post(
        f"/bookings/{b.id}/shifts/{shift.id}/care-notes",
        json={"body": "Handover done"},
        headers=cast.cg,
    )
    assert r.status_code == 201


@pytest.mark.parametrize("payload", [{"body": ""}, {"body": "   "}, {"body": "x" * 5001}, {}])
async def test_care_note_validation(api, db, cast, payload) -> None:
    b, [shift] = await running(db, cast)
    r = await api.post(
        f"/bookings/{b.id}/shifts/{shift.id}/care-notes", json=payload, headers=cast.cg
    )
    assert r.status_code == 422
    assert await db.scalar(select(CareNote.id)) is None


# ================================================================ reviews list
async def test_reviews_list(api, db, cast) -> None:
    b = await paid_booking(db, cast)
    await complete_booking(db, b.id)
    [shift] = await shifts_of(db, b.id)
    base = f"/bookings/{b.id}"
    family_review = await api.post(
        f"{base}/shifts/{shift.id}/reviews", json={"rating": 5, "comment": "Kind"}, headers=cast.me
    )
    assert family_review.status_code == 201, family_review.text
    carer_review = await api.post(
        f"{base}/shifts/{shift.id}/reviews", json={"rating": 4}, headers=cast.cg
    )
    assert carer_review.status_code == 201

    for user in (cast.patient_user, cast.carer_user, cast.ops, cast.family):
        r = await api.get(f"{base}/reviews", headers=auth(user))
        assert r.status_code == 200, user.full_name
        assert [(x["direction"], x["rating"]) for x in r.json()] == [
            ("family_to_caregiver", 5),
            ("caregiver_to_family", 4),
        ]
    [first, second] = (await api.get(f"{base}/reviews", headers=cast.me)).json()
    assert (first["reviewer_id"], first["reviewee_id"]) == (
        str(cast.patient_user.id),
        str(cast.carer_user.id),
    )
    assert (second["reviewer_id"], second["reviewee_id"]) == (
        str(cast.carer_user.id),
        str(cast.patient_user.id),
    )
    for user in (cast.stranger, cast.other_carer_user):
        assert (await api.get(f"{base}/reviews", headers=auth(user))).status_code == 404
    other = await paid_booking(db, cast, starts_in=timedelta(days=3))
    assert (await api.get(f"/bookings/{other.id}/reviews", headers=cast.me)).json() == []


# ================================================================ housekeeping
async def test_housekeeping_is_for_ops_only(api, db, cast) -> None:
    for user in (cast.patient_user, cast.carer_user, cast.family):
        r = await api.post("/bookings/ops/housekeeping", headers=auth(user))
        assert r.status_code == 403, user.full_name
    assert (await api.post("/bookings/ops/housekeeping")).status_code == 401


async def test_housekeeping_on_a_quiet_day_does_nothing(api, db, cast) -> None:
    fresh_request = await book(api, cast)
    r = await api.post("/bookings/ops/housekeeping", headers=auth(cast.ops))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "expired": 0,
        "shifts_missed": 0,
        "shifts_auto_closed": 0,
        "completed": 0,
        "no_shows": 0,
    }
    assert (await booking_row(db, fresh_request["id"])).status is BookingStatus.REQUESTED


async def test_housekeeping_expires_and_closes_and_settles(api, db, cast, minor) -> None:
    def mk(patient_id, booked_by, status, starts_in, **kw):
        return make_booking(
            db,
            patient_id=patient_id,
            caregiver=cast.carer,
            booked_by=booked_by,
            status=status,
            starts_in=starts_in,
            **kw,
        )

    pid, by = cast.patient.id, cast.patient_user.id
    # R2: never confirmed, and the start has passed.
    unanswered = await mk(pid, by, BookingStatus.REQUESTED, -timedelta(minutes=30))
    awaiting_guardian = await mk(
        minor.child.id, minor.booker_user.id, BookingStatus.PENDING_GUARDIAN, -timedelta(hours=1)
    )
    # R2: accepted but not paid within 24 hours.
    unpaid = await mk(pid, by, BookingStatus.ACCEPTED, timedelta(days=3))
    unpaid.accepted_at = datetime.now(UTC) - timedelta(hours=25)
    # Accepted recently and starting later: left alone.
    fine = await mk(pid, by, BookingStatus.ACCEPTED, timedelta(days=4))
    await db.commit()

    # R1: paid, the only shift ended 13 hours ago with no check-in: a no-show.
    no_show = await mk(pid, by, BookingStatus.PAID, -timedelta(hours=16))
    # R1: checked in, never checked out, ended 13 hours ago: auto-closed and completed.
    forgot = await mk(pid, by, BookingStatus.IN_PROGRESS, -timedelta(hours=16, minutes=5))
    [s] = await shifts_of(db, forgot.id)
    s.status, s.actual_start = ShiftStatus.IN_PROGRESS, s.scheduled_start
    # Two shifts: first ran, second missed: completed, not a no-show.
    partial = await mk(pid, by, BookingStatus.IN_PROGRESS, -timedelta(hours=19), shifts=2)
    p1, _p2 = await shifts_of(db, partial.id)
    p1.status = ShiftStatus.COMPLETED
    p1.actual_start, p1.actual_end = p1.scheduled_start, p1.scheduled_end
    # Paid, ended 11 hours ago: still inside the grace, left alone.
    grace = await mk(pid, by, BookingStatus.PAID, -timedelta(hours=14, minutes=10))
    await db.commit()
    cast.sms.outbox.clear()

    r = await api.post("/bookings/ops/housekeeping", headers=auth(cast.ops))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "expired": 3,
        "shifts_missed": 2,
        "shifts_auto_closed": 1,
        "completed": 2,
        "no_shows": 1,
    }

    status = {
        name: (await booking_row(db, b.id)).status
        for name, b in {
            "unanswered": unanswered,
            "awaiting_guardian": awaiting_guardian,
            "unpaid": unpaid,
            "fine": fine,
            "no_show": no_show,
            "forgot": forgot,
            "partial": partial,
            "grace": grace,
        }.items()
    }
    assert status == {
        "unanswered": BookingStatus.CANCELLED,
        "awaiting_guardian": BookingStatus.CANCELLED,
        "unpaid": BookingStatus.CANCELLED,
        "fine": BookingStatus.ACCEPTED,
        "no_show": BookingStatus.CANCELLED,
        "forgot": BookingStatus.COMPLETED,
        "partial": BookingStatus.COMPLETED,
        "grace": BookingStatus.PAID,
    }
    assert [s.status for s in await shifts_of(db, unpaid.id)] == [ShiftStatus.CANCELLED]
    assert [s.status for s in await shifts_of(db, no_show.id)] == [ShiftStatus.MISSED]
    [closed] = await shifts_of(db, forgot.id)
    assert closed.status is ShiftStatus.COMPLETED and closed.actual_end is None
    assert [s.status for s in await shifts_of(db, partial.id)] == [
        ShiftStatus.COMPLETED,
        ShiftStatus.MISSED,
    ]
    assert [s.status for s in await shifts_of(db, grace.id)] == [ShiftStatus.SCHEDULED]

    # Audited as the ops user who ran it, with the reason.
    expiries = {e.entity_id: e for e in await actions(db, "booking.expire")}
    assert expiries[unpaid.id].meta == {"previous_status": "accepted", "reason": "not_paid"}
    assert expiries[unanswered.id].meta["reason"] == "not_confirmed"
    assert {e.actor_id for e in expiries.values()} == {cast.ops.id}
    assert len(await actions(db, "booking.no_show")) == 1
    assert len(await actions(db, "shift.auto_close")) == 1

    # Notices: payers and caregivers, but not the caregiver for a booking they never saw.
    patient_texts = texts_to(cast.sms, cast.patient_user.phone)
    assert sum("wasn't paid in time" in t for t in patient_texts) == 1
    assert sum("wasn't confirmed in time" in t for t in patient_texts) == 1
    assert sum("did not attend" in t for t in patient_texts) == 1
    assert sum("did not check in" in t for t in patient_texts) == 2
    assert any("wasn't confirmed" in t for t in texts_to(cast.sms, minor.booker_user.phone))
    carer_texts = texts_to(cast.sms, cast.carer_user.phone)
    assert sum("cancelled because" in t for t in carer_texts) == 2  # unanswered, unpaid

    # A second run finds nothing left to do.
    r = await api.post("/bookings/ops/housekeeping", headers=auth(cast.ops))
    assert set(r.json().values()) == {0}


async def test_housekeeping_leaves_disputed_and_cancelled_bookings_alone(api, db, cast) -> None:
    for status in (BookingStatus.DISPUTED, BookingStatus.CANCELLED, BookingStatus.COMPLETED):
        await make_booking(
            db,
            patient_id=cast.patient.id,
            caregiver=cast.carer,
            booked_by=cast.patient_user.id,
            status=status,
            starts_in=-timedelta(days=2),
        )
    r = await api.post("/bookings/ops/housekeeping", headers=auth(cast.ops))
    assert set(r.json().values()) == {0}
    # A disputed booking's overdue shift stays scheduled: escrow is held for a human.
    statuses = set(await db.scalars(select(Shift.status)))
    assert statuses == {ShiftStatus.SCHEDULED}


@pytest.mark.xfail(
    strict=True,
    reason="Open question (Log.md): the caregiver can read a minor's booking, address "
    "included, before any guardian has approved them (TS-3). Remove this marker once "
    "get_booking_access and list_bookings hide pending_guardian bookings from caregivers.",
)
async def test_caregiver_cannot_see_a_minors_booking_before_approval(api, db, cast, minor) -> None:
    r = await api.post("/bookings", json=minor_body(cast, minor), headers=auth(minor.booker_user))
    assert r.json()["status"] == "pending_guardian"
    assert (await api.get("/bookings", headers=cast.cg)).json() == []
    assert (await api.get(f"/bookings/{r.json()['id']}", headers=cast.cg)).status_code == 404
