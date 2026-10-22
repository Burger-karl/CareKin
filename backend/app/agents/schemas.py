"""Agent API schemas (Day 14): POST /agents/match."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.domains.bookings.models import BookingType
from app.domains.bookings.schemas import MAX_BOOKING, MIN_BOOKING
from app.domains.caregivers.models import CaregiverTier
from app.domains.caregivers.schemas import CaregiverMatch
from app.domains.patients.schemas import GeoPoint

MAX_MESSAGE_LENGTH = 1000


class AgentMatchRequest(BaseModel):
    """What the family typed, plus the booking the app already knows.

    The patient and time slot come from the app's own fields, never from the model:
    the model only reads `message` for preferences such as language or tier.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_LENGTH,
        examples=["Someone who speaks Igbo and is good with children"],
    )
    session_id: uuid.UUID | None = Field(
        default=None, description="Continue an earlier conversation; omit to start one."
    )
    patient_id: uuid.UUID
    booking_type: BookingType
    starts_at: AwareDatetime = Field(description="With a timezone, e.g. 2026-11-02T08:00:00+01:00.")
    ends_at: AwareDatetime
    service_location: GeoPoint | None = Field(
        default=None, description="Where care happens. Defaults to the patient's home location."
    )
    limit: int = Field(default=5, ge=1, le=10)
    channel: Literal["web", "mobile"] = "web"

    @model_validator(mode="after")
    def _check(self) -> AgentMatchRequest:
        length = self.ends_at - self.starts_at
        if length < MIN_BOOKING:
            raise ValueError("The time slot must be at least 1 hour long and end after it starts")
        if length > MAX_BOOKING:
            raise ValueError("The time slot can't be longer than 31 days")
        return self


class AgentMatchOut(BaseModel):
    session_id: uuid.UUID
    status: Literal["answered", "handed_off"]
    reply: str = Field(description="What to show the family.")
    reply_source: Literal["model", "fallback", "handoff"] = Field(
        description="model: written by the AI; fallback: built without it (the AI was down, "
        "slow or off-topic); handoff: a fixed message, a person will follow up."
    )
    results: list[CaregiverMatch] = Field(
        description="Ranked caregivers. Empty after a handoff. The agent never books."
    )
    guardian_approval_required: bool
    min_tier_applied: CaregiverTier | None
