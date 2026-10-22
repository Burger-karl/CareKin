"""Tests for guardrails and human handoff (Day 14, PRD section 8 hard rule).

The phrase lists are the specification: what must hand off, and, just as important,
the everyday matching requests that must not. Add to both from pilot logs.
"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.agents.guardrails import (
    ensure_llm_consent,
    hand_off,
    handoff_reply,
    screen_message,
    screen_reply,
)
from app.agents.models import (
    AgentChannel,
    AgentConversation,
    AgentKind,
    AgentMessage,
    ConversationStatus,
    GuardrailFlag,
    MessageRole,
)
from app.agents.runner import ConversationLog, run_agent
from app.agents.tools import ApiCaller
from app.config import get_settings
from app.core import encryption
from app.core.audit import AuditLog
from app.core.exceptions import ForbiddenError
from app.core.permissions import UserRole
from app.domains.auth.models import ConsentRecord, ConsentType, User
from app.domains.trust.models import Incident, IncidentCategory, IncidentStatus, Severity
from app.integrations.llm import MockLLMClient, text_reply, tool_reply
from app.tests.test_agent_runner import ECHO, FIXED, stand_in

DISTRESS = GuardrailFlag.DISTRESS
CLINICAL = GuardrailFlag.CLINICAL
NONE = GuardrailFlag.NONE


# ----------------------------------------------------------------- incoming messages
@pytest.mark.parametrize(
    "text",
    [
        "My son is in crisis and crying",
        "she's having a sickle cell crisis right now",
        "I'm going into a crisis",
        "crisis now please help",
        "He can't breathe properly",
        "my daughter is not breathing",
        "e no fit breathe",
        "she fainted in the kitchen",
        "he don faint",
        "having a seizure",
        "severe pain in his chest",
        "chest pain since morning",
        "I want to die",
        "thinking about suicide",
        "she said she will hurt herself",
    ],
)
def test_emergencies_hand_off_as_distress(text) -> None:
    assert screen_message(text) is DISTRESS


@pytest.mark.parametrize(
    "text",
    [
        "What dose of folic acid should he take?",
        "how much paracetamol can I give my son",
        "Should I stop his hydroxyurea before the trip?",
        "can we double the painkillers tonight",
        "is it safe to give her ibuprofen",
        "Is it safe to mix his antibiotics with folic acid?",
        "he takes 5mg daily, is that right",
        "which medicine is best for the pain",
        "what treatment does he need",
        "Is this a crisis or just tiredness?",
        "is it normal for his eyes to be yellow",
        "can you diagnose what's wrong with him",
        "how do I treat a fever at home",
        "does he need a blood transfusion",
        "what do his test results mean",
        "what are the side effects of hydroxyurea",
    ],
)
def test_medical_questions_hand_off_as_clinical(text) -> None:
    assert screen_message(text) is CLINICAL


@pytest.mark.parametrize(
    "text",
    [
        "Find a carer for my son, he has sickle cell",
        "someone who can handle a sickle cell crisis",
        "a carer experienced in crisis care",
        "I need help with my daughter's care while I travel",
        "help me find a carer please",
        "can we start the booking on Monday?",
        "Can I give the carer my spare key?",
        "is it safe to book a carer at night?",
        "is it safe to use a companion for a 10 year old",
        "what does it mean to be a trained carer?",
        "I want to end my booking early",
        "Someone who speaks Igbo and can do school runs",
        "a nurse who can remind him to take his medicine",
        "overnight carer for 3 nights, close to Ogui Road",
        "someone patient, my son gets scared in hospital",
    ],
)
def test_everyday_matching_requests_pass(text) -> None:
    assert screen_message(text) is NONE


def test_distress_wins_over_clinical() -> None:
    assert screen_message("she's having a crisis, how much paracetamol?") is DISTRESS


# ----------------------------------------------------------------- model replies
@pytest.mark.parametrize(
    "reply",
    [
        "Chi Okeke is nearby. Give him 5mg of folic acid daily.",
        "It sounds like a crisis; Ada can help.",
        "Ada is a nurse and can adjust his dosage.",
        "You may have an infection, so choose the nurse.",
        "Ada can treat his pain at home.",
        "Ngozi can give him his tablets and stop the medicine if needed.",
    ],
)
def test_model_replies_about_medicine_are_withheld(reply) -> None:
    assert screen_reply(reply) is CLINICAL


@pytest.mark.parametrize(
    "reply",
    [
        "Chi Okeke is 1.2 km away, speaks Igbo and has a 4.9 rating from 20 families.",
        "Ada is a licensed nurse with 8 years' experience; Chi is closer but newer.",
        "Two carers are free on Monday morning. Ngozi scores highest.",
    ],
)
def test_ordinary_explanations_are_sent(reply) -> None:
    assert screen_reply(reply) is NONE


def test_fixed_replies_never_give_advice() -> None:
    number = get_settings().EMERGENCY_NUMBER
    distress = handoff_reply(DISTRESS)
    clinical = handoff_reply(CLINICAL)
    assert "Crisis button" in distress and number in distress and "alerted" in distress
    assert "can't give medical advice" in clinical and number in clinical


# ----------------------------------------------------------------- handoff
@pytest.fixture(autouse=True)
def fernet(monkeypatch):
    """Tests don't set FIELD_ENCRYPTION_KEY; use a throwaway key (as test_patients does)."""
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_fernet", lambda: key)
    return key


