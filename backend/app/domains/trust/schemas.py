"""trust Pydantic schemas: incidents."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domains.trust.models import IncidentCategory, IncidentStatus, Severity

Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=10, max_length=5000)
]
Resolution = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=5000)]


class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: uuid.UUID | None = Field(
        default=None, description="The booking it's about. Needed for a dispute."
    )
    category: IncidentCategory
    severity: Severity = Severity.MEDIUM
    description: Description
    is_dispute: bool = Field(
        default=False,
        description="Hold the booking's payment while CareKin reviews it (TS-5).",
    )
    against_user_id: uuid.UUID | None = Field(
        default=None,
        description="Who it's about. Defaults to the other side of the booking.",
    )

    @model_validator(mode="after")
    def _dispute_needs_booking(self) -> IncidentCreate:
        if self.is_dispute and self.booking_id is None:
            raise ValueError("A dispute must name the booking it's about")
        return self


class IncidentUpdate(BaseModel):
    """Ops only. Send just the fields that change."""

    model_config = ConfigDict(extra="forbid")

    status: IncidentStatus | None = None
    severity: Severity | None = None
    assigned_to: uuid.UUID | None = None
    resolution: Resolution | None = None
    is_dispute: bool | None = Field(
        default=None,
        description="true turns an open incident into a dispute and holds the payment. "
        "To end a dispute, resolve or dismiss the incident.",
    )

    @model_validator(mode="after")
    def _something_to_change(self) -> IncidentUpdate:
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change")
        return self


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    booking_id: uuid.UUID | None
    reported_by: uuid.UUID | None
    against_user_id: uuid.UUID | None
    category: IncidentCategory
    severity: Severity
    is_dispute: bool
    status: IncidentStatus
    description: str
    response_due_at: datetime
    first_response_at: datetime | None
    assigned_to: uuid.UUID | None
    resolved_at: datetime | None
    resolution: str | None
    created_at: datetime
    updated_at: datetime
