"""WhatsApp care companion (Day 15, PRD section 8). Reminders only: no medical advice,
dosing or diagnosis.

"Daily check-ins, hydration and medication reminders, appointment reminders, and a
quick path to the crisis flow." The same turn runs for the app (POST
/agents/companion/message) and for WhatsApp (POST /webhooks/whatsapp).

One turn:
1. Screen the message (guardrails.py). Distress or a medical question hands off to a
   person. "SOS", "crisis" or "emergency" on their own count as distress: the quick
   path into the crisis flow. On WhatsApp every handoff reply carries the crisis link.
2. "help" gets a fixed menu that starts with the crisis link.
3. A message about reminders ("remind me...", "my reminders") goes to the model with
   two tools that call our own API as the user: list_reminders and add_reminder
   (/patients/{id}/reminders). The patient comes from our code; the model only sets
   the kind and time, and the wording of reminders is fixed (reminders.py). Once a
   tool has run, our code writes the reply from its result: the model never says
   what it did, so it can't claim a reminder it didn't set.
4. Anything else is chat: the model replies with no tools, seeing the last few lines
   of the conversation.
5. If the model is down, slow, off-topic or strays into medicine, the reply is the menu.

What the live model (llama3.2:3b) taught us: shown earlier requests, it repeats them
(a second water reminder instead of the appointment); offered tools during chat, it
sets reminders nobody asked for ("I'm tired" became a check-in). So a reminder
message is sent alone, with one exception: the answer to the model's own question
("what time?") is sent with that question and the request before it.

WhatsApp only (`handle_whatsapp`): the sender is found by phone number. Unknown
numbers, other roles and non-text messages get a fixed reply and nothing is stored.
"STOP" switches off their WhatsApp reminders. A conversation handed to a person stays
with them until the handoff incident is resolved: messages are logged for ops and get
a fixed reply. Without `llm_processing` consent, only safety messages are taken (they
go to a person, not the model); anything else gets a reply asking for consent.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from fastapi import Request
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp

from app.agents import reminders
from app.agents import schemas as s
from app.agents.guardrails import (
    crisis_link_line,
    ensure_llm_consent,
    hand_off,
    handoff_reply,
    screen_message,
)
from app.agents.models import (
    AgentChannel,
    AgentConversation,
    AgentKind,
    AgentMessage,
    ConversationStatus,
    GuardrailFlag,
    MessageRole,
    ReminderKind,
)
from app.agents.runner import AgentOutcome, ConversationLog, complete_sentences, run_agent
from app.agents.tools import AgentTool, ApiCaller, ToolRequestFailed
from app.config import get_settings
from app.core.audit import write_audit
from app.core.exceptions import ConflictError, NotFoundError
from app.core.permissions import UserRole
from app.core.phone import mask_phone
from app.database import SessionLocal
from app.domains.auth.models import ConsentType, User
from app.domains.auth.service import has_consent
from app.domains.bookings.service import LAGOS
from app.domains.patients.models import Guardian, Patient
from app.domains.patients.service import PatientAccessDenied, get_access
from app.domains.trust.models import OPEN_STATUSES, Incident
from app.integrations.base import IntegrationError
from app.integrations.llm import ChatMessage, LLMClient
from app.integrations.whatsapp import IncomingMessage, WhatsAppClient

logger = logging.getLogger(__name__)

LIST_TOOL_NAME = "list_reminders"
ADD_TOOL_NAME = "add_reminder"
HISTORY_TURNS = 4  # earlier lines the model sees; each costs seconds on CPU
HISTORY_LIMIT = 500  # messages returned by GET /history
COMPANION_ROLES = (UserRole.PATIENT, UserRole.GUARDIAN)
SEEN_TTL_SECONDS = 7 * 24 * 3600  # Meta retries a webhook for up to a week

# Short on purpose: the model reads ~14 tokens/s on CPU.
REMINDER_PROMPT = (
    "You set and list CareKin reminders with the tools: water (hydration), medicine, "
    "appointments and a daily check-in. Act on the user's last message only. If they "
    "didn't say a time, ask for it in one short sentence. Never give medical advice."
)
CHAT_PROMPT = (
    "You are CareKin's care companion for people living with sickle cell disease and "
    "their families. Reply in one or two short, warm sentences. Never give medical "
    "advice, never name medicines or doses, never say whether a symptom is normal: "
    "suggest their doctor. In an emergency, tell them to press the Crisis button. You "
    "can't set reminders in this reply: if they want one, ask them to say what and when, "
    'e.g. "remind me to drink water at 8pm".'
)

# A message about reminders: only these get the reminder tools.
REMINDER_WORDS = re.compile(r"\b(remind\w*|alarm|check[- ]?ins?)\b", re.IGNORECASE)
# The quick path into the crisis flow: the whole message is one of these words.
QUICK_CRISIS = re.compile(r"^\W*(sos|crisis|emergency)\W*$", re.IGNORECASE)
HELP = re.compile(r"^\W*(help|menu)\W*$", re.IGNORECASE)
STOP = re.compile(r"^\W*(stop|unsubscribe)\W*$", re.IGNORECASE)


# ---------------------------------------------------------------- fixed replies
def _emergency() -> str:
    return get_settings().EMERGENCY_NUMBER


def help_reply(*, link_crisis: bool) -> str:
    reply = f"In an emergency, press the Crisis button in the CareKin app or call {_emergency()}."
    if link_crisis:
        reply += f"\n{crisis_link_line()}"
    return (
        f"{reply}\n\nI'm CareKin's care companion. I can remind you about water, medicine, "
        'appointments and a daily check-in, for example: "remind me to drink water at '
        "10am\". I can't give medical advice: please ask a doctor, nurse or pharmacist."
    )


def held_reply() -> str:
    return (
        "A member of the CareKin team has your conversation and will contact you soon. "
        f"In an emergency, press the Crisis button or call {_emergency()}.\n\n"
        f"{crisis_link_line()}"
    )


def unknown_sender_reply() -> str:
    return (
        "This is CareKin's care companion. It works for registered patients and families: "
        "sign up in the CareKin app with this phone number first. "
        f"In an emergency, call {_emergency()}."
    )


def wrong_role_reply() -> str:
    return (
        "The care companion is for patients and their families. Please use the CareKin app. "
        f"In an emergency, call {_emergency()}."
    )


def text_only_reply() -> str:
    return (
        "Sorry, I can only read text messages. "
        f"In an emergency, press the Crisis button or call {_emergency()}.\n\n"
        f"{crisis_link_line()}"
    )


def consent_reply() -> str:
    return (
        "To chat with CareKin's assistant, allow it to read your messages in the CareKin app "
        "(Settings, Privacy). Your reminders still work without it. "
        f"In an emergency, press the Crisis button or call {_emergency()}."
    )


def stopped_reply(count: int) -> str:
    if not count:
        return "You have no WhatsApp reminders switched on."
    return (
        f"Done: {count} WhatsApp reminder{'s' if count > 1 else ''} switched off. "
        "Switch them back on in the CareKin app."
    )


def error_reply() -> str:
    return (
        "Sorry, something went wrong on our side. Please try again in a few minutes. "
        f"In an emergency, press the Crisis button or call {_emergency()}.\n\n"
        f"{crisis_link_line()}"
    )


TOOL_REFUSED_REPLY = (
    "I couldn't change your reminders just now. You can manage them in the CareKin app."
)


# ---------------------------------------------------------------- tools
_KIND_WORDS: dict[str, ReminderKind] = {
    "water": ReminderKind.HYDRATION,
    "drink": ReminderKind.HYDRATION,
    "hydration": ReminderKind.HYDRATION,
    "medicine": ReminderKind.MEDICATION,
    "medication": ReminderKind.MEDICATION,
    "meds": ReminderKind.MEDICATION,
    "drugs": ReminderKind.MEDICATION,
    "appointment": ReminderKind.APPOINTMENT,
    "clinic": ReminderKind.APPOINTMENT,
    "hospital": ReminderKind.APPOINTMENT,
    "check in": ReminderKind.CHECK_IN,
    "checkin": ReminderKind.CHECK_IN,
}
_TIME = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?$")
_DAY_GROUPS: dict[str, list[str]] = {
    "every day": list(s.WEEKDAYS),
    "everyday": list(s.WEEKDAYS),
    "daily": list(s.WEEKDAYS),
    "weekdays": list(s.WEEKDAYS[:5]),
    "weekends": list(s.WEEKDAYS[5:]),
}


class ReminderArgs(BaseModel):
    """What the model may set. Forgiving: a 3B model writes "water", "8pm", "Mondays".

    Field names match POST /patients/{id}/reminders; the model sees time, days and date.
    """

    model_config = ConfigDict(extra="ignore")

    kind: ReminderKind
    local_time: time = Field(validation_alias=AliasChoices("time", "local_time"))
    days: list[s.Weekday] | None = None
    on_date: date | None = Field(default=None, validation_alias=AliasChoices("date", "on_date"))

    @field_validator("kind", mode="before")
    @classmethod
    def _kind_words(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        words = " ".join(value.replace("_", " ").replace("-", " ").lower().split())
        if words in {kind.value.replace("_", " ") for kind in ReminderKind}:
            return words.replace(" ", "_")
        for word, kind in _KIND_WORDS.items():
            if word in words:
                return kind
        return value

    @field_validator("local_time", mode="before")
    @classmethod
    def _clock(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        match = _TIME.match(value.strip().lower())
        if match is None:
            return value  # let Pydantic try "08:00:00" and explain the error
        hour, minute = int(match[1]), int(match[2] or 0)
        suffix = (match[3] or "").replace(".", "")
        if suffix == "pm" and hour < 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            raise ValueError("time must be a clock time such as 08:00 or 8pm")
        return time(hour, minute)

    @field_validator("days", mode="before")
    @classmethod
    def _day_words(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
            return value  # None, or something Pydantic will explain to the model
        days: list[str] = []
        for part in value:
            text = " ".join(part.lower().split())
            if text in _DAY_GROUPS:
                days.extend(_DAY_GROUPS[text])
                continue
            for word in re.split(r"\s*(?:,|\band\b|\s)\s*", text):
                if word:
                    days.append(word[:3])
        return sorted(set(days), key=lambda d: s.WEEKDAYS.index(d) if d in s.WEEKDAYS else 9)

    @field_validator("on_date", mode="before")
    @classmethod
    def _date_words(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        today = datetime.now(LAGOS).date()
        words = " ".join(re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", value).replace(",", " ").split())
        if words.lower() == "today":
            return today
        if words.lower() == "tomorrow":
            return today + timedelta(days=1)
        # "3 November", "Nov 3", "3 Nov 2026": without a year, the next one to come.
        for pattern in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(words, pattern).date()
            except ValueError:
                pass
            try:  # always parse with a year: 29 Feb is ambiguous without one
                day = datetime.strptime(f"{words} {today.year}", pattern).date()
            except ValueError:
                continue
            return day if day >= today else day.replace(year=today.year + 1)
        return value

    @field_validator("days")
    @classmethod
    def _every_day_is_the_default(cls, value: list[str] | None) -> list[str] | None:
        return None if value is not None and len(value) == 7 else value

    @model_validator(mode="after")
    def _appointments_have_a_date(self) -> ReminderArgs:
        # The live model dropped "on 3 November" and set a daily appointment reminder.
        if self.kind is ReminderKind.APPOINTMENT and self.on_date is None:
            raise ValueError("an appointment needs its date: set date as YYYY-MM-DD")
        return self


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")


KIND_LABELS = {kind.value: word for kind, word in reminders.KIND_WORDS.items()}


def _brief(reminder: dict[str, Any]) -> dict[str, Any]:
    """One reminder as the model sees it: no ids or timestamps."""
    out: dict[str, Any] = {
        "kind": KIND_LABELS[reminder["kind"]],
        "time": reminder["local_time"][:5],
    }
    if reminder["on_date"]:
        out["date"] = reminder["on_date"]
    else:
        out["days"] = "every day" if len(reminder["days"]) == 7 else reminder["days"]
    return out


LIST_TOOL = AgentTool(
    name=LIST_TOOL_NAME,
    description="List the reminders already set.",
    args=NoArgs,
    method="GET",
    path="/patients/{patient_id}/reminders",
    for_model=lambda result: {"reminders": [_brief(r) for r in result if r["active"]]},
    parameters={"type": "object", "properties": {}, "required": []},
)

ADD_TOOL = AgentTool(
    name=ADD_TOOL_NAME,
    description="Set a reminder. The wording is fixed; you only choose kind and time.",
    args=ReminderArgs,
    method="POST",
    path="/patients/{patient_id}/reminders",
    for_model=lambda result: {"set": _brief(result)},
    parameters={
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": [kind.value for kind in ReminderKind],
                "description": "hydration is water; check_in is a daily how-are-you message.",
            },
            "time": {"type": "string", "description": "Clock time, e.g. 08:00 or 8pm."},
            "days": {
                "type": "array",
                "items": {"type": "string", "enum": list(s.WEEKDAYS)},
                "description": "Only if they named days. Omit for every day.",
            },
            "date": {
                "type": "string",
                "description": "YYYY-MM-DD. Required for an appointment.",
            },
        },
        "required": ["kind", "time"],
    },
)


# ---------------------------------------------------------------- replies
def tools_reply(outcome: AgentOutcome) -> str | None:
    """The reply written from what the tools did; None if no tool ran."""
    for name, result in reversed(outcome.tool_results):
        if name == ADD_TOOL_NAME:
            reminder = s.ReminderRead.model_validate(result)
            return f"Done: I'll remind you about {reminders.describe(reminder)}."
        if name == LIST_TOOL_NAME:
            active = [s.ReminderRead.model_validate(r) for r in result if r["active"]]
            if not active:
                return 'You have no reminders set. Try: "remind me to drink water at 10am".'
            return "Your reminders: " + "; ".join(reminders.describe(r) for r in active) + "."
    return None


def _screen(text: str) -> GuardrailFlag:
    if QUICK_CRISIS.match(text):
        return GuardrailFlag.DISTRESS
    return screen_message(text)


def _system_prompt(prompt: str, patient: Patient | None) -> str:
    now = datetime.now(LAGOS)
    about = f" Reminders are for {reminders.first_name(patient)}." if patient else ""
    return f"{prompt} Today is {now:%A %d %B %Y}, {now:%H:%M} in Lagos.{about}"


def _as_chat(rows: list[AgentMessage]) -> list[ChatMessage]:
    return [
        ChatMessage("user" if row.role is MessageRole.USER else "assistant", row.content)
        for row in rows
    ]


def _unanswered_reminder(rows: list[AgentMessage]) -> list[AgentMessage] | None:
    """A reminder request the model answered with a question ("what time?"), if the
    last two lines are one: the next message is the answer."""
    if len(rows) < 2:
        return None
    request, question = rows[-2:]
    if (
        request.role is MessageRole.USER
        and REMINDER_WORDS.search(request.content)
        and question.role is MessageRole.ASSISTANT
    ):
        return [request, question]
    return None


async def _recent(db: AsyncSession, conversation: AgentConversation) -> list[AgentMessage]:
    """The last few lines the user saw, oldest first."""
    rows = (
        await db.scalars(
            select(AgentMessage)
            .where(
                AgentMessage.conversation_id == conversation.id,
                AgentMessage.role.in_(
                    (MessageRole.USER, MessageRole.ASSISTANT, MessageRole.SYSTEM)
                ),
                AgentMessage.guardrail_flag == GuardrailFlag.NONE,
            )
            .order_by(AgentMessage.position.desc())
            .limit(HISTORY_TURNS)
        )
    ).all()
    return list(reversed(rows))


# ---------------------------------------------------------------- one turn
@dataclass(frozen=True)
class Turn:
    conversation: AgentConversation
    status: Literal["answered", "handed_off"]
    reply: str
    source: Literal["model", "fallback", "handoff"]
    reminder: s.ReminderRead | None = None


async def take_turn(
    db: AsyncSession,
    *,
    user: User,
    conversation: AgentConversation,
    text: str,
    app: ASGIApp,
    llm: LLMClient,
    link_crisis: bool,
    wa_message_id: str | None = None,
) -> Turn:
    """One message through guardrails and the model. See the module docstring."""
    log = ConversationLog(db, conversation)
    flag = _screen(text)
    recent = await _recent(db, conversation) if flag is GuardrailFlag.NONE else []
    await log.add(MessageRole.USER, text, flag=flag, wa_message_id=wa_message_id)

    # 1. Guardrails.
    if flag is not GuardrailFlag.NONE:
        reply, _ = await hand_off(db, log, flag, link_crisis=link_crisis)
        return Turn(conversation, "handed_off", reply, "handoff")

    # 2. The menu.
    if HELP.match(text):
        reply = help_reply(link_crisis=link_crisis)
        await log.add(MessageRole.SYSTEM, reply)
        return Turn(conversation, "answered", reply, "fallback")

    # 3 and 4. Reminders (with the tools) or chat (without them).
    patient = await db.get(Patient, conversation.patient_id) if conversation.patient_id else None
    tools: list[AgentTool] = []
    fixed: dict[str, dict[str, Any]] = {}
    unanswered = _unanswered_reminder(recent)
    if patient is not None and (REMINDER_WORDS.search(text) or unanswered):
        prompt, history = REMINDER_PROMPT, _as_chat(unanswered or [])
        tools = [LIST_TOOL, ADD_TOOL]
        fixed = {
            LIST_TOOL_NAME: {"patient_id": str(patient.id)},
            ADD_TOOL_NAME: {"patient_id": str(patient.id)},
        }
        if conversation.channel is AgentChannel.WHATSAPP:
            fixed[ADD_TOOL_NAME]["channel"] = "whatsapp"  # they're on WhatsApp now
    else:
        prompt, history = CHAT_PROMPT, _as_chat(recent)
    try:
        outcome = await run_agent(
            llm=llm,
            log=log,
            caller=ApiCaller(app, user.id, user.role),
            system=_system_prompt(prompt, patient),
            user_text=text,
            tools=tools,
            required=None,
            fixed=fixed,
            history=history,
            user_logged=True,
            answer_after_tools=False,
        )
    except ToolRequestFailed:
        # Our own route refused (e.g. too many reminders, or access was removed).
        await log.add(MessageRole.SYSTEM, TOOL_REFUSED_REPLY)
        return Turn(conversation, "answered", TOOL_REFUSED_REPLY, "fallback")

    # 5. The reply: ours after a tool ran, else the model's, else the menu.
    reply = tools_reply(outcome)
    if reply is not None:
        reminder = next(
            (
                s.ReminderRead.model_validate(result)
                for name, result in reversed(outcome.tool_results)
                if name == ADD_TOOL_NAME
            ),
            None,
        )
        await log.add(MessageRole.SYSTEM, reply)
        return Turn(conversation, "answered", reply, "fallback", reminder)
    reply = outcome.text
    if reply is not None and outcome.truncated:
        reply = complete_sentences(reply)
    if reply is not None:
        return Turn(conversation, "answered", reply, "model")
    reply = help_reply(link_crisis=link_crisis)
    await log.add(MessageRole.SYSTEM, reply)
    return Turn(conversation, "answered", reply, "fallback")


# ---------------------------------------------------------------- the patient
async def default_patient(db: AsyncSession, user: User) -> uuid.UUID | None:
    """The patient's own record, or a guardian's only patient. None if neither."""
    if user.role is UserRole.PATIENT:
        return await db.scalar(select(Patient.id).where(Patient.user_id == user.id))
    ids = (
        await db.scalars(select(Guardian.patient_id).where(Guardian.user_id == user.id).limit(2))
    ).all()
    return ids[0] if len(ids) == 1 else None


async def _chosen_patient(db: AsyncSession, user: User, patient_id: uuid.UUID) -> uuid.UUID:
    access = await get_access(db, patient_id, user.id)  # 404 without access
    if not access.can_edit:
        raise PatientAccessDenied("Only the patient or a guardian can use the companion for them")
    return patient_id


# ---------------------------------------------------------------- the app
async def _app_conversation(
    db: AsyncSession, user: User, data: s.CompanionMessageRequest
) -> AgentConversation:
    if data.session_id is None:
        patient_id = (
            await _chosen_patient(db, user, data.patient_id)
            if data.patient_id
            else await default_patient(db, user)
        )
        conversation = AgentConversation(
            agent=AgentKind.COMPANION,
            user_id=user.id,
            patient_id=patient_id,
            channel=AgentChannel(data.channel),
        )
        db.add(conversation)
        await db.commit()
        return conversation
    conversation = await db.get(AgentConversation, data.session_id)
    if (
        conversation is None
        or conversation.user_id != user.id
        or conversation.agent is not AgentKind.COMPANION
    ):
        raise NotFoundError("Conversation not found", code="conversation_not_found")
    if conversation.status is ConversationStatus.HANDED_OFF:
        raise ConflictError(
            "A member of the CareKin team has taken over this conversation. "
            "Start a new one to talk to the companion again.",
            code="conversation_handed_off",
        )
    if conversation.status is ConversationStatus.CLOSED:
        raise ConflictError(
            "This conversation has ended. Start a new one.", code="conversation_closed"
        )
    if data.patient_id is not None and conversation.patient_id != data.patient_id:
        raise ConflictError(
            "This conversation is about another patient. Start a new one.",
            code="conversation_patient_mismatch",
        )
    return conversation


async def run_message(
    db: AsyncSession,
    user: User,
    data: s.CompanionMessageRequest,
    *,
    app: ASGIApp,
    llm: LLMClient,
) -> s.CompanionMessageOut:
    """POST /agents/companion/message."""
    await ensure_llm_consent(db, user.id)
    conversation = await _app_conversation(db, user, data)
    turn = await take_turn(
        db,
        user=user,
        conversation=conversation,
        text=data.message,
        app=app,
        llm=llm,
        link_crisis=False,
    )
    return s.CompanionMessageOut(
        session_id=conversation.id,
        status=turn.status,
        reply=turn.reply,
        reply_source=turn.source,
        reminder=turn.reminder,
    )


# ---------------------------------------------------------------- history
async def history(
    db: AsyncSession, user: User, session_id: uuid.UUID, *, request: Request | None = None
) -> s.ConversationHistoryOut:
    """GET /agents/companion/history/{session_id}.

    The owner sees what they were shown: their messages and the replies sent (no tool
    calls, no withheld model replies). Ops see the full log, only for conversations that
    were handed to a person: that is the reason they need it. Works for the matching
    agent too, whose handoff incidents point here. Every read is audited (HP-4).
    """
    conversation = await db.get(AgentConversation, session_id)
    if conversation is None:
        raise NotFoundError("Conversation not found", code="conversation_not_found")
    if conversation.user_id == user.id:
        view: Literal["owner", "ops"] = "owner"
    elif user.role is UserRole.OPS and conversation.handed_off_at is not None:
        view = "ops"
    else:
        raise NotFoundError("Conversation not found", code="conversation_not_found")

    query = select(AgentMessage).where(AgentMessage.conversation_id == conversation.id)
    if view == "owner":
        query = query.where(
            (AgentMessage.role.in_((MessageRole.USER, MessageRole.SYSTEM)))
            | (
                (AgentMessage.role == MessageRole.ASSISTANT)
                & (AgentMessage.guardrail_flag == GuardrailFlag.NONE)
            )
        )
    rows = (await db.scalars(query.order_by(AgentMessage.position).limit(HISTORY_LIMIT))).all()
    write_audit(
        db,
        action="agent_conversation.view",
        actor_id=user.id,
        entity_type="agent_conversation",
        entity_id=conversation.id,
        request=request,
        metadata={"view": view, "messages": len(rows)},
    )
    await db.commit()
    return s.ConversationHistoryOut(
        session_id=conversation.id,
        agent=conversation.agent,
        channel=conversation.channel,
        status=conversation.status,
        patient_id=conversation.patient_id,
        started_at=conversation.created_at,
        handed_off_at=conversation.handed_off_at,
        handoff_incident_id=conversation.handoff_incident_id,
        view=view,
        messages=[
            s.HistoryMessage(
                position=row.position,
                role=row.role,
                content=row.content,
                tool_name=row.tool_name,
                guardrail_flag=row.guardrail_flag,
                created_at=row.created_at,
            )
            for row in rows
        ],
    )


# ---------------------------------------------------------------- WhatsApp
async def accept_whatsapp(redis: Redis, messages: list[IncomingMessage]) -> list[IncomingMessage]:
    """The messages not seen before. Meta resends a webhook it thinks failed."""
    fresh: list[IncomingMessage] = []
    for message in messages:
        if await redis.set(
            f"whatsapp:seen:{message.message_id}", "1", nx=True, ex=SEEN_TTL_SECONDS
        ):
            fresh.append(message)
    return fresh


async def _whatsapp_conversation(db: AsyncSession, user: User) -> AgentConversation | None:
    """The user's open WhatsApp conversation: active and recent, or still with a person."""
    conversation = await db.scalar(
        select(AgentConversation)
        .where(
            AgentConversation.user_id == user.id,
            AgentConversation.agent == AgentKind.COMPANION,
            AgentConversation.channel == AgentChannel.WHATSAPP,
            AgentConversation.status.in_(
                (ConversationStatus.ACTIVE, ConversationStatus.HANDED_OFF)
            ),
        )
        .order_by(AgentConversation.created_at.desc())
        .limit(1)
    )
    if conversation is None:
        return None
    if conversation.status is ConversationStatus.HANDED_OFF:
        incident = (
            await db.get(Incident, conversation.handoff_incident_id)
            if conversation.handoff_incident_id
            else None
        )
        if incident is not None and incident.status in OPEN_STATUSES:
            return conversation
    else:
        last = await db.scalar(
            select(func.max(AgentMessage.created_at)).where(
                AgentMessage.conversation_id == conversation.id
            )
        )
        idle = timedelta(hours=get_settings().COMPANION_SESSION_IDLE_HOURS)
        if (last or conversation.created_at) > datetime.now(UTC) - idle:
            return conversation
    conversation.status = ConversationStatus.CLOSED
    await db.commit()
    return None