@pytest.fixture
async def user(db) -> User:
    phone = "+234803" + "".join(random.choices("0123456789", k=7))
    user = User(phone=phone, full_name="Ngozi Okafor", role=UserRole.GUARDIAN)
    db.add(user)
    await db.commit()
    return user


@pytest.fixture
async def log(db, user) -> ConversationLog:
    conversation = AgentConversation(
        agent=AgentKind.MATCHING, user_id=user.id, channel=AgentChannel.WEB
    )
    db.add(conversation)
    await db.commit()
    return await ConversationLog.open(db, conversation)


@pytest.mark.parametrize(
    ("flag", "category", "severity", "target"),
    [
        (DISTRESS, IncidentCategory.SAFETY, Severity.HIGH, timedelta(hours=4)),
        (CLINICAL, IncidentCategory.OTHER, Severity.MEDIUM, timedelta(hours=24)),
    ],
)
async def test_handoff_stops_the_agent_and_opens_an_incident(
    db, log, flag, category, severity, target
) -> None:
    message = "My son has sickle cell and severe pain"
    await log.add(MessageRole.USER, message, flag=flag)
    reply, incident = await hand_off(db, log, flag)

    conversation = log.conversation
    assert conversation.status is ConversationStatus.HANDED_OFF
    assert conversation.handed_off_at is not None
    assert conversation.handoff_incident_id == incident.id

    stored = await db.get(Incident, incident.id)
    assert (stored.category, stored.severity) == (category, severity)
    assert stored.status is IncidentStatus.OPEN and stored.reported_by is None
    clock = stored.response_due_at - stored.created_at
    assert abs(clock - target) < timedelta(minutes=1)
    assert str(conversation.id) in stored.description
    assert "severe pain" not in stored.description  # health details stay in the log

    rows = list(
        await db.scalars(
            select(AgentMessage)
            .where(AgentMessage.conversation_id == conversation.id)
            .order_by(AgentMessage.position)
        )
    )
    assert [(r.role, r.guardrail_flag) for r in rows] == [
        (MessageRole.USER, flag),
        (MessageRole.SYSTEM, flag),
    ]
    assert rows[1].content == reply == handoff_reply(flag)

    audit = await db.scalar(select(AuditLog).where(AuditLog.action == "agent.handoff"))
    assert audit.entity_id == conversation.id and audit.meta["flag"] == flag.value


async def test_handoff_needs_a_flag(db, log) -> None:
    with pytest.raises(ValueError):
        await hand_off(db, log, NONE)


# ----------------------------------------------------------------- the loop withholds
async def test_a_reply_about_medicine_is_logged_but_withheld(db, log, user) -> None:
    model = MockLLMClient()
    model.queue(tool_reply("find"), text_reply("Chi can manage his 10mg morphine dose."))
    outcome = await run_agent(
        llm=model,
        log=log,
        caller=ApiCaller(stand_in, user.id, user.role),
        system="You find caregivers.",
        user_text="Someone for nights",
        tools=[ECHO],
        required="find",
        fixed=FIXED,
    )
    assert outcome.blocked is True and outcome.text is None
    assert outcome.result is not None  # the data still comes back
    last = await db.scalar(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == log.conversation.id)
        .order_by(AgentMessage.position.desc())
    )
    assert last.role is MessageRole.ASSISTANT and last.guardrail_flag is CLINICAL


# ----------------------------------------------------------------- consent
async def test_no_llm_consent_is_refused(db, user) -> None:
    with pytest.raises(ForbiddenError) as caught:
        await ensure_llm_consent(db, user.id)
    assert caught.value.code == "consent_required"
    assert caught.value.details["consent_type"] == "llm_processing"


async def test_llm_consent_lets_the_user_through(db, user) -> None:
    db.add(
        ConsentRecord(
            user_id=user.id,
            consent_type=ConsentType.LLM_PROCESSING,
            policy_version=get_settings().CONSENT_POLICY_VERSION,
            granted=True,
        )
    )
    await db.commit()
    await ensure_llm_consent(db, user.id)  # no error


async def test_revoked_llm_consent_is_refused(db, user) -> None:
    db.add(
        ConsentRecord(
            user_id=user.id,
            consent_type=ConsentType.LLM_PROCESSING,
            policy_version=get_settings().CONSENT_POLICY_VERSION,
            granted=False,
        )
    )
    await db.commit()
    with pytest.raises(ForbiddenError):
        await ensure_llm_consent(db, user.id)
