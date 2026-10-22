"""Day 13 ops console (OP-1, OP-3, OP-5, TS-1): vetting queue and approval, facility
verification, dashboard and audit log."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.audit import AuditLog, write_audit
from app.core.permissions import UserRole
from app.domains.caregivers.models import (
    Caregiver,
    CaregiverStatus,
    CaregiverTier,
    Certification,
    CertType,
    CheckStatus,
    CheckType,
    Guarantor,
    ReviewStatus,
    VerificationCheck,
)
from app.domains.facilities.models import Facility, FacilityService
from app.domains.trust.models import Incident, IncidentCategory, IncidentStatus, Severity
from app.tests.crisis_factories import ENUGU, auth, make_facility, make_user, phone
from app.tests.payments_fixtures import encryption_key_fixture  # noqa: F401 - autouse fixture


# ------------------------------------------------------------------ builders
async def make_applicant(
    db,
    *,
    tier: CaregiverTier = CaregiverTier.TRAINED_CARER,
    status: CaregiverStatus = CaregiverStatus.VERIFYING,
    guarantors: int = 2,
    nin: CheckStatus | None = CheckStatus.PASSED,
    background: CheckStatus | None = CheckStatus.PASSED,
    licence: ReviewStatus | None = None,
    requested_by=None,
    name: str = "Amaka Nwosu",
) -> Caregiver:
    user = await make_user(db, UserRole.CAREGIVER, name)
    caregiver = Caregiver(user_id=user.id, tier=tier, status=status, hourly_rate_minor=150_000)
    db.add(caregiver)
    await db.flush()
    for i in range(guarantors):
        db.add(
            Guarantor(
                caregiver_id=caregiver.id,
                full_name=f"Guarantor {i}",
                phone=phone(),
                relationship="uncle",
                address="12 Ogui Road, Enugu",
            )
        )
    checker = requested_by or (await make_user(db, UserRole.OPS, "Checker")).id
    for check_type, outcome in ((CheckType.NIN, nin), (CheckType.BACKGROUND, background)):
        if outcome is not None:
            db.add(
                VerificationCheck(
                    caregiver_id=caregiver.id,
                    check_type=check_type,
                    provider="mock",
                    status=outcome,
                    requested_by=checker,
                )
            )
    if licence is not None:
        db.add(
            Certification(
                caregiver_id=caregiver.id,
                cert_type=CertType.NURSING_LICENCE,
                title="RN licence",
                licence_number="RN-12345",
                status=licence,
            )
        )
    await db.commit()
    return caregiver


@pytest.fixture
async def ops(db):
    user = await make_user(db, UserRole.OPS, "Ifeoma Ops")
    return user


@pytest.fixture
def ops_h(ops):
    return auth(ops)


# ===================================================================== access
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/ops/dashboard"),
        ("get", "/ops/caregivers/pending"),
        ("post", f"/ops/caregivers/{uuid.uuid4()}/approve"),
        ("get", "/ops/facilities/pending"),
        ("post", f"/ops/facilities/{uuid.uuid4()}/verify"),
        ("get", "/audit-log"),
    ],
)
async def test_ops_only(api, db, method, path):
    assert (await api.request(method, path)).status_code == 401
    for role in (UserRole.PATIENT, UserRole.CAREGIVER, UserRole.FACILITY_ADMIN):
        user = await make_user(db, role)
        response = await api.request(method, path, headers=auth(user), json={})
        assert response.status_code == 403, (role, response.text)
        assert response.json()["error"]["code"] == "role_forbidden"


# ============================================================ caregiver queue
async def test_pending_queue_shows_blockers(api, db, ops, ops_h):
    ready = await make_applicant(db, name="Ready Carer", requested_by=ops.id)
    no_checks = await make_applicant(
        db, status=CaregiverStatus.APPLIED, nin=None, background=None, requested_by=ops.id
    )
    failed = await make_applicant(db, background=CheckStatus.FAILED, guarantors=1, requested_by=ops.id)
    approved = await make_applicant(db, status=CaregiverStatus.APPROVED, requested_by=ops.id)

    response = await api.get("/ops/caregivers/pending", headers=ops_h)
    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()}
    assert str(approved.id) not in rows  # decided caregivers leave the queue

    assert rows[str(ready.id)]["ready_to_approve"] is True
    assert rows[str(ready.id)]["blockers"] == []
    assert rows[str(ready.id)]["checks"] == {"nin": "passed", "background": "passed"}
    assert rows[str(ready.id)]["guarantor_count"] == 2
    assert rows[str(ready.id)]["full_name"] == "Ready Carer"

    blocked = rows[str(no_checks.id)]
    assert blocked["ready_to_approve"] is False
    assert any("verification has not been run" in b for b in blocked["blockers"])
    assert any("NIN check has not been run" in b for b in blocked["blockers"])

    bad = rows[str(failed.id)]["blockers"]
    assert "background check failed" in bad
    assert "needs 2 guarantors, has 1" in bad

    ready_only = await api.get("/ops/caregivers/pending?ready_only=true", headers=ops_h)
    assert [row["id"] for row in ready_only.json()] == [str(ready.id)]


async def test_queue_uses_latest_check(api, db, ops, ops_h):
    """A failed check that was re-run and passed no longer blocks."""
    caregiver = await make_applicant(db, nin=CheckStatus.ERROR, requested_by=ops.id)
    db.add(
        VerificationCheck(
            caregiver_id=caregiver.id,
            check_type=CheckType.NIN,
            provider="mock",
            status=CheckStatus.PASSED,
            requested_by=ops.id,
        )
    )
    await db.commit()
    rows = (await api.get("/ops/caregivers/pending", headers=ops_h)).json()
    assert rows[0]["checks"]["nin"] == "passed" and rows[0]["ready_to_approve"] is True


# ================================================================ approval
async def test_approve_ready_caregiver(api, db, ops, ops_h):
    caregiver = await make_applicant(db, requested_by=ops.id)
    response = await api.post(f"/ops/caregivers/{caregiver.id}/approve", json={}, headers=ops_h)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == str(ops.id) and body["approved_at"] is not None

    await db.refresh(caregiver)
    assert caregiver.status is CaregiverStatus.APPROVED
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "caregiver.approve"))
    assert entry.actor_id == ops.id and entry.entity_id == caregiver.id

    # Approved caregivers are now publicly visible and bookable.
    stranger = await make_user(db, UserRole.PATIENT)
    assert (await api.get(f"/caregivers/{caregiver.id}", headers=auth(stranger))).status_code == 200

    again = await api.post(f"/ops/caregivers/{caregiver.id}/approve", json={}, headers=ops_h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "caregiver_not_pending"


async def test_cannot_approve_incomplete_vetting(api, db, ops, ops_h):
    caregiver = await make_applicant(db, nin=CheckStatus.FAILED, guarantors=1, requested_by=ops.id)
    response = await api.post(f"/ops/caregivers/{caregiver.id}/approve", json={}, headers=ops_h)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "caregiver_not_ready"
    assert "NIN check failed" in error["details"]["blockers"]
    await db.refresh(caregiver)
    assert caregiver.status is CaregiverStatus.VERIFYING


async def test_cannot_approve_before_verification_runs(api, db, ops, ops_h):
    caregiver = await make_applicant(db, status=CaregiverStatus.APPLIED, requested_by=ops.id)
    response = await api.post(f"/ops/caregivers/{caregiver.id}/approve", json={}, headers=ops_h)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "caregiver_not_ready"


async def test_nurse_needs_verified_licence_and_can_get_it_in_one_step(api, db, ops, ops_h):
    nurse = await make_applicant(
        db, tier=CaregiverTier.LICENSED_NURSE, licence=ReviewStatus.PENDING, requested_by=ops.id
    )
    response = await api.post(f"/ops/caregivers/{nurse.id}/approve", json={}, headers=ops_h)
    assert response.status_code == 409
    assert "licensed nurse needs a verified nursing licence" in (
        response.json()["error"]["details"]["blockers"]
    )

    licence_id = await db.scalar(
        select(Certification.id).where(Certification.caregiver_id == nurse.id)
    )
    response = await api.post(
        f"/ops/caregivers/{nurse.id}/approve",
        json={"certifications": [{"id": str(licence_id), "status": "verified"}]},
        headers=ops_h,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert body["certifications"][0]["status"] == "verified"
    cert = await db.get(Certification, licence_id)
    await db.refresh(cert)
    assert cert.verified_by == ops.id


async def test_failed_approval_does_not_save_certification_changes(api, db, ops, ops_h):
    nurse = await make_applicant(
        db,
        tier=CaregiverTier.LICENSED_NURSE,
        licence=ReviewStatus.PENDING,
        background=CheckStatus.FAILED,
        requested_by=ops.id,
    )
    licence_id = await db.scalar(
        select(Certification.id).where(Certification.caregiver_id == nurse.id)
    )
    response = await api.post(
        f"/ops/caregivers/{nurse.id}/approve",
        json={"certifications": [{"id": str(licence_id), "status": "verified"}]},
        headers=ops_h,
    )
    assert response.status_code == 409
    status = await db.scalar(select(Certification.status).where(Certification.id == licence_id))
    assert status is ReviewStatus.PENDING


async def test_reject_needs_reason_and_is_audited(api, db, ops, ops_h):
    caregiver = await make_applicant(db, status=CaregiverStatus.APPLIED, requested_by=ops.id)
    response = await api.post(
        f"/ops/caregivers/{caregiver.id}/approve", json={"decision": "reject"}, headers=ops_h
    )
    assert response.status_code == 422

    response = await api.post(
        f"/ops/caregivers/{caregiver.id}/approve",
        json={"decision": "reject", "reason": "Guarantors could not be reached"},
        headers=ops_h,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "caregiver.reject"))
    assert entry.meta["reason"] == "Guarantors could not be reached"


async def test_certification_from_another_caregiver_is_refused(api, db, ops, ops_h):
    nurse = await make_applicant(
        db, tier=CaregiverTier.LICENSED_NURSE, licence=ReviewStatus.PENDING, requested_by=ops.id
    )
    other = await make_applicant(db, requested_by=ops.id, name="Other Carer")
    licence_id = await db.scalar(
        select(Certification.id).where(Certification.caregiver_id == nurse.id)
    )
    response = await api.post(
        f"/ops/caregivers/{other.id}/approve",
        json={"certifications": [{"id": str(licence_id), "status": "verified"}]},
        headers=ops_h,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "certification_not_found"


async def test_approve_unknown_caregiver(api, ops_h):
    response = await api.post(f"/ops/caregivers/{uuid.uuid4()}/approve", json={}, headers=ops_h)
    assert response.status_code == 404


# ============================================================ facility queue
async def test_facility_queue_reasons(api, db, ops_h, monkeypatch):
    new = await make_facility(
        db, "New Clinic", *ENUGU, status="pending", location_verified=False, service_status="pending"
    )
    no_location = Facility(name="No Map Clinic", address="Enugu", phone_number=phone())
    db.add(no_location)
    fresh = await make_facility(db, "Fresh Hospital", *ENUGU)
    fresh.last_verified_at = datetime.now(UTC)
    old = await make_facility(db, "Old Hospital", *ENUGU)
    old.last_verified_at = datetime.now(UTC) - timedelta(days=200)
    rejected = await make_facility(db, "Closed Clinic", *ENUGU, status="rejected")
    await db.commit()

    response = await api.get("/ops/facilities/pending", headers=ops_h)
    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()}
    assert str(fresh.id) not in rows and str(rejected.id) not in rows
    assert set(rows[str(new.id)]["reasons"]) == {"unverified", "location_unconfirmed", "services_pending"}
    assert rows[str(no_location.id)]["reasons"] == ["unverified", "no_location"]
    assert rows[str(old.id)]["reasons"] == ["stale"]
    # Never-checked listings come before stale ones.
    order = [row["id"] for row in response.json()]
    assert order.index(str(new.id)) < order.index(str(old.id))

    without_stale = await api.get("/ops/facilities/pending?include_stale=false", headers=ops_h)
    assert str(old.id) not in {row["id"] for row in without_stale.json()}


# ============================================================ verify facility
async def test_verify_facility_stamps_and_reaches_crisis_screen(api, db, ops, ops_h):
    facility = await make_facility(
        db,
        "Parklane Hospital",
        *ENUGU,
        status="pending",
        location_verified=False,
        service_status="pending",
    )
    response = await api.post(
        f"/ops/facilities/{facility.id}/verify", json={"decision": "verified"}, headers=ops_h
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["facility"]["verification_status"] == "verified"
    assert body["facility"]["location_verified"] is True
    assert body["facility"]["last_verified_at"] is not None
    assert all(sv["verification_status"] == "verified" for sv in body["services"])

    await db.refresh(facility)
    assert facility.verified_by == ops.id
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "facility.verify"))
    assert entry.meta["services_verified"] == 1

    # FF-3: public facility details show the date.
    public = await api.get(f"/facilities/{facility.id}")
    assert public.status_code == 200 and public.json()["last_verified_at"] is not None

    # The crisis screen now finds it, with the date (it was a placeholder before Day 13).
    from app.domains.crisis.facilities import nearby

    found = await nearby(db, *ENUGU)
    assert [row["id"] for row in found.facilities] == [facility.id]
    assert found.facilities[0]["last_verified_at"] is not None


async def test_verify_with_explicit_service_results(api, db, ops_h):
    facility = await make_facility(
        db,
        "Mother of Christ",
        *ENUGU,
        services=("haematologist", "transfusion"),
        status="pending",
        service_status="pending",
    )
    services = {
        sv.service_type: sv.id
        for sv in await db.scalars(
            select(FacilityService).where(FacilityService.facility_id == facility.id)
        )
    }
    response = await api.post(
        f"/ops/facilities/{facility.id}/verify",
        json={
            "decision": "verified",
            "services": [
                {"id": str(services["haematologist"]), "status": "verified"},
                {"id": str(services["transfusion"]), "status": "rejected"},
            ],
        },
        headers=ops_h,
    )
    assert response.status_code == 200, response.text
    result = {sv["service_type"]: sv["verification_status"] for sv in response.json()["services"]}
    assert result == {"haematologist": "verified", "transfusion": "rejected"}


async def test_reject_facility_needs_note(api, db, ops_h):
    facility = await make_facility(db, "Ghost Clinic", *ENUGU, status="pending")
    response = await api.post(
        f"/ops/facilities/{facility.id}/verify", json={"decision": "rejected"}, headers=ops_h
    )
    assert response.status_code == 422
    response = await api.post(
        f"/ops/facilities/{facility.id}/verify",
        json={"decision": "rejected", "note": "Number not in service; address is a shop"},
        headers=ops_h,
    )
    assert response.status_code == 200, response.text
    assert response.json()["facility"]["verification_status"] == "rejected"
    assert response.json()["facility"]["location_verified"] is False


async def test_cannot_confirm_missing_location(api, db, ops_h):
    facility = Facility(name="No Map Clinic", address="Enugu", phone_number=phone())
    db.add(facility)
    await db.commit()
    response = await api.post(
        f"/ops/facilities/{facility.id}/verify",
        json={"decision": "verified", "location_verified": True},
        headers=ops_h,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "facility_has_no_location"
    # Without asking to confirm a location, the listing itself can still be verified.
    response = await api.post(
        f"/ops/facilities/{facility.id}/verify", json={"decision": "verified"}, headers=ops_h
    )
    assert response.status_code == 200
    assert response.json()["facility"]["location_verified"] is False


async def test_service_from_another_facility_is_refused(api, db, ops_h):
    a = await make_facility(db, "A Clinic", *ENUGU, status="pending")
    b = await make_facility(db, "B Clinic", *ENUGU, status="pending")
    b_service = await db.scalar(select(FacilityService.id).where(FacilityService.facility_id == b.id))
    response = await api.post(
        f"/ops/facilities/{a.id}/verify",
        json={"decision": "verified", "services": [{"id": str(b_service), "status": "verified"}]},
        headers=ops_h,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "facility_service_not_found"


async def test_verify_unknown_facility(api, ops_h):
    response = await api.post(
        f"/ops/facilities/{uuid.uuid4()}/verify", json={"decision": "verified"}, headers=ops_h
    )
    assert response.status_code == 404


# ================================================================ dashboard
async def test_dashboard_on_empty_platform(api, ops_h):
    response = await api.get("/ops/dashboard", headers=ops_h)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["period_days"] == 7
    assert body["users"]["by_role"]["ops"] == 1
    assert set(body["caregivers"]["by_status"]) == {s.value for s in CaregiverStatus}
    assert body["pilot"] == {"caregiver_hours": 0.0, "completed_shifts": 0, "family_spend": []}
    assert body["incidents"]["avg_first_response_minutes"] is None
    assert body["bookings"]["avg_minutes_to_accept"] is None


async def test_dashboard_counts(api, db, ops, ops_h):
    await make_applicant(db, requested_by=ops.id)
    await make_applicant(db, status=CaregiverStatus.APPLIED, requested_by=ops.id)
    await make_facility(db, "Pending Clinic", *ENUGU, status="pending")
    now = datetime.now(UTC)
    overdue = Incident(
        reported_by=ops.id,
        category=IncidentCategory.SAFETY,
        severity=Severity.CRITICAL,
        status=IncidentStatus.OPEN,
        description="Caregiver did not arrive for the overnight shift.",
        response_due_at=now - timedelta(hours=2),
    )
    answered = Incident(
        reported_by=ops.id,
        category=IncidentCategory.OTHER,
        severity=Severity.LOW,
        status=IncidentStatus.INVESTIGATING,
        description="Question about the invoice amount on my booking.",
        response_due_at=now + timedelta(hours=10),
        first_response_at=now,
    )
    db.add_all([overdue, answered])
    await db.commit()

    body = (await api.get("/ops/dashboard?period_days=30", headers=ops_h)).json()
    assert body["period_days"] == 30
    assert body["caregivers"]["awaiting_decision"] == 2
    assert body["caregivers"]["by_status"]["verifying"] == 1
    assert body["facilities"]["by_status"]["pending"] == 1
    assert body["facilities"]["verification_queue"] == 1
    assert body["incidents"]["open"] == 2
    assert body["incidents"]["overdue"] == 1
    assert body["incidents"]["avg_first_response_minutes"] is not None


async def test_dashboard_period_bounds(api, ops_h):
    assert (await api.get("/ops/dashboard?period_days=0", headers=ops_h)).status_code == 422
    assert (await api.get("/ops/dashboard?period_days=91", headers=ops_h)).status_code == 422


# ================================================================ audit log
async def test_audit_log_search_and_paging(api, db, ops, ops_h):
    target = uuid.uuid4()
    for i in range(5):
        write_audit(
            db,
            action="escrow.release" if i % 2 else "passport.view",
            actor_id=ops.id,
            entity_type="booking",
            entity_id=target if i < 3 else uuid.uuid4(),
            metadata={"n": i},
        )
    await db.commit()

    first = await api.get("/audit-log?limit=2", headers=ops_h)
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 2 and page["next_before_id"] is not None
    assert page["items"][0]["id"] > page["items"][1]["id"]  # newest first
    assert page["items"][0]["actor_name"] == "Ifeoma Ops"
    assert page["items"][0]["actor_role"] == "ops"
    assert page["items"][0]["metadata"] == {"n": 4}

    second = (
        await api.get(f"/audit-log?limit=2&before_id={page['next_before_id']}", headers=ops_h)
    ).json()
    assert all(item["id"] < page["next_before_id"] for item in second["items"])

    exact = (await api.get("/audit-log?action=passport.view", headers=ops_h)).json()["items"]
    assert {item["action"] for item in exact} == {"passport.view"} and len(exact) == 3

    prefix = (await api.get("/audit-log?action=escrow.", headers=ops_h)).json()["items"]
    assert {item["action"] for item in prefix} == {"escrow.release"} and len(prefix) == 2

    by_entity = (await api.get(f"/audit-log?entity_id={target}", headers=ops_h)).json()["items"]
    assert len(by_entity) == 3


async def test_reading_the_audit_log_is_audited(api, db, ops, ops_h):
    await api.get("/audit-log?entity_type=booking", headers=ops_h)
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "audit_log.view"))
    assert entry.actor_id == ops.id
    assert entry.meta["filters"] == {"entity_type": "booking"}


async def test_audit_log_like_wildcards_are_literal(api, db, ops, ops_h):
    write_audit(db, action="escrowXrelease", actor_id=ops.id, entity_type="booking")
    await db.commit()
    items = (await api.get("/audit-log?action=escrow_.", headers=ops_h)).json()["items"]
    assert items == []
