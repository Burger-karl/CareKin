"""Agent API schemas: POST /agents/match (Day 14); the companion, its history and
reminders (Day 15)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.agents.models import (
    AgentChannel,
    AgentKind,
    ConversationStatus,
    GuardrailFlag,
    MessageRole,
    ReminderKind,
)
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


# ---------------------------------------------------------------- reminders (Day 15)
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ReminderChannel = Literal["whatsapp", "sms"]


def _minutes(value: dt.time | None) -> dt.time | None:
    """Reminders fire on the minute; drop seconds and any timezone (it's Lagos time)."""
    return None if value is None else value.replace(second=0, microsecond=0, tzinfo=None)


def _sorted_days(value: list[Weekday] | None) -> list[Weekday] | None:
    if value is None:
        return None
    if not value:
        raise ValueError("days needs at least one day; leave it out for every day")
    return sorted(set(value), key=WEEKDAYS.index)


class ReminderCreate(BaseModel):
    """A recurring reminder (`days`, default every day) or a one-off (`on_date`)."""

    model_config = ConfigDict(extra="forbid")

    kind: ReminderKind
    local_time: dt.time = Field(description="Lagos time, e.g. 08:00.", examples=["08:00"])
    days: list[Weekday] | None = Field(
        default=None, description="Days it repeats on. Omit for every day."
    )
    on_date: dt.date | None = Field(default=None, description="Once, on this date (Lagos).")
    channel: ReminderChannel = "whatsapp"

    @field_validator("local_time")
    @classmethod
    def _round_time(cls, value: dt.time) -> dt.time | None:
        return _minutes(value)

    @field_validator("days")
    @classmethod
    def _check_days(cls, value: list[Weekday] | None) -> list[Weekday] | None:
        return _sorted_days(value)

    @model_validator(mode="after")
    def _one_schedule(self) -> ReminderCreate:
        if self.days is not None and self.on_date is not None:
            raise ValueError("Set days for a repeating reminder or on_date for a one-off, not both")
        return self


class ReminderUpdate(BaseModel):
    """Change or switch off a reminder. Setting `days` makes it repeat; `on_date`, a one-off."""

    model_config = ConfigDict(extra="forbid")

    active: bool | None = None
    local_time: dt.time | None = None
    days: list[Weekday] | None = None
    on_date: dt.date | None = None
    channel: ReminderChannel | None = None

    @field_validator("local_time")
    @classmethod
    def _round_time(cls, value: dt.time | None) -> dt.time | None:
        return _minutes(value)

    @field_validator("days")
    @classmethod
    def _check_days(cls, value: list[Weekday] | None) -> list[Weekday] | None:
        return _sorted_days(value)

    @model_validator(mode="after")
    def _check(self) -> ReminderUpdate:
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change")
        if self.days is not None and self.on_date is not None:
            raise ValueError("Set days or on_date, not both")
        return self


class ReminderRead(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    kind: ReminderKind
    local_time: dt.time
    days: list[Weekday] = Field(description="Empty for a one-off.")
    on_date: dt.date | None
    channel: ReminderChannel
    active: bool
    next_due_at: dt.datetime | None
    last_sent_at: dt.datetime | None
    text: str = Field(description="What the reminder will say: fixed wording, never advice.")
    created_at: dt.datetime


# ---------------------------------------------------------------- companion (Day 15)
class CompanionMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(
        min_length=1, max_length=MAX_MESSAGE_LENGTH, examples=["Remind me to drink water at 10am"]
    )
    session_id: uuid.UUID | None = Field(
        default=None, description="Continue an earlier conversation; omit to start one."
    )
    patient_id: uuid.UUID | None = Field(
        default=None,
        description="Who reminders are for. Defaults to your own record, or your only "
        "patient if you are a guardian of one. Without a patient the companion can chat "
        "but not set reminders.",
    )
    channel: Literal["web", "mobile"] = "web"


class CompanionMessageOut(BaseModel):
    session_id: uuid.UUID
    status: Literal["answered", "handed_off"]
    reply: str = Field(description="What to show the user.")
    reply_source: Literal["model", "fallback", "handoff"] = Field(
        description="model: written by the AI; fallback: written by our code (the AI was "
        "down, slow or off-topic, or a fixed answer); handoff: a person will follow up."
    )
    reminder: ReminderRead | None = Field(
        default=None, description="The reminder set during this turn, if one was."
    )


class HistoryMessage(BaseModel):
    position: int
    role: MessageRole
    content: str
    tool_name: str | None
    guardrail_flag: GuardrailFlag
    created_at: dt.datetime


class ConversationHistoryOut(BaseModel):
    session_id: uuid.UUID
    agent: AgentKind
    channel: AgentChannel
    status: ConversationStatus
    patient_id: uuid.UUID | None
    started_at: dt.datetime
    handed_off_at: dt.datetime | None
    handoff_incident_id: uuid.UUID | None
    view: Literal["owner", "ops"] = Field(
        description="owner: what the user was shown. ops: the full log, with tool calls and "
        "withheld model replies, for handed-off conversations."
    )
    messages: list[HistoryMessage]
