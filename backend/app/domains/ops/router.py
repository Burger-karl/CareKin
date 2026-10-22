"""ops routes: the operations console (build plan Day 13). Ops users only.

GET  /ops/dashboard                     platform metrics and pilot numbers (OP-1)
GET  /ops/caregivers/pending            vetting queue (OP-1)
POST /ops/caregivers/{id}/approve       human approval or rejection (OP-1, TS-1)
GET  /ops/facilities/pending            facility verification queue (OP-3)
POST /ops/facilities/{id}/verify        verify a listing and stamp last_verified_at (OP-3)
GET  /audit-log                         search the audit log (OP-5)

Incidents (TS-2, OP-2) live in the trust domain: /incidents.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.permissions import UserRole, require_roles
from app.database import DbSession
from app.domains.auth.models import User
from app.domains.caregivers.models import CaregiverStatus
from app.domains.ops import schemas as s
from app.domains.ops import service

router = APIRouter(prefix="/ops", tags=["ops"])
audit_router = APIRouter(prefix="/audit-log", tags=["ops"])

OpsUser = Annotated[User, require_roles(UserRole.OPS)]


def get_actor(request: Request, user: OpsUser) -> service.Actor:
    return service.Actor(user_id=user.id, request=request)


Actor = Annotated[service.Actor, Depends(get_actor)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/dashboard", response_model=s.OpsDashboard, summary="Platform metrics")
async def dashboard(
    db: DbSession,
    _ops: OpsUser,
    period_days: Annotated[int, Query(ge=1, le=90, description="Window for the period figures")] = 7,
) -> s.OpsDashboard:
    """Queues, bookings, money and incidents. Doubles as pilot instrumentation: booking
    time, caregiver hours and family spend for the unit economics (Topic 9)."""
    return await service.dashboard(db, period_days)


@router.get(
    "/caregivers/pending",
    response_model=list[s.PendingCaregiver],
    summary="Caregiver vetting queue",
)
async def pending_caregivers(
    db: DbSession,
    _ops: OpsUser,
    status: CaregiverStatus | None = Query(
        default=None, description="applied, verifying or verified. Default: all three."
    ),
    ready_only: bool = Query(default=False, description="Only caregivers ops can approve now"),
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[s.PendingCaregiver]:
    """Caregivers waiting for a decision, longest waiting first, each with what still
    blocks approval."""
    return await service.pending_caregivers(
        db, status=status, ready_only=ready_only, limit=limit, offset=offset
    )


@router.post(
    "/caregivers/{caregiver_id}/approve",
    response_model=s.CaregiverDecisionOut,
    summary="Approve or reject a caregiver",
)
async def decide_caregiver(
    caregiver_id: uuid.UUID, data: s.CaregiverDecision, db: DbSession, actor: Actor
) -> s.CaregiverDecisionOut:
    """Approving needs two guarantors, passed NIN and background checks, and for nurses a
    verified licence (verify it in the same request). Send decision=reject with a reason
    to turn an applicant down."""
    return await service.decide_caregiver(db, actor, caregiver_id, data)


@router.get(
    "/facilities/pending",
    response_model=list[s.PendingFacility],
    summary="Facility verification queue",
)
async def pending_facilities(
    db: DbSession,
    _ops: OpsUser,
    include_stale: bool = Query(
        default=True, description="Also list verified facilities due for a re-check"
    ),
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[s.PendingFacility]:
    return await service.pending_facilities(
        db, include_stale=include_stale, limit=limit, offset=offset
    )


@router.post(
    "/facilities/{facility_id}/verify",
    response_model=s.FacilityVerificationOut,
    summary="Verify a facility listing",
)
async def verify_facility(
    facility_id: uuid.UUID, data: s.FacilityVerification, db: DbSession, actor: Actor
) -> s.FacilityVerificationOut:
    """Record the result of checking a listing. Stamps last_verified_at, which every
    listing shows. A facility appears on the crisis screen only once it, its location and
    a crisis service are verified."""
    return await service.verify_facility(db, actor, facility_id, data)


@audit_router.get("", response_model=s.AuditPage, summary="Search the audit log")
async def search_audit_log(
    db: DbSession,
    actor: Actor,
    actor_id: uuid.UUID | None = None,
    action: str | None = Query(
        default=None,
        max_length=64,
        description='Exact action, or a prefix ending in "." (e.g. "escrow.")',
    ),
    entity_type: str | None = Query(default=None, max_length=64),
    entity_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = Query(default=None, ge=1, description="next_before_id from the last page"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> s.AuditPage:
    """Newest first. Reading the log is itself recorded as audit_log.view."""
    return await service.search_audit_log(
        db,
        actor,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        since=since,
        until=until,
        before_id=before_id,
        limit=limit,
    )
