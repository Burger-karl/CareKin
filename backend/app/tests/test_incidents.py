"""Day 11 incidents and disputes (TS-2, TS-5): reporting, access, the ops workflow, and
how a dispute holds and frees a booking's escrow."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core.audit import AuditLog
from app.core.permissions import UserRole
from app.domains.bookings.models import BookingStatus
from app.domains.payments.models import EscrowStatus
from app.domains.trust.models import Incident
from app.tests.crisis_factories import auth, make_user
from app.tests.payments_fixtures import (  # noqa: F401 - fixtures, used by name
    booking_row,
    complete_booking,
    encryption_key_fixture,
    escrow_row,
    hold,
    ledger,
    make_booking,
    mocks_fixture,
    pay,
    world_fixture,
)

DESCRIPTION = "The caregiver arrived three hours late and left early."


def dispute(world, **extra):
    return {
        "booking_id": str(world.booking_id),
        "category": "conduct",
        "severity": "high",
        "description": DESCRIPTION,
        "is_dispute": True,
        **extra,
    }


async def _resolve(
    api, world, incident_id, status="resolved", resolution="Reviewed with both sides."
):
    return await api.patch(
        f"/incidents/{incident_id}",
        json={"status": status, "resolution": resolution},
        headers=world.ops_h,
    )


# ================================================================== reporting
async def test_report_without_a_booking(api, db, world):
    response = await api.post(
        "/incidents",
        json={
            "category": "other",
            "severity": "low",
            "description": "Someone called pretending to be CareKin.",
        },
        headers=world.patient,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "open" and body["is_dispute"] is False
    assert body["reported_by"] == str(world.patient_user.id) and body["against_user_id"] is None
    # TS-2: low severity gets a 72 hour human response target.
    from datetime import datetime

    created = datetime.fromisoformat(body["created_at"])
    due = datetime.fromisoformat(body["response_due_at"])
    assert timedelta(hours=71, minutes=59) < due - created < timedelta(hours=72, minutes=1)
    assert "incident.create" in list(await db.scalars(select(AuditLog.action)))


@pytest.mark.parametrize(("severity", "hours"), [("critical", 1), ("high", 4), ("medium", 24)])
async def test_response_targets_by_severity(api, world, severity, hours):
    from datetime import datetime

    response = await api.post(
        "/incidents",
        json={"category": "safety", "severity": severity, "description": DESCRIPTION},
        headers=world.patient,
    )
    body = response.json()
    gap = datetime.fromisoformat(body["response_due_at"]) - datetime.fromisoformat(
        body["created_at"]
    )
    assert abs(gap - timedelta(hours=hours)) < timedelta(minutes=1)


async def test_description_is_encrypted_at_rest(api, db, world):
    response = await api.post(
        "/incidents", json={"category": "safety", "description": DESCRIPTION}, headers=world.patient
    )
    raw = await db.scalar(
        text("SELECT description FROM incidents WHERE id = :id"), {"id": response.json()["id"]}
    )
    assert DESCRIPTION.encode() not in bytes(raw)
    audit = list(
        await db.scalars(select(AuditLog.meta).where(AuditLog.action == "incident.create"))
    )
    assert DESCRIPTION not in str(audit)


async def test_against_defaults_to_the_other_side(api, world):
    by_patient = await api.post(
        "/incidents",
        json={
            "booking_id": str(world.booking_id),
            "category": "conduct",
            "description": DESCRIPTION,
        },
        headers=world.patient,
    )
    assert by_patient.json()["against_user_id"] == str(world.caregiver_user.id)
    by_carer = await api.post(
        "/incidents",
        json={
            "booking_id": str(world.booking_id),
            "category": "conduct",
            "description": "The family was abusive.",
        },
        headers=world.carer,
    )
    assert by_carer.json()["against_user_id"] == str(world.patient_user.id)


async def test_against_must_be_on_the_booking(api, world):
    response = await api.post(
        "/incidents",
        json={
            "booking_id": str(world.booking_id),
            "category": "conduct",
            "description": DESCRIPTION,
            "against_user_id": str(world.stranger.id),
        },
        headers=world.patient,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "against_not_on_booking"


async def test_against_unknown_user_without_booking(api, world):
    response = await api.post(
        "/incidents",
        json={
            "category": "other",
            "description": DESCRIPTION,
            "against_user_id": str(uuid.uuid4()),
        },
        headers=world.patient,
    )
    assert response.status_code == 422


async def test_stranger_cannot_report_on_someone_elses_booking(api, db, world):
    response = await api.post(
        "/incidents",
        json={
            "booking_id": str(world.booking_id),
            "category": "safety",
            "description": DESCRIPTION,
        },
        headers=auth(world.stranger),
    )
    assert response.status_code == 404
    assert list(await db.scalars(select(Incident))) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"category": "safety", "description": "short"},
        {"category": "unknown", "description": DESCRIPTION},
        {"category": "safety", "description": DESCRIPTION, "severity": "extreme"},
        {"category": "safety", "description": DESCRIPTION, "is_dispute": True},
        {"category": "safety", "description": DESCRIPTION, "status": "resolved"},
    ],
    ids=["short", "bad-category", "bad-severity", "dispute-without-booking", "extra-field"],
)
async def test_report_validation(api, world, payload):
    response = await api.post("/incidents", json=payload, headers=world.patient)
    assert response.status_code == 422


async def test_report_needs_login(api):
    response = await api.post("/incidents", json={"category": "safety", "description": DESCRIPTION})
    assert response.status_code == 401


# ================================================================== disputes
async def test_dispute_holds_the_escrow(api, db, world, mocks, sms):
    await pay(api, world)
    response = await api.post("/incidents", json=dispute(world), headers=world.patient)
    assert response.status_code == 201, response.text
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.DISPUTED
    escrow = await escrow_row(db, world.booking_id)
    assert escrow.status is EscrowStatus.ON_HOLD_DISPUTE
    assert escrow.partner_status is EscrowStatus.ON_HOLD_DISPUTE
    assert (await mocks.escrow.get(escrow.partner_ref)).status is EscrowStatus.ON_HOLD_DISPUTE
    incident = await db.get(Incident, uuid.UUID(response.json()["id"]))
    assert incident.booking_status_before is BookingStatus.PAID
    assert any("dispute was opened" in m.body for m in sms.outbox)
    # Money didn't move: holding is a state, not a posting.
    assert [e.memo for e in await ledger(db)].count("escrow.funded") == 2


async def test_dispute_on_unpaid_booking_is_refused_and_nothing_saved(api, db, world):
    response = await api.post("/incidents", json=dispute(world), headers=world.patient)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "nothing_to_dispute"
    assert list(await db.scalars(select(Incident))) == []
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.ACCEPTED


async def test_dispute_after_release_is_refused(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    response = await api.post("/incidents", json=dispute(world), headers=world.patient)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "escrow_already_released"
    # The same report without a dispute still goes through.
    plain = await api.post(
        "/incidents", json=dispute(world, is_dispute=False), headers=world.patient
    )
    assert plain.status_code == 201


async def test_who_may_open_a_dispute(api, db, world):
    await pay(api, world)
    relative = await api.post("/incidents", json=dispute(world), headers=auth(world.relative))
    assert relative.status_code == 422 and relative.json()["error"]["code"] == "dispute_not_allowed"
    plain = await api.post(
        "/incidents", json=dispute(world, is_dispute=False), headers=auth(world.relative)
    )
    assert plain.status_code == 201
    family = await api.post("/incidents", json=dispute(world), headers=auth(world.family))
    assert family.status_code == 201


async def test_caregiver_can_open_a_dispute(api, db, world):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    response = await api.post(
        "/incidents",
        json=dispute(
            world, category="payment", description="The family refuses to confirm the visit."
        ),
        headers=world.carer,
    )
    assert response.status_code == 201
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.DISPUTED


async def test_closing_the_dispute_restores_the_booking_and_frees_release(api, db, world, mocks):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    incident = (await api.post("/incidents", json=dispute(world), headers=world.patient)).json()
    no_text = await api.patch(
        f"/incidents/{incident['id']}", json={"status": "resolved"}, headers=world.ops_h
    )
    assert no_text.status_code == 422 and no_text.json()["error"]["code"] == "resolution_required"
    closed = await _resolve(api, world, incident["id"])
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["status"] == "resolved" and body["resolved_at"] and body["first_response_at"]
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.COMPLETED
    escrow = await escrow_row(db, world.booking_id)
    assert escrow.status is EscrowStatus.HELD and escrow.partner_status is EscrowStatus.HELD
    released = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    assert released.status_code == 200


async def test_two_disputes_hold_until_both_close(api, db, world):
    await pay(api, world)
    first = (await api.post("/incidents", json=dispute(world), headers=world.patient)).json()
    second = (await api.post("/incidents", json=dispute(world), headers=world.carer)).json()
    assert (await db.get(Incident, uuid.UUID(second["id"]))).booking_status_before is None
    await _resolve(api, world, first["id"])
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.DISPUTED
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.ON_HOLD_DISPUTE
    await _resolve(api, world, second["id"], status="dismissed")
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.PAID
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.HELD


async def test_dispute_settled_for_the_family_by_refund(api, db, world):
    await pay(api, world)
    incident = (await api.post("/incidents", json=dispute(world), headers=world.patient)).json()
    refund = await api.post(
        f"/bookings/{world.booking_id}/escrow/refund",
        json={"reason": "Dispute upheld for the family."},
        headers=world.ops_h,
    )
    assert refund.status_code == 200, refund.text
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.CANCELLED
    closed = await _resolve(api, world, incident["id"])
    assert closed.status_code == 200
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.CANCELLED
    assert (await escrow_row(db, world.booking_id)).status is EscrowStatus.REFUNDED


async def test_dispute_is_saved_even_when_the_partner_is_down(api, db, world, mocks):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    mocks.escrow.unavailable = True
    response = await api.post("/incidents", json=dispute(world), headers=world.patient)
    assert response.status_code == 201, response.text
    escrow = await escrow_row(db, world.booking_id)
    assert escrow.status is EscrowStatus.ON_HOLD_DISPUTE  # our state holds the money
    assert escrow.partner_status is EscrowStatus.HELD  # the partner hasn't heard yet
    mocks.escrow.unavailable = False
    release = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.ops_h)
    assert release.status_code == 409 and release.json()["error"]["code"] == "dispute_open"
    settled = await api.post("/payments/ops/settle", headers=world.ops_h)
    assert settled.json()["partner_synced"] == 1
    escrow = await escrow_row(db, world.booking_id)
    assert escrow.partner_status is EscrowStatus.ON_HOLD_DISPUTE
    assert (await mocks.escrow.get(escrow.partner_ref)).status is EscrowStatus.ON_HOLD_DISPUTE


async def test_dispute_closed_during_outage_syncs_before_release(api, db, world, mocks):
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    incident = (await api.post("/incidents", json=dispute(world), headers=world.patient)).json()
    mocks.escrow.unavailable = True
    assert (await _resolve(api, world, incident["id"])).status_code == 200
    escrow = await escrow_row(db, world.booking_id)
    assert (
        escrow.status is EscrowStatus.HELD and escrow.partner_status is EscrowStatus.ON_HOLD_DISPUTE
    )
    mocks.escrow.unavailable = False
    # Release brings the partner in line first, then releases.
    release = await api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient)
    assert release.status_code == 200, release.text
    assert (await mocks.escrow.get(escrow.partner_ref)).status is EscrowStatus.RELEASED


async def test_concurrent_disputes_and_release(api, db, world):
    """A release racing a dispute: either the dispute lands first (release refused) or the
    release does (dispute refused). Never both, never money released while disputed."""
    await pay(api, world)
    await complete_booking(db, world.booking_id)
    release, report = await asyncio.gather(
        api.post(f"/bookings/{world.booking_id}/escrow/release", headers=world.patient),
        api.post("/incidents", json=dispute(world), headers=world.carer),
    )
    escrow = await escrow_row(db, world.booking_id)
    if release.status_code == 200:
        assert report.status_code == 409
        assert escrow.status is EscrowStatus.RELEASED
    else:
        assert release.status_code == 409 and report.status_code == 201
        assert escrow.status is EscrowStatus.ON_HOLD_DISPUTE


async def test_ops_can_turn_a_report_into_a_dispute(api, db, world):
    await pay(api, world)
    report = (
        await api.post("/incidents", json=dispute(world, is_dispute=False), headers=world.patient)
    ).json()
    response = await api.patch(
        f"/incidents/{report['id']}", json={"is_dispute": True}, headers=world.ops_h
    )
    assert response.status_code == 200 and response.json()["is_dispute"] is True
    assert (await booking_row(db, world.booking_id)).status is BookingStatus.DISPUTED
    off = await api.patch(
        f"/incidents/{report['id']}", json={"is_dispute": False}, headers=world.ops_h
    )
    assert off.status_code == 422 and off.json()["error"]["code"] == "dispute_flag_one_way"


async def test_ops_cannot_make_a_bookingless_report_a_dispute(api, world):
    report = (
        await api.post(
            "/incidents",
            json={"category": "other", "description": DESCRIPTION},
            headers=world.patient,
        )
    ).json()
    response = await api.patch(
        f"/incidents/{report['id']}", json={"is_dispute": True}, headers=world.ops_h
    )
    assert response.status_code == 422


# ================================================================== ops workflow
async def test_status_workflow(api, db, world):
    report = (
        await api.post(
            "/incidents",
            json={"category": "safety", "description": DESCRIPTION},
            headers=world.patient,
        )
    ).json()
    url = f"/incidents/{report['id']}"
    working = await api.patch(url, json={"status": "investigating"}, headers=world.ops_h)
    assert working.status_code == 200
    assert working.json()["first_response_at"] is not None
    back = await api.patch(url, json={"status": "open"}, headers=world.ops_h)
    assert back.status_code == 409 and back.json()["error"]["code"] == "invalid_transition"
    done = await _resolve(api, world, report["id"])
    assert done.status_code == 200
    again = await api.patch(url, json={"severity": "low"}, headers=world.ops_h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "incident_closed"


async def test_assignment_only_to_active_ops(api, db, world):
    report = (
        await api.post(
            "/incidents",
            json={"category": "safety", "description": DESCRIPTION},
            headers=world.patient,
        )
    ).json()
    url = f"/incidents/{report['id']}"
    bad = await api.patch(
        url, json={"assigned_to": str(world.patient_user.id)}, headers=world.ops_h
    )
    assert bad.status_code == 422
    other_ops = await make_user(db, UserRole.OPS, "Second Ops")
    good = await api.patch(url, json={"assigned_to": str(other_ops.id)}, headers=world.ops_h)
    assert good.status_code == 200 and good.json()["assigned_to"] == str(other_ops.id)
    assert good.json()["first_response_at"] is not None
    unassigned = await api.patch(url, json={"assigned_to": None}, headers=world.ops_h)
    assert unassigned.json()["assigned_to"] is None


async def test_severity_change_moves_the_deadline_until_someone_responds(api, world):
    from datetime import datetime

    report = (
        await api.post(
            "/incidents",
            json={"category": "safety", "severity": "low", "description": DESCRIPTION},
            headers=world.patient,
        )
    ).json()
    url = f"/incidents/{report['id']}"
    raised = (await api.patch(url, json={"severity": "critical"}, headers=world.ops_h)).json()
    gap = datetime.fromisoformat(raised["response_due_at"]) - datetime.fromisoformat(
        raised["created_at"]
    )
    assert abs(gap - timedelta(hours=1)) < timedelta(minutes=1)
    await api.patch(url, json={"status": "investigating"}, headers=world.ops_h)
    lowered = (await api.patch(url, json={"severity": "low"}, headers=world.ops_h)).json()
    assert lowered["response_due_at"] == raised["response_due_at"]


async def test_only_ops_can_update(api, world):
    report = (
        await api.post(
            "/incidents",
            json={"category": "safety", "description": DESCRIPTION},
            headers=world.patient,
        )
    ).json()
    for headers in (world.patient, world.carer, auth(world.stranger)):
        response = await api.patch(
            f"/incidents/{report['id']}", json={"status": "dismissed"}, headers=headers
        )
        assert response.status_code == 404
    empty = await api.patch(f"/incidents/{report['id']}", json={}, headers=world.ops_h)
    assert empty.status_code == 422
    missing = await api.patch(
        f"/incidents/{uuid.uuid4()}", json={"status": "investigating"}, headers=world.ops_h
    )
    assert missing.status_code == 404


# ================================================================== reading
async def test_who_can_read_an_incident(api, world):
    report = (
        await api.post(
            "/incidents",
            json={
                "booking_id": str(world.booking_id),
                "category": "conduct",
                "description": DESCRIPTION,
            },
            headers=world.patient,
        )
    ).json()
    url = f"/incidents/{report['id']}"
    assert (await api.get(url, headers=world.patient)).status_code == 200
    assert (await api.get(url, headers=world.carer)).status_code == 200  # it's about them
    assert (await api.get(url, headers=world.ops_h)).status_code == 200
    assert (await api.get(url, headers=auth(world.family))).status_code == 404
    assert (await api.get(url, headers=auth(world.stranger))).status_code == 404
    assert (await api.get(url, headers=world.patient)).json()["description"] == DESCRIPTION


async def test_lists(api, world):
    await pay(api, world)
    urgent = (
        await api.post(
            "/incidents",
            json={"category": "safety", "severity": "critical", "description": DESCRIPTION},
            headers=world.carer,
        )
    ).json()
    mine = (await api.post("/incidents", json=dispute(world), headers=world.patient)).json()
    ops_queue = (await api.get("/incidents", headers=world.ops_h)).json()
    assert [i["id"] for i in ops_queue] == [urgent["id"], mine["id"]]  # most urgent first
    patient_list = (await api.get("/incidents", headers=world.patient)).json()
    assert [i["id"] for i in patient_list] == [mine["id"]]
    disputes = (await api.get("/incidents?disputes_only=true", headers=world.ops_h)).json()
    assert [i["id"] for i in disputes] == [mine["id"]]
    by_status = (await api.get("/incidents?status=resolved", headers=world.ops_h)).json()
    assert by_status == []
    by_booking = (
        await api.get(f"/incidents?booking_id={world.booking_id}", headers=world.ops_h)
    ).json()
    assert [i["id"] for i in by_booking] == [mine["id"]]
