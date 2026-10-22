"""ops Pydantic schemas: the operations console (Day 13: OP-1, OP-3, OP-5, TS-1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.domains.caregivers.models import CaregiverStatus, CaregiverTier, CheckStatus, ReviewStatus
from app.domains.caregivers.schemas import CertificationOut
from app.domains.facilities.models import ServiceAvailability, VerificationStatus
from app.domains.facilities.schemas import FacilityRead, FacilityServiceRead

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ================================================================ caregivers
class CheckSummary(BaseModel):
    """The latest result of each vetting check. Null: never run."""

    nin: CheckStatus | None
    background: CheckStatus | None


class CertificationCounts(BaseModel):
    total: int
    pending: int
    verified: int
    rejected: int


class PendingCaregiver(BaseModel):
    """One row in the vetting queue."""

    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    phone: str
    tier: CaregiverTier
    status: CaregiverStatus
    applied_at: datetime
    waiting_days: int
    guarantor_count: int
    checks: CheckSummary
    certifications: CertificationCounts
    ready_to_approve: bool = Field(description="True when nothing in `blockers` stands in the way")
    blockers: list[str] = Field(description="What must happen before ops can approve")


class CertificationDecision(_Strict):
    id: uuid.UUID
    status: Literal[ReviewStatus.VERIFIED, ReviewStatus.REJECTED]


class CaregiverDecision(_Strict):
    """Approve or reject a caregiver. Approving needs the vetting to be complete (TS-1)."""

    decision: Literal["approve", "reject"] = "approve"
    reason: Reason | None = Field(
        default=None, description="Required when rejecting. Kept in the audit log."
    )
    certifications: list[CertificationDecision] = Field(
        default_factory=list,
        description="Documents ops checked in this review, e.g. a nurse's licence.",
    )

    @model_validator(mode="after")
    def _reject_needs_reason(self) -> CaregiverDecision:
        if self.decision == "reject" and not self.reason:
            raise ValueError("Give a reason when rejecting a caregiver")
        ids = [c.id for c in self.certifications]
        if len(ids) != len(set(ids)):
            raise ValueError("Each certification can only appear once")
        return self


class CaregiverDecisionOut(BaseModel):
    caregiver_id: uuid.UUID
    status: CaregiverStatus
    approved_at: datetime | None
    approved_by: uuid.UUID | None
    certifications: list[CertificationOut]


# ================================================================ facilities
class PendingService(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_type: str
    availability: ServiceAvailability
    verification_status: VerificationStatus


FacilityReason = Literal[
    "unverified", "no_location", "location_unconfirmed", "services_pending", "stale"
]


class PendingFacility(BaseModel):
    """One row in the facility verification queue."""

    id: uuid.UUID
    name: str
    address: str
    phone_number: str
    claimed_by: uuid.UUID | None
    verification_status: VerificationStatus
    has_location: bool
    location_verified: bool
    last_verified_at: datetime | None
    created_at: datetime
    services: list[PendingService]
    reasons: list[FacilityReason] = Field(description="Why the listing needs a check")


class ServiceDecision(_Strict):
    id: uuid.UUID
    status: Literal[VerificationStatus.VERIFIED, VerificationStatus.REJECTED]


class FacilityVerification(_Strict):
    """The result of ops checking a listing (usually by phone or a visit)."""

    decision: Literal[VerificationStatus.VERIFIED, VerificationStatus.REJECTED]
    location_verified: bool | None = Field(
        default=None,
        description="Coordinates confirmed. Default: true when verifying a facility "
        "that has coordinates.",
    )
    services: list[ServiceDecision] | None = Field(
        default=None,
        description="Per-service results. Omitted when verifying: every pending service "
        "is marked verified.",
    )
    note: Reason | None = Field(default=None, description="Required when rejecting.")

    @model_validator(mode="after")
    def _check(self) -> FacilityVerification:
        if self.decision is VerificationStatus.REJECTED and not self.note:
            raise ValueError("Say why the listing is rejected (note)")
        if self.services is not None:
            ids = [s.id for s in self.services]
            if len(ids) != len(set(ids)):
                raise ValueError("Each service can only appear once")
        return self


class FacilityVerificationOut(BaseModel):
    facility: FacilityRead
    services: list[FacilityServiceRead]


# ================================================================ dashboard
class Money(BaseModel):
    currency: str
    amount_minor: int
    count: int


class UsersBlock(BaseModel):
    total: int
    by_role: dict[str, int]
    new_in_period: int


class CaregiversBlock(BaseModel):
    by_status: dict[str, int]
    awaiting_decision: int


class FacilitiesBlock(BaseModel):
    by_status: dict[str, int]
    verification_queue: int
    stale: int


class BookingsBlock(BaseModel):
    by_status: dict[str, int]
    created_in_period: int
    completed_in_period: int
    avg_minutes_to_accept: float | None = Field(
        description="Booking request to caregiver acceptance, for bookings accepted in the period"
    )


class PilotBlock(BaseModel):
    """Unit-economics inputs for the pilot (Topic 9)."""

    caregiver_hours: float = Field(description="Hours worked in completed shifts in the period")
    completed_shifts: int
    family_spend: list[Money] = Field(description="Succeeded payments in the period")


class EscrowBlock(BaseModel):
    held: list[Money]
    on_hold_dispute: list[Money]


class IncidentsBlock(BaseModel):
    open: int
    overdue: int = Field(description="Open, no human response yet, past the response target")
    open_disputes: int
    avg_first_response_minutes: float | None = Field(
        description="For incidents first answered in the period"
    )


class CrisisBlock(BaseModel):
    active: int
    in_period: int


class OpsDashboard(BaseModel):
    generated_at: datetime
    period_days: int
    period_start: datetime
    users: UsersBlock
    caregivers: CaregiversBlock
    facilities: FacilitiesBlock
    bookings: BookingsBlock
    pilot: PilotBlock
    escrow: EscrowBlock
    incidents: IncidentsBlock
    crisis: CrisisBlock


# ================================================================ audit log
def _ip_to_str(value: Any) -> str | None:
    return None if value is None else str(value)


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    actor_id: uuid.UUID | None
    actor_name: str | None = None
    actor_role: str | None = None
    action: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    ip_address: Annotated[str | None, BeforeValidator(_ip_to_str)]
    user_agent: str | None
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="meta")


class AuditPage(BaseModel):
    items: list[AuditEntry]
    next_before_id: int | None = Field(
        description="Pass as before_id to get the next (older) page. Null on the last page."
    )
