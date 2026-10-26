"""Agent toolkit models: conversation logs for every AI agent (PRD 8.1, ERD section 2.10).

"All conversations logged; clinical or distress content triggers handoff to a human."
Every user message, model reply and tool call is one AgentMessage row, so ops can
read back exactly what an agent saw and said.

- `AgentMessage.content` is encrypted: users may mention symptoms or their child's
  condition, which is health data (HP-4).
- Nothing cascades: conversation logs are records, like the rest of the health data.

Not in the ERD:
- `AgentMessage.position` orders a conversation's messages. Rows written in one
  transaction share `created_at` (Postgres `now()` is the transaction's start time),
  so the timestamp alone can't order them.
- `AgentConversation.handed_off_at` and `handoff_incident_id` record the handoff and
  the ops incident it opened, which carries the human response clock (TS-2).
- The channel enum is named `agent_channel` instead of the ERD's bare `channel`.
- `started_at` is `created_at` from TimestampMixin.

`Reminder` (Day 15) differs from the ERD:
- The ERD's `schedule` (a cron string) is `local_time` plus `days_of_week`, or
  `on_date` for a one-off such as an appointment. Apps and the companion's model can
  fill these in; nobody has to write cron.
- The ERD's `message_template` is not stored: the text is fixed per `kind` in
  reminders.py (approved wording, no advice), so no free text reaches a user.
- `created_by` is also the recipient: a reminder goes to the person who set it.
- `next_due_at` and `last_sent_at` let Beat find due reminders with one indexed query.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.encryption import EncryptedText
from app.database import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.domains.patients.models import AlertChannel


class AgentKind(StrEnum):
    COMPANION = "companion"
    MATCHING = "matching"


class AgentChannel(StrEnum):
    WHATSAPP = "whatsapp"
    WEB = "web"
    MOBILE = "mobile"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    HANDED_OFF = "handed_off"  # a human takes over; the agent stops replying
    CLOSED = "closed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"  # a tool call and its result
    SYSTEM = "system"  # fixed replies sent by our code, not the model


class GuardrailFlag(StrEnum):
    NONE = "none"
    CLINICAL = "clinical"  # dosing, diagnosis, symptoms: needs a professional
    DISTRESS = "distress"  # crisis or self-harm language: needs a human now


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=_enum_values)


class AgentConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One session with one agent. Its id is the `session_id` in the API."""

    __tablename__ = "agent_conversations"

    agent: Mapped[AgentKind] = mapped_column(_pg_enum(AgentKind, "agent_kind"))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    # The patient the conversation is about, when there is one.
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), index=True
    )
    channel: Mapped[AgentChannel] = mapped_column(_pg_enum(AgentChannel, "agent_channel"))
    status: Mapped[ConversationStatus] = mapped_column(
        _pg_enum(ConversationStatus, "conversation_status"),
        default=ConversationStatus.ACTIVE,
        server_default=ConversationStatus.ACTIVE.value,
    )
    handed_off_at: Mapped[datetime | None]
    # The ops incident opened by the handoff; ops work it from the incident queue.
    handoff_incident_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), index=True
    )
    # The ops user who picked the conversation up.
    handed_off_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )

    messages: Mapped[list[AgentMessage]] = relationship(
        back_populates="conversation", lazy="raise", order_by="AgentMessage.position"
    )


class AgentMessage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One turn: what the user said, what the model replied, or one tool call."""

    __tablename__ = "agent_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "position"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="RESTRICT"), index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger)  # 0, 1, 2... within the conversation
    role: Mapped[MessageRole] = mapped_column(_pg_enum(MessageRole, "message_role"))
    # Text for user, assistant and system rows; JSON of the call and result for tool rows.
    content: Mapped[str] = mapped_column(EncryptedText)
    tool_name: Mapped[str | None] = mapped_column(String(64))
    guardrail_flag: Mapped[GuardrailFlag] = mapped_column(
        _pg_enum(GuardrailFlag, "guardrail_flag"),
        default=GuardrailFlag.NONE,
        server_default=GuardrailFlag.NONE.value,
    )
    # WhatsApp's message id, so a webhook Meta resends is stored once (Day 15).
    wa_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)

    conversation: Mapped[AgentConversation] = relationship(back_populates="messages", lazy="raise")


class ReminderKind(StrEnum):
    HYDRATION = "hydration"
    MEDICATION = "medication"  # "time for your medicine": never a name or a dose
    APPOINTMENT = "appointment"
    CHECK_IN = "check_in"  # the companion's daily "how are you today?"


class Reminder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A reminder the companion sends (PRD section 8). Times are Lagos local time."""

    __tablename__ = "reminders"
    __table_args__ = (
        # Recurring on some days of the week, or once on a date.
        CheckConstraint(
            "on_date IS NOT NULL OR cardinality(days_of_week) BETWEEN 1 AND 7",
            name="schedule",
        ),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[ReminderKind] = mapped_column(_pg_enum(ReminderKind, "reminder_kind"))
    local_time: Mapped[time]
    # 0 = Monday ... 6 = Sunday (Python's weekday()). Empty for a one-off.
    days_of_week: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), default=list, server_default=text("'{}'::smallint[]")
    )
    on_date: Mapped[date | None]
    # Reuses the alert_channel enum type created with emergency_contacts.
    channel: Mapped[AlertChannel] = mapped_column(
        _pg_enum(AlertChannel, "alert_channel"),
        default=AlertChannel.WHATSAPP,
        server_default=AlertChannel.WHATSAPP.value,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Who set it, and who receives it.
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    # When it fires next, in UTC. Null once inactive or a one-off has fired.
    next_due_at: Mapped[datetime | None] = mapped_column(index=True)
    last_sent_at: Mapped[datetime | None]
