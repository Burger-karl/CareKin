"""Guardrails and human handoff: no medical advice, ever (PRD section 8, hard rule).

"No medical advice, no dosing, no diagnosis. Anything clinical is routed to a human
professional." and "clinical or distress content triggers handoff to a human."

The checks are keyword and pattern rules, not the model: a 3B model would miss
things and can't be trusted to judge its own output. They err towards flagging:
a needless handoff costs ops a few minutes; a missed one could cost far more.

Incoming message (`screen_message`), distress checked first:
- DISTRESS: an emergency now (can't breathe, unconscious, seizure, severe pain,
  self-harm). Not the word "crisis" alone: "a carer who can handle crises" is a
  normal matching request.
- CLINICAL: asking for medical advice (doses, which medicine, is it safe, diagnosis,
  treatment). Mentioning a condition ("my son has sickle cell") is not.
Either one stops the agent and hands off (`hand_off`): the conversation is marked
handed_off, the user gets a fixed reply written by us, never the model, and an ops
incident opens with a response clock (distress safety/high, clinical other/medium).

The model's reply (`screen_reply`): doses, diagnoses or treatment talk mean the reply
is not sent. The agent answers without the model instead (no handoff: the user
didn't ask for advice).

Patterns cover English and common Nigerian Pidgin. Extend them from pilot logs:
every flag is stored on the message (`guardrail_flag`), so misses and false alarms
can be found.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models import ConversationStatus, GuardrailFlag, MessageRole
from app.config import get_settings
from app.core.audit import write_audit
from app.core.exceptions import ForbiddenError
from app.domains.auth.models import ConsentType
from app.domains.trust.models import Incident, IncidentCategory, Severity
from app.domains.trust.rules import open_system_incident

if TYPE_CHECKING:  # runner imports this module
    from app.agents.runner import ConversationLog


def _rules(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


# An emergency happening now, or self-harm.
DISTRESS = _rules(
    r"\b(can'?t|cannot|can ?not|unable to|struggling to|hard to) breathe?\b",
    r"\bnot breathing\b",
    r"\bno (dey|fit) breathe\b",  # Pidgin
    r"\b(unconscious|unresponsive|passed out|fainted|collapsed|seizure|fitting|convuls\w*)\b",
    r"\b(don|done) faint\b",  # Pidgin
    # Someone IS in a crisis: "my son is in crisis", "she's having a crisis".
    r"\b(is|am|are|was|he'?s|she'?s|i'?m|we'?re)\s+(now\s+|currently\s+)?(in|having|going into)"
    r"\s+(a |an )?(bad |severe |sickle[- ]cell )?(crisis|emergency)\b",
    r"\bhaving (a |an )?(bad |severe |sickle[- ]cell )?crisis\b",
    r"\b(crisis|emergency)\s+(now|right now|happening)\b",
    r"\b(severe|terrible|unbearable|extreme) pain\b",
    r"\bchest pain\b",
    r"\b(stroke|bleeding heavily|won'?t stop bleeding)\b",
    r"\b(dying|i'?m going to die|dey die)\b",
    r"\b(suicid\w*|kill (my|him|her)sel(f|ves)|end (my life|it all)|want to die|self[- ]harm"
    r"|hurt (my|him|her)self|don'?t want to (live|be alive))\b",
)

_MEDICINE = (
    r"(medicine|medication|meds|drugs?|tablets?|pills?|doses?|folic|hydroxyurea|paracetamol"
    r"|ibuprofen|morphine|tramadol|painkillers?|antibiotics?|penicillin|injections?)"
)

# Asking for medical advice: doses, medicines, diagnosis, treatment. Questions about
# medicine must name one: "can we start on Monday?" is a booking question.
CLINICAL = _rules(
    r"\b(dose|doses|dosage|dosing|overdose|side effects?)\b",
    r"\b\d+(\.\d+)?\s?(mg|mcg|ml|milligrams?|tablets?|pills?)\b",
    rf"\bhow (much|many|often)\b.{{0,40}}\b{_MEDICINE}",
    rf"\b(should|can|could|may|do) (i|we|he|she|they|my \w+)\b.{{0,40}}\b(take|give|stop|start"
    rf"|double|skip|increase|reduce|mix|use)\b.{{0,30}}\b{_MEDICINE}",
    rf"\bsafe to (take|give|mix|use)\b.{{0,25}}\b{_MEDICINE}",
    r"\b(what|which) (medicine|medication|drug|tablet|pill|treatment|antibiotic)s?\b",
    r"\bis (it|this|that) (normal|dangerous|a crisis|sickle cell)\b",
    r"\b(diagnos\w*|what'?s wrong with (him|her|me|my)|what is wrong with (him|her|me|my))\b",
    r"\b(how (do|can|should) (i|we) treat|how to treat|treatment for|cure for|remedy for)\b",
    r"\b(do (i|we|they|he|she) need|need) (a |an )?(blood )?transfusion\b",
    r"\bwhat (does|do) (the|my|his|her|these|this) (\w+ )?(results?|tests?|scan|report|reading"
    r"|levels?|numbers?) mean\b",
)

# A model reply that strays into medicine. Broader than CLINICAL on purpose: the model
# has no reason to mention any of this when explaining a list of caregivers.
UNSAFE_REPLY = _rules(
    r"\b(dose|doses|dosage|dosing|prescri\w*|overdose)\b",
    r"\b\d+(\.\d+)?\s?(mg|mcg|ml|milligrams?|tablets?|pills?)\b",
    r"\b(diagnos\w*|you (may|might|probably) have|it sounds like|symptoms? of)\b",
    r"\b(treat|treatment|treating|cure|remedy|antibiotics?)\b",
    rf"\b(take|give|stop|start|increase|reduce) (the |your |his |her |some )?{_MEDICINE}",
)

HANDOFF_INCIDENT: dict[GuardrailFlag, tuple[IncidentCategory, Severity]] = {
    GuardrailFlag.DISTRESS: (IncidentCategory.SAFETY, Severity.HIGH),
    GuardrailFlag.CLINICAL: (IncidentCategory.OTHER, Severity.MEDIUM),
}


def screen_message(text: str) -> GuardrailFlag:
    """What a user's message needs. Distress wins over clinical."""
    if DISTRESS.search(text):
        return GuardrailFlag.DISTRESS
    if CLINICAL.search(text):
        return GuardrailFlag.CLINICAL
    return GuardrailFlag.NONE


