"""ops business logic: the operations console (Day 13).

- Caregiver vetting queue and the human approval decision (OP-1, TS-1).
- Facility verification queue and the verification stamp (OP-3, FF-3).
- Platform dashboard, also the pilot's instrumentation (OP-1, Topic 9).
- Audit log search (OP-5).

Every endpoint here is ops-only (enforced in the router). Every decision writes an audit
entry in the same transaction as the change it records.

Approval rule (TS-1): a caregiver can be approved only after the vetting has run and
passed: at least two guarantors, the latest NIN check and the latest background check
both passed, and for licensed nurses a verified nursing licence. Ops can verify or reject
documents in the same request. Nothing here approves anyone automatically.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.audit import AuditLog, write_audit
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.permissions import UserRole
from app.domains.auth.models import User
from app.domains.bookings.models import Booking, BookingStatus, Shift, ShiftStatus
from app.domains.caregivers.models import (
    Caregiver,
    CaregiverStatus,
    CaregiverTier,
    CertType,
    CheckStatus,
    CheckType,
    ReviewStatus,
)
from app.domains.caregivers.schemas import CertificationOut
from app.domains.crisis.models import CrisisEvent, CrisisStatus
from app.domains.facilities import service as facilities
from app.domains.facilities.models import Facility, FacilityService, VerificationStatus
from app.domains.facilities.schemas import FacilityServiceRead
from app.domains.ops import schemas as s
from app.domains.payments.models import EscrowTransaction, Payment
from app.domains.trust.models import OPEN_STATUSES, Incident
from app.integrations.escrow import EscrowStatus
from app.integrations.payments import PaymentStatus

# Caregivers still waiting for a human decision (matches the partial index on caregivers).
QUEUE_STATUSES = (CaregiverStatus.APPLIED, CaregiverStatus.VERIFYING, CaregiverStatus.VERIFIED)
# Approval needs the checks to have been run (POST /caregivers/{id}/verify).
APPROVABLE_STATUSES = (CaregiverStatus.VERIFYING, CaregiverStatus.VERIFIED)
MIN_GUARANTORS = 2


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: uuid.UUID
    request: Request | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(
    db: AsyncSession,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    write_audit(
        db,
        action=action,
        actor_id=actor.user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        request=actor.request,
        metadata=metadata,
    )


# ======================================================================= caregivers
def _latest_checks(caregiver: Caregiver) -> s.CheckSummary:
    latest: dict[CheckType, tuple[datetime, CheckStatus]] = {}
    for check in caregiver.verification_checks:
        seen = latest.get(check.check_type)
        if seen is None or check.created_at >= seen[0]:
            latest[check.check_type] = (check.created_at, check.status)
    return s.CheckSummary(
        nin=latest[CheckType.NIN][1] if CheckType.NIN in latest else None,
        background=latest[CheckType.BACKGROUND][1] if CheckType.BACKGROUND in latest else None,
    )


def _blockers(caregiver: Caregiver) -> list[str]:
    """What stops ops from approving this caregiver right now (TS-1)."""
    blockers: list[str] = []
    if caregiver.status is CaregiverStatus.APPLIED:
        blockers.append("verification has not been run (POST /caregivers/{id}/verify)")
    elif caregiver.status not in APPROVABLE_STATUSES:
        blockers.append(f"caregiver is {caregiver.status.value}")
    if len(caregiver.guarantors) < MIN_GUARANTORS:
        blockers.append(f"needs {MIN_GUARANTORS} guarantors, has {len(caregiver.guarantors)}")
    checks = _latest_checks(caregiver)
    for name, status in (("NIN", checks.nin), ("background", checks.background)):
        if status is None:
            blockers.append(f"{name} check has not been run")
        elif status is not CheckStatus.PASSED:
            blockers.append(f"{name} check {status.value}")
    if caregiver.tier is CaregiverTier.LICENSED_NURSE and not any(
        c.cert_type is CertType.NURSING_LICENCE and c.status is ReviewStatus.VERIFIED
        for c in caregiver.certifications
    ):
        blockers.append("licensed nurse needs a verified nursing licence")
    return blockers


def _cert_counts(caregiver: Caregiver) -> s.CertificationCounts:
    statuses = [c.status for c in caregiver.certifications]
    return s.CertificationCounts(
        total=len(statuses),
        pending=statuses.count(ReviewStatus.PENDING),
        verified=statuses.count(ReviewStatus.VERIFIED),
        rejected=statuses.count(ReviewStatus.REJECTED),
    )


def _with_vetting():  # noqa: ANN202
    return (
        selectinload(Caregiver.guarantors),
        selectinload(Caregiver.certifications),
        selectinload(Caregiver.verification_checks),
    )


async def pending_caregivers(
    db: AsyncSession,
    *,
    status: CaregiverStatus | None = None,
    ready_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[s.PendingCaregiver]:
    """The vetting queue, longest waiting first."""
    statuses = (status,) if status is not None else QUEUE_STATUSES
    rows = (
        await db.execute(
            select(Caregiver, User)
            .join(User, User.id == Caregiver.user_id)
            .where(Caregiver.status.in_(statuses))
            .options(*_with_vetting())
            .order_by(Caregiver.created_at, Caregiver.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    now = _now()
    queue: list[s.PendingCaregiver] = []
    for caregiver, user in rows:
        blockers = _blockers(caregiver)
        if ready_only and blockers:
            continue
        queue.append(
            s.PendingCaregiver(
                id=caregiver.id,
                user_id=user.id,
                full_name=user.full_name,
                phone=user.phone,
                tier=caregiver.tier,
                status=caregiver.status,
                applied_at=caregiver.created_at,
                waiting_days=(now - caregiver.created_at).days,
                guarantor_count=len(caregiver.guarantors),
                checks=_latest_checks(caregiver),
                certifications=_cert_counts(caregiver),
                ready_to_approve=not blockers,
                blockers=blockers,
            )
        )
    return queue


async def decide_caregiver(
    db: AsyncSession, actor: Actor, caregiver_id: uuid.UUID, data: s.CaregiverDecision
) -> s.CaregiverDecisionOut:
    """Approve or reject a caregiver: always a human ops decision (TS-1)."""
    caregiver = await db.scalar(
        select(Caregiver)
        .where(Caregiver.id == caregiver_id)
        .options(*_with_vetting())
        .with_for_update(of=Caregiver)
        .execution_options(populate_existing=True)
    )
    if caregiver is None:
        raise NotFoundError("Caregiver not found.", code="caregiver_not_found")
    if caregiver.status not in QUEUE_STATUSES:
        raise ConflictError(
            f"This caregiver is already {caregiver.status.value}.",
            code="caregiver_not_pending",
            details={"status": caregiver.status.value},
        )

    by_id = {c.id: c for c in caregiver.certifications}
    unknown = [str(d.id) for d in data.certifications if d.id not in by_id]
    if unknown:
        raise BadRequestError(
            "These certifications do not belong to this caregiver.",
            code="certification_not_found",
            details={"ids": unknown},
        )
    now = _now()
    reviewed: dict[str, str] = {}
    for decision in data.certifications:
        cert = by_id[decision.id]
        cert.status = ReviewStatus(decision.status)
        cert.verified_by = actor.user_id
        cert.verified_at = now
        reviewed[str(cert.id)] = cert.status.value

    if data.decision == "approve":
        blockers = _blockers(caregiver)
        if blockers:
            raise ConflictError(
                "This caregiver cannot be approved yet.",
                code="caregiver_not_ready",
                details={"blockers": blockers},
            )
        caregiver.status = CaregiverStatus.APPROVED
        caregiver.approved_by = actor.user_id
        caregiver.approved_at = now
        action, metadata = "caregiver.approve", {"tier": caregiver.tier.value}
    else:
        caregiver.status = CaregiverStatus.REJECTED
        action, metadata = "caregiver.reject", {"reason": data.reason}
    if reviewed:
        metadata["certifications"] = reviewed
    _audit(db, actor, action, "caregiver", caregiver.id, metadata)
    await db.commit()

    certs = list(by_id.values())
    for cert in certs:
        await db.refresh(cert)
    return s.CaregiverDecisionOut(
        caregiver_id=caregiver.id,
        status=caregiver.status,
        approved_at=caregiver.approved_at,
        approved_by=caregiver.approved_by,
        certifications=[
            CertificationOut.model_validate(c) for c in sorted(certs, key=lambda c: c.created_at)
        ],
    )


# ======================================================================= facilities
def _stale_cutoff(now: datetime) -> datetime:
    return now - timedelta(days=get_settings().FACILITY_REVERIFY_AFTER_DAYS)


def _facility_reasons(
    facility: Facility, services: list[FacilityService], cutoff: datetime
) -> list[s.FacilityReason]:
    reasons: list[s.FacilityReason] = []
    if facility.verification_status is VerificationStatus.PENDING:
        reasons.append("unverified")
    if facility.location is None:
        reasons.append("no_location")
    elif not facility.location_verified:
        reasons.append("location_unconfirmed")
    if any(sv.verification_status is VerificationStatus.PENDING for sv in services):
        reasons.append("services_pending")
    if facility.verification_status is VerificationStatus.VERIFIED and (
        facility.last_verified_at is None or facility.last_verified_at < cutoff
    ):
        reasons.append("stale")
    return reasons


def _needs_check(cutoff: datetime, include_stale: bool) -> Any:
    """SQL for: this listing belongs in the verification queue. Rejected listings never do."""
    pending_service = exists().where(
        FacilityService.facility_id == Facility.id,
        FacilityService.verification_status == VerificationStatus.PENDING,
    )
    conditions = [
        Facility.verification_status == VerificationStatus.PENDING,
        and_(
            Facility.verification_status == VerificationStatus.VERIFIED,
            or_(Facility.location.is_(None), Facility.location_verified.is_(False)),
        ),
        and_(Facility.verification_status == VerificationStatus.VERIFIED, pending_service),
    ]
    if include_stale:
        conditions.append(
            and_(
                Facility.verification_status == VerificationStatus.VERIFIED,
                or_(Facility.last_verified_at.is_(None), Facility.last_verified_at < cutoff),
            )
        )
    return or_(*conditions)


async def pending_facilities(
    db: AsyncSession, *, include_stale: bool = True, limit: int = 50, offset: int = 0
) -> list[s.PendingFacility]:
    """Listings that need a check: new, incomplete, or not checked for a while.

    Never-checked listings come first, then the longest since their last check.
    """
    now = _now()
    cutoff = _stale_cutoff(now)
    facilities_page = list(
        await db.scalars(
            select(Facility)
            .where(_needs_check(cutoff, include_stale))
            .options(selectinload(Facility.services))
            .order_by(
                Facility.last_verified_at.asc().nulls_first(), Facility.created_at, Facility.id
            )
            .limit(limit)
            .offset(offset)
        )
    )
    queue: list[s.PendingFacility] = []
    for facility in facilities_page:
        services = sorted(facility.services, key=lambda sv: sv.service_type)
        reasons = _facility_reasons(facility, services, cutoff)
        if not include_stale:
            reasons = [r for r in reasons if r != "stale"]
        queue.append(
            s.PendingFacility(
                id=facility.id,
                name=facility.name,
                address=facility.address,
                phone_number=facility.phone_number,
                claimed_by=facility.claimed_by,
                verification_status=facility.verification_status,
                has_location=facility.location is not None,
                location_verified=facility.location_verified,
                last_verified_at=facility.last_verified_at,
                created_at=facility.created_at,
                services=[s.PendingService.model_validate(sv) for sv in services],
                reasons=reasons,
            )
        )
    return queue


async def verify_facility(
    db: AsyncSession, actor: Actor, facility_id: uuid.UUID, data: s.FacilityVerification
) -> s.FacilityVerificationOut:
    """Record ops' check of a listing and stamp last_verified_at (OP-3, FF-3).

    The stamp is set for rejections too: it records when ops last looked.
    """
    facility = await db.scalar(
        select(Facility)
        .where(Facility.id == facility_id)
        .options(selectinload(Facility.services))
        .with_for_update(of=Facility)
        .execution_options(populate_existing=True)
    )
    if facility is None:
        raise NotFoundError("Facility not found.", code="facility_not_found")
    services = {sv.id: sv for sv in facility.services}

    if data.services is not None:
        unknown = [str(d.id) for d in data.services if d.id not in services]
        if unknown:
            raise BadRequestError(
                "These services do not belong to this facility.",
                code="facility_service_not_found",
                details={"ids": unknown},
            )
    verifying = data.decision is VerificationStatus.VERIFIED
    location_verified = data.location_verified
    if location_verified is None:
        location_verified = verifying and facility.location is not None
    if location_verified and facility.location is None:
        raise ConflictError(
            "This facility has no coordinates to confirm. Add them first.",
            code="facility_has_no_location",
        )

    changed: dict[str, int] = {"services_verified": 0, "services_rejected": 0}
    if data.services is not None:
        decisions = {d.id: VerificationStatus(d.status) for d in data.services}
    elif verifying:
        decisions = {
            sv_id: VerificationStatus.VERIFIED
            for sv_id, sv in services.items()
            if sv.verification_status is VerificationStatus.PENDING
        }
    else:
        decisions = {}
    for sv_id, status in decisions.items():
        services[sv_id].verification_status = status
        key = "services_verified" if status is VerificationStatus.VERIFIED else "services_rejected"
        changed[key] += 1

    now = _now()
    facility.verification_status = VerificationStatus(data.decision)
    facility.location_verified = bool(location_verified)
    facility.last_verified_at = now
    facility.verified_by = actor.user_id
    metadata: dict[str, Any] = {
        "decision": facility.verification_status.value,
        "location_verified": facility.location_verified,
        **changed,
    }
    if data.note:
        metadata["note"] = data.note
    _audit(db, actor, "facility.verify", "facility", facility.id, metadata)
    await db.commit()
    await db.refresh(facility)
    service_rows = []
    for sv in sorted(services.values(), key=lambda sv: sv.service_type):
        await db.refresh(sv)
        service_rows.append(FacilityServiceRead.model_validate(sv))
    return s.FacilityVerificationOut(
        facility=await facilities.facility_to_read(db, facility), services=service_rows
    )


# ======================================================================= dashboard
async def _counts_by(db: AsyncSession, column: Any, enum_cls: Any) -> dict[str, int]:
    """Count rows by an enum column, with every value present (zero if none)."""
    found = dict((await db.execute(select(column, func.count()).group_by(column))).all())
    return {member.value: int(found.get(member, 0)) for member in enum_cls}


async def _money(db: AsyncSession, stmt: Any) -> list[s.Money]:
    return [
        s.Money(currency=currency, amount_minor=int(amount or 0), count=int(count))
        for currency, amount, count in (await db.execute(stmt)).all()
    ]


async def dashboard(db: AsyncSession, period_days: int = 7) -> s.OpsDashboard:
    """Platform numbers for the ops console and the pilot's unit economics."""
    now = _now()
    since = now - timedelta(days=period_days)

    by_role = await _counts_by(db, User.role, UserRole)
    users = s.UsersBlock(
        total=sum(by_role.values()),
        by_role=by_role,
        new_in_period=await db.scalar(select(func.count()).where(User.created_at >= since)) or 0,
    )

    caregiver_status = await _counts_by(db, Caregiver.status, CaregiverStatus)
    caregivers = s.CaregiversBlock(
        by_status=caregiver_status,
        awaiting_decision=sum(caregiver_status[st.value] for st in QUEUE_STATUSES),
    )

    cutoff = _stale_cutoff(now)
    facilities_block = s.FacilitiesBlock(
        by_status=await _counts_by(db, Facility.verification_status, VerificationStatus),
        verification_queue=await db.scalar(
            select(func.count()).select_from(Facility).where(_needs_check(cutoff, True))
        )
        or 0,
        stale=await db.scalar(
            select(func.count())
            .select_from(Facility)
            .where(
                Facility.verification_status == VerificationStatus.VERIFIED,
                or_(Facility.last_verified_at.is_(None), Facility.last_verified_at < cutoff),
            )
        )
        or 0,
    )

    accept_minutes = await db.scalar(
        select(func.avg(func.extract("epoch", Booking.accepted_at - Booking.created_at) / 60)).where(
            Booking.accepted_at.is_not(None), Booking.accepted_at >= since
        )
    )
    bookings = s.BookingsBlock(
        by_status=await _counts_by(db, Booking.status, BookingStatus),
        created_in_period=await db.scalar(
            select(func.count()).select_from(Booking).where(Booking.created_at >= since)
        )
        or 0,
        completed_in_period=await db.scalar(
            select(func.count(func.distinct(Shift.booking_id)))
            .join(Booking, Booking.id == Shift.booking_id)
            .where(Booking.status == BookingStatus.COMPLETED, Shift.actual_end >= since)
        )
        or 0,
        avg_minutes_to_accept=round(float(accept_minutes), 1) if accept_minutes is not None else None,
    )

    hours, shifts = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(func.extract("epoch", Shift.actual_end - Shift.actual_start)), 0
                )
                / 3600,
                func.count(),
            ).where(
                Shift.status == ShiftStatus.COMPLETED,
                Shift.actual_start.is_not(None),
                Shift.actual_end >= since,
            )
        )
    ).one()
    pilot = s.PilotBlock(
        caregiver_hours=round(float(hours or 0), 2),
        completed_shifts=int(shifts),
        family_spend=await _money(
            db,
            select(Payment.currency, func.sum(Payment.amount_minor), func.count())
            .where(Payment.status == PaymentStatus.SUCCEEDED, Payment.paid_at >= since)
            .group_by(Payment.currency)
            .order_by(Payment.currency),
        ),
    )

    def _escrow(status: EscrowStatus) -> Any:
        return (
            select(
                EscrowTransaction.currency,
                func.sum(EscrowTransaction.amount_minor),
                func.count(),
            )
            .where(EscrowTransaction.status == status)
            .group_by(EscrowTransaction.currency)
            .order_by(EscrowTransaction.currency)
        )

    escrow = s.EscrowBlock(
        held=await _money(db, _escrow(EscrowStatus.HELD)),
        on_hold_dispute=await _money(db, _escrow(EscrowStatus.ON_HOLD_DISPUTE)),
    )

    open_incident = Incident.status.in_(OPEN_STATUSES)
    response_minutes = await db.scalar(
        select(
            func.avg(func.extract("epoch", Incident.first_response_at - Incident.created_at) / 60)
        ).where(Incident.first_response_at >= since)
    )
    incidents = s.IncidentsBlock(
        open=await db.scalar(select(func.count()).select_from(Incident).where(open_incident)) or 0,
        overdue=await db.scalar(
            select(func.count())
            .select_from(Incident)
            .where(
                open_incident,
                Incident.first_response_at.is_(None),
                Incident.response_due_at < now,
            )
        )
        or 0,
        open_disputes=await db.scalar(
            select(func.count())
            .select_from(Incident)
            .where(open_incident, Incident.is_dispute.is_(True))
        )
        or 0,
        avg_first_response_minutes=(
            round(float(response_minutes), 1) if response_minutes is not None else None
        ),
    )

    crisis = s.CrisisBlock(
        active=await db.scalar(
            select(func.count())
            .select_from(CrisisEvent)
            .where(CrisisEvent.status == CrisisStatus.ACTIVE)
        )
        or 0,
        in_period=await db.scalar(
            select(func.count()).select_from(CrisisEvent).where(CrisisEvent.triggered_at >= since)
        )
        or 0,
    )

    return s.OpsDashboard(
        generated_at=now,
        period_days=period_days,
        period_start=since,
        users=users,
        caregivers=caregivers,
        facilities=facilities_block,
        bookings=bookings,
        pilot=pilot,
        escrow=escrow,
        incidents=incidents,
        crisis=crisis,
    )