async def _held(
    db: AsyncSession, conversation: AgentConversation, text: str, wa_message_id: str
) -> str:
    """A message to a conversation a person has taken over: logged for ops, fixed reply."""
    log = ConversationLog(db, conversation)
    flag = _screen(text)
    await log.add(MessageRole.USER, text, flag=flag, wa_message_id=wa_message_id)
    reply = (
        handoff_reply(flag, link_crisis=True) if flag is GuardrailFlag.DISTRESS else held_reply()
    )
    await log.add(MessageRole.SYSTEM, reply)
    return reply


async def whatsapp_reply(
    db: AsyncSession, app: ASGIApp, message: IncomingMessage, *, llm: LLMClient
) -> str:
    """What to answer one inbound WhatsApp message. See the module docstring."""
    user = await db.scalar(select(User).where(User.phone == message.from_phone))
    if user is None or not user.is_active:
        return unknown_sender_reply()
    if user.role not in COMPANION_ROLES:
        return wrong_role_reply()
    text = (message.text or "").strip()[: s.MAX_MESSAGE_LENGTH]
    if not text:
        return text_only_reply()
    if STOP.match(text):
        return stopped_reply(await reminders.stop_whatsapp_reminders(db, user))

    conversation = await _whatsapp_conversation(db, user)
    if conversation is not None and conversation.status is ConversationStatus.HANDED_OFF:
        return await _held(db, conversation, text, message.message_id)
    if _screen(text) is GuardrailFlag.NONE and not await has_consent(
        db, user.id, ConsentType.LLM_PROCESSING
    ):
        return consent_reply()  # safety messages still go through: to a person
    if conversation is None:
        conversation = AgentConversation(
            agent=AgentKind.COMPANION,
            user_id=user.id,
            patient_id=await default_patient(db, user),
            channel=AgentChannel.WHATSAPP,
        )
        db.add(conversation)
        await db.commit()
    turn = await take_turn(
        db,
        user=user,
        conversation=conversation,
        text=text,
        app=app,
        llm=llm,
        link_crisis=True,
        wa_message_id=message.message_id,
    )
    return turn.reply


async def handle_whatsapp(
    app: ASGIApp, message: IncomingMessage, *, whatsapp: WhatsAppClient, llm: LLMClient
) -> None:
    """Answer one inbound WhatsApp message. Runs after the webhook has returned 200.

    Never raises: a failure is logged without the message, and the user still gets a
    reply with the emergency number.
    """
    async with SessionLocal() as db:
        try:
            reply = await whatsapp_reply(db, app, message, llm=llm)
        except IntegrityError:
            # Its wa_message_id is already logged: a resend that outlived the Redis check.
            logger.info("WhatsApp message %s already handled", message.message_id)
            return
        except Exception:
            logger.exception("WhatsApp message %s could not be handled", message.message_id)
            await db.rollback()
            reply = error_reply()
    try:
        await whatsapp.send_text(message.from_phone, reply)
    except (IntegrationError, ValueError) as exc:
        logger.warning("WhatsApp reply to %s not sent: %s", mask_phone(message.from_phone), exc)