def screen_reply(text: str) -> GuardrailFlag:
    """CLINICAL if a model's reply strays into medicine and must not be sent."""
    return GuardrailFlag.CLINICAL if UNSAFE_REPLY.search(text) else GuardrailFlag.NONE


def handoff_reply(flag: GuardrailFlag) -> str:
    """Fixed text sent on a handoff. Written by us; the model never words this."""
    emergency = get_settings().EMERGENCY_NUMBER
    if flag is GuardrailFlag.DISTRESS:
        return (
            "This sounds urgent. If someone is in danger or having a sickle cell crisis, "
            f"press the Crisis button in the CareKin app now, or call {emergency}. "
            "A member of the CareKin team has been alerted and will contact you."
        )
    return (
        "I can't give medical advice, including about medicines, doses or symptoms. "
        "Please ask a doctor, nurse or pharmacist. A member of the CareKin team will "
        f"follow up with you. If it's an emergency, press the Crisis button or call {emergency}."
    )


async def hand_off(
    db: AsyncSession, log: ConversationLog, flag: GuardrailFlag
) -> tuple[str, Incident]:
    """Stop the agent and pass the conversation to a human. Returns the reply to send.

    The user's message must already be logged with `flag`. The incident names the
    conversation, not the message: ops read the message in the conversation log, so
    health details aren't copied around.
    """
    if flag is GuardrailFlag.NONE:
        raise ValueError("hand_off needs a clinical or distress flag")
    category, severity = HANDOFF_INCIDENT[flag]
    conversation = log.conversation
    incident = open_system_incident(
        db,
        booking_id=None,
        category=category,
        severity=severity,
        description=(
            f"AI agent handoff ({flag.value}). The {conversation.agent.value} agent stopped "
            f"and needs a person. Read conversation {conversation.id} "
            f"(user {conversation.user_id}) and contact the user."
        ),
    )
    # Write the incident before pointing at it: nothing tells SQLAlchemy to order these.
    await db.flush()
    conversation.status = ConversationStatus.HANDED_OFF
    conversation.handed_off_at = datetime.now(UTC)
    conversation.handoff_incident_id = incident.id
    write_audit(
        db,
        action="agent.handoff",
        actor_id=None,  # the system decided, on the user's message
        entity_type="agent_conversation",
        entity_id=conversation.id,
        metadata={"flag": flag.value, "incident_id": str(incident.id)},
    )
    reply = handoff_reply(flag)
    await log.add(MessageRole.SYSTEM, reply, flag=flag)  # commits all of the above
    return reply, incident


async def ensure_llm_consent(db: AsyncSession, user_id: uuid.UUID) -> None:
    """403 consent_required unless the user allowed their messages to reach the model."""
    # Imported here like auth.dependencies does: auth.service imports its schemas.
    from app.domains.auth.service import has_consent

    if not await has_consent(db, user_id, ConsentType.LLM_PROCESSING):
        raise ForbiddenError(
            "Allow CareKin's assistant to process your messages before using it.",
            code="consent_required",
            details={"consent_type": ConsentType.LLM_PROCESSING.value, "next": "/auth/consent"},
        )