# ======================================================================= audit log
async def search_audit_log(
    db: AsyncSession,
    actor: Actor,
    *,
    actor_id: uuid.UUID | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> s.AuditPage:
    """Newest first. `action` ending in "." matches a prefix, e.g. "escrow." for every
    escrow action. Reading the log is itself audited (OP-5)."""
    query = select(AuditLog, User.full_name, User.role).outerjoin(
        User, User.id == AuditLog.actor_id
    )
    if actor_id is not None:
        query = query.where(AuditLog.actor_id == actor_id)
    if action:
        if action.endswith("."):
            escaped = action.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(AuditLog.action.like(escaped + "%", escape="\\"))
        else:
            query = query.where(AuditLog.action == action)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        query = query.where(AuditLog.entity_id == entity_id)
    if since is not None:
        query = query.where(AuditLog.created_at >= since)
    if until is not None:
        query = query.where(AuditLog.created_at < until)
    if before_id is not None:
        query = query.where(AuditLog.id < before_id)
    rows = (await db.execute(query.order_by(AuditLog.id.desc()).limit(limit + 1))).all()

    items = [
        s.AuditEntry.model_validate(entry).model_copy(
            update={"actor_name": name, "actor_role": role.value if role else None}
        )
        for entry, name, role in rows[:limit]
    ]
    filters = {
        key: str(value)
        for key, value in {
            "actor_id": actor_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "since": since,
            "until": until,
            "before_id": before_id,
        }.items()
        if value is not None
    }
    _audit(db, actor, "audit_log.view", "audit_log", None, {"filters": filters, "rows": len(items)})
    await db.commit()
    return s.AuditPage(
        items=items, next_before_id=items[-1].id if len(rows) > limit and items else None
    )
