"""Day 12: a completed booking the settlement rule can't release is queued for operations.

Clean bookings are released by the rule after the hold period (covered in test_payments).
Anything else used to sit held without a word; now the sweep opens one incident that names
the reasons, and operations releases or refunds by hand.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, text, update

from app.core.audit import AuditLog
from app.domains.bookings.models import Shift, ShiftStatus
from app.domains.payments import service as payments
from app.domains.payments.models import EscrowStatus
from app.domains.trust.models import Incident, IncidentCategory, IncidentStatus, Severity
from app.tests.payments_fixtures import (  # noqa: F401 - fixtures, used by name
    World,
    booking_row,
    complete_booking,
    encryption_key_fixture,
    escrow_row,
    make_booking,
    mocks_fixture,
    pay,
    world_fixture,
)


async def _settle(api, world: World) -> dict:
    response = await api.post("/payments/ops/settle", headers=world.ops_h)
    assert response.status_code == 200, response.text
    return response.json()


async def _incidents(db, world: World) -> list[Incident]:
    return list(
        await db.scalars(
            select(Incident)
            .where(Incident.booking_id == world.booking_id)
            .execution_options(populate_existing=True)
        )
    )


async def _queued_actions(db) -> int:
    return await db.scalar(
        select(func.count(AuditLog.id)).where(AuditLog.action == payments.RELEASE_QUEUED)
    )


# ------------------------------------------------------------------ queued
async def test_a_flagged_check_in_is_queued_for_operations(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id, inside=False)

    result = await _settle(api, world)

    assert result["queued_for_ops"] == 1 and result["released"] == 0 and result["errors"] == 0
    incidents = await _incidents(db, world)
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.category is IncidentCategory.PAYMENT
    assert incident.severity is Severity.MEDIUM
    assert incident.reported_by is None and incident.is_dispute is False
    assert incident.status is IncidentStatus.OPEN
    assert "outside the service location" in incident.description
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.HELD
    assert await _queued_actions(db) == 1


async def test_queueing_happens_once(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id, inside=False)
    await _settle(api, world)

    again = await _settle(api, world)

    assert again["queued_for_ops"] == 0
    assert len(await _incidents(db, world)) == 1


async def test_a_resolved_incident_is_not_reopened(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id, inside=False)
    await _settle(api, world)
    await db.execute(
        update(Incident)
        .where(Incident.booking_id == world.booking_id)
        .values(status=IncidentStatus.RESOLVED)
    )
    await db.commit()

    again = await _settle(api, world)

    assert again["queued_for_ops"] == 0
    assert len(await _incidents(db, world)) == 1
    # The rule still won't release a flagged booking: only a human can now.
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.HELD


async def test_a_missed_shift_is_queued(api, db, world):
    booking = await make_booking(
        db,
        patient_id=world.patient_id,
        caregiver=world.caregiver,
        booked_by=world.patient_user.id,
        starts_in=timedelta(days=5),
        shifts=2,
    )
    world.booking_id = booking.id
    await pay(api, world)
    await complete_booking(db, booking.id)
    first = await db.scalar(
        select(Shift).where(Shift.booking_id == booking.id).order_by(Shift.scheduled_start)
    )
    first.status = ShiftStatus.MISSED
    await db.commit()

    result = await _settle(api, world)

    assert result["queued_for_ops"] == 1
    (incident,) = await _incidents(db, world)
    assert "not every shift was completed" in incident.description


async def test_a_missing_check_out_is_queued(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    await db.execute(text("DELETE FROM check_ins WHERE kind = 'check_out'"))
    await db.commit()

    result = await _settle(api, world)

    assert result["queued_for_ops"] == 1
    (incident,) = await _incidents(db, world)
    assert "missing its check-in or check-out" in incident.description


async def test_operations_can_then_release_by_hand(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id, inside=False)
    await _settle(api, world)

    response = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.ops_h)

    assert response.status_code == 200, response.text
    escrow = await escrow_row(db, world.booking_id)
    assert escrow.status is EscrowStatus.RELEASED
    assert escrow.release_method.value == "human" and escrow.released_by == world.ops.id


# --------------------------------------------------------------- not queued
async def test_a_clean_booking_inside_the_hold_period_is_left_alone(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id, checked_out_ago=timedelta(hours=2))

    result = await _settle(api, world)

    assert result["queued_for_ops"] == 0 and result["released"] == 0
    assert await _incidents(db, world) == []


async def test_a_clean_booking_after_the_hold_period_is_released_not_queued(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id, checked_out_ago=timedelta(hours=25))

    result = await _settle(api, world)

    assert result["released"] == 1 and result["queued_for_ops"] == 0
    assert await _incidents(db, world) == []


async def test_an_open_incident_already_puts_it_in_front_of_operations(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id, inside=False)
    report = await api.post(
        "/incidents",
        json={
            "booking_id": str(world.booking_id),
            "category": "safety",
            "description": "Left the gas on overnight.",
        },
        headers=world.patient,
    )
    assert report.status_code == 201

    result = await _settle(api, world)

    assert result["queued_for_ops"] == 0
    assert len(await _incidents(db, world)) == 1  # only the family's report


async def test_an_unpaid_booking_is_not_queued(api, db, world):
    await complete_booking(db, world.booking_id, inside=False)

    result = await _settle(api, world)

    assert result["queued_for_ops"] == 0
    assert await _incidents(db, world) == []


# ------------------------------------------------------------------ blockers
async def test_release_blockers_lists_every_reason(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id, inside=False)
    booking = await booking_row(db, world.booking_id)
    assert await payments.release_blockers(db, booking) == [
        "a check-in or check-out was outside the service location"
    ]


async def test_release_blockers_is_empty_for_a_clean_booking(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    booking = await booking_row(db, world.booking_id)
    assert await payments.release_blockers(db, booking) == []