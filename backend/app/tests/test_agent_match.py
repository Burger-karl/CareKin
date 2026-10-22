"""Tests for the matching agent (Day 14): POST /agents/match, end to end.

Real database, real routes (the agent's tool call goes through POST /caregivers/match
as the user), real guardrails. Only the model is scripted (MockLLMClient).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.agents.matching import GUARDIAN_NOTE, MatchPreferences
from app.agents.models import AgentConversation, AgentMessage, ConversationStatus, MessageRole
from app.config import get_settings
from app.core import encryption
from app.core.permissions import UserRole
from app.domains.auth.models import ConsentRecord, ConsentType, User
from app.domains.bookings.service import LAGOS
from app.domains.caregivers.models import CaregiverTier
from app.domains.trust.models import Incident
from app.integrations import llm
from app.integrations.llm import LLMReply, LLMUnavailable, text_reply, tool_reply
from app.tests.test_caregiver_matching import (
    auth,
    make_caregiver,
    make_guardian,
    make_patient,
    make_user,
    slot,
)

URL = "/agents/match"


# ----------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def fernet(monkeypatch):
    """Tests don't set FIELD_ENCRYPTION_KEY; use a throwaway key (as test_patients does)."""
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_fernet", lambda: key)
    return key


@pytest.fixture
def model():
    """The app's mock model (LLM_PROVIDER=mock in tests), emptied around each test."""
    mock = llm.get_llm_client()
    assert isinstance(mock, llm.MockLLMClient)
    mock.reset()
    yield mock
    mock.reset()


async def consent(db, user: User) -> None:
    db.add(
        ConsentRecord(
            user_id=user.id,
            consent_type=ConsentType.LLM_PROCESSING,
            policy_version=get_settings().CONSENT_POLICY_VERSION,
            granted=True,
        )
    )
    await db.commit()


async def family(db, **patient_kwargs) -> tuple[User, object]:
    """An adult patient who consented to the assistant."""
    user, patient = await make_patient(db, **patient_kwargs)
    await consent(db, user)
    return user, patient


def body(patient, message: str = "Someone who speaks Igbo, please", **overrides) -> dict:
    starts_at, ends_at = slot()
    data = {
        "message": message,
        "patient_id": str(patient.id),
        "booking_type": "hourly",
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
    }
    data.update(overrides)
    return data


async def ask(api, user, patient, message="Someone who speaks Igbo, please", **overrides):
    return await api.post(URL, json=body(patient, message, **overrides), headers=auth(user))


async def log_of(db, session_id) -> list[tuple[MessageRole, str]]:
    rows = await db.scalars(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == session_id)
        .order_by(AgentMessage.position)
    )
    return [(row.role, row.tool_name or row.content) for row in rows]


def code(response) -> str:
    return response.json()["error"]["code"]


# ----------------------------------------------------------------- answering
async def test_the_model_ranks_by_what_the_family_asked(api, db, model) -> None:
    user, patient = await family(db)
    await make_caregiver(db, name="English Only", km=1, languages=["english"])
    await make_caregiver(db, name="Speaks Igbo", km=3, languages=["english", "igbo"])
    model.queue(
        tool_reply("match_caregivers", {"languages": ["igbo"]}),
        text_reply("Speaks Igbo is the best fit: she speaks Igbo and is 3 km away."),
    )
    response = await ask(api, user, patient)

    assert response.status_code == 200, response.text
    out = response.json()
    assert out["status"] == "answered" and out["reply_source"] == "model"
    assert out["reply"] == "Speaks Igbo is the best fit: she speaks Igbo and is 3 km away."
    assert [m["full_name"] for m in out["results"]] == ["Speaks Igbo", "English Only"]
    assert out["guardian_approval_required"] is False
    assert await log_of(db, out["session_id"]) == [
        (MessageRole.USER, "Someone who speaks Igbo, please"),
        (MessageRole.TOOL, "match_caregivers"),
        (MessageRole.ASSISTANT, out["reply"]),
    ]


async def test_the_model_reads_a_short_summary_not_the_whole_result(api, db, model) -> None:
    user, patient = await family(db)
    await make_caregiver(db, name="Chi")
    model.queue(tool_reply("match_caregivers"), text_reply("Chi fits."))
    await ask(api, user, patient)
    summary = model.requests[1]["messages"][-1].content
    assert '"name":"Chi"' in summary
    assert "caregiver_id" not in summary and "estimated_total_minor" not in summary
    assert "score" not in summary  # the live model quoted it to a family


async def test_the_booking_comes_from_the_app_not_the_model(api, db, model) -> None:
    user, patient = await family(db)
    _, other_patient = await make_patient(db)
    await make_caregiver(db, name="Chi")
    model.queue(
        tool_reply("match_caregivers", {"patient_id": str(other_patient.id), "starts_at": "9am"}),
        text_reply("Chi fits."),
    )
    response = await ask(api, user, patient)
    assert response.status_code == 200, response.text  # the model's patient was ignored
    assert [m["full_name"] for m in response.json()["results"]] == ["Chi"]


async def test_plain_words_for_the_tier_are_understood(api, db, model) -> None:
    user, patient = await family(db)
    await make_caregiver(db, name="Companion", tier=CaregiverTier.COMPANION)
    await make_caregiver(db, name="Nurse", tier=CaregiverTier.LICENSED_NURSE)
    model.queue(tool_reply("match_caregivers", {"min_tier": "Nurse"}), text_reply("A nurse."))
    out = (await ask(api, user, patient, "I want a nurse")).json()
    assert [m["full_name"] for m in out["results"]] == ["Nurse"]
    assert out["min_tier_applied"] == "licensed_nurse"


def test_preferences_forgive_a_small_model() -> None:
    assert MatchPreferences.model_validate({"languages": "Igbo and English"}).languages == [
        "igbo",
        "english",
    ]
    assert MatchPreferences.model_validate({"min_tier": "trained carer"}).min_tier == (
        CaregiverTier.TRAINED_CARER
    )
    assert MatchPreferences.model_validate({"min_tier": "any"}).min_tier is None


# ----------------------------------------------------------------- without the model
async def test_model_down_still_answers_from_the_results(api, db, model) -> None:
    user, patient = await family(db)
    await make_caregiver(db, name="Chi Okeke", km=1, rating=("4.8", 12), languages=["igbo"])
    await make_caregiver(db, name="Ada", km=4)
    model.queue(LLMUnavailable("ollama", "unreachable (ConnectError)"))
    response = await ask(api, user, patient)

    assert response.status_code == 200, response.text
    out = response.json()
    assert out["reply_source"] == "fallback"
    assert out["reply"].startswith("Best match: Chi Okeke, a companion, 1.0 km away")
    assert "rated 4.80 by 12 families" in out["reply"] and "Speaks Igbo" in out["reply"]
    assert "1 more caregiver also fits." in out["reply"]
    assert (MessageRole.SYSTEM, out["reply"]) in await log_of(db, out["session_id"])


async def test_a_reply_about_medicine_is_replaced(api, db, model) -> None:
    user, patient = await family(db)
    await make_caregiver(db, name="Chi")
    model.queue(tool_reply("match_caregivers"), text_reply("Chi can give him 5mg of morphine."))
    out = (await ask(api, user, patient)).json()
    assert out["reply_source"] == "fallback" and "morphine" not in out["reply"]


async def test_a_cut_off_reply_keeps_only_whole_sentences(api, db, model) -> None:
    user, patient = await family(db)
    await make_caregiver(db, name="Chi")
    model.queue(
        tool_reply("match_caregivers"),
        LLMReply(content="Chi is the best fit. She is close and", truncated=True),
    )
    out = (await ask(api, user, patient)).json()
    assert out["reply"] == "Chi is the best fit." and out["reply_source"] == "model"


async def test_no_one_free_says_so(api, db, model) -> None:
    user, patient = await family(db)
    model.queue(LLMUnavailable("ollama", "down"))
    out = (await ask(api, user, patient)).json()
    assert out["results"] == [] and out["reply"].startswith("No caregiver is free")


# ----------------------------------------------------------------- guardians and minors
async def test_the_guardian_note_is_always_added_by_us(api, db, model) -> None:
    _, patient = await make_patient(db, minor=True)
    guardian = await make_guardian(db, patient, can_approve=False)
    await consent(db, guardian)
    await make_caregiver(db, name="Trained", tier=CaregiverTier.TRAINED_CARER)
    model.queue(tool_reply("match_caregivers"), text_reply("Trained fits well."))
    out = (await ask(api, guardian, patient)).json()
    assert out["guardian_approval_required"] is True
    assert out["reply"] == f"Trained fits well.\n\n{GUARDIAN_NOTE}"
    assert out["min_tier_applied"] == "trained_carer"  # the minor rule still applies


# ----------------------------------------------------------------- guardrails
async def test_an_emergency_hands_off_without_calling_the_model(api, db, model) -> None:
    user, patient = await family(db)
    await make_caregiver(db, name="Chi")
    response = await ask(api, user, patient, "my son is in crisis and can't breathe")

    assert response.status_code == 200, response.text
    out = response.json()
    assert out["status"] == "handed_off" and out["reply_source"] == "handoff"
    assert "Crisis button" in out["reply"] and out["results"] == []
    assert model.requests == []
    conversation = await db.get(AgentConversation, out["session_id"])
    await db.refresh(conversation)
    assert conversation.status is ConversationStatus.HANDED_OFF
    assert await db.get(Incident, conversation.handoff_incident_id) is not None


async def test_a_medical_question_hands_off(api, db, model) -> None:
    user, patient = await family(db)
    out = (await ask(api, user, patient, "what dose of folic acid should he take?")).json()
    assert out["status"] == "handed_off" and "can't give medical advice" in out["reply"]
    assert model.requests == []


async def test_a_handed_off_conversation_stays_with_a_person(api, db, model) -> None:
    user, patient = await family(db)
    first = (await ask(api, user, patient, "she fainted")).json()
    again = await ask(api, user, patient, session_id=first["session_id"])
    assert again.status_code == 409 and code(again) == "conversation_handed_off"


# ----------------------------------------------------------------- conversations
async def test_a_follow_up_continues_the_conversation(api, db, model) -> None:
    user, patient = await family(db)
    await make_caregiver(db, name="Chi")
    model.queue(
        tool_reply("match_caregivers"),
        text_reply("Chi fits."),
        tool_reply("match_caregivers", {"min_tier": "nurse"}),
        text_reply("No nurse is free."),
    )
    first = (await ask(api, user, patient)).json()
    second = (await ask(api, user, patient, "only a nurse", session_id=first["session_id"])).json()
    assert second["session_id"] == first["session_id"]
    assert len(await log_of(db, first["session_id"])) == 6


async def test_someone_elses_conversation_is_not_found(api, db, model) -> None:
    user, patient = await family(db)
    other, other_patient = await family(db)
    model.queue(tool_reply("match_caregivers"), text_reply("Done."))
    theirs = (await ask(api, other, other_patient)).json()
    response = await ask(api, user, patient, session_id=theirs["session_id"])
    assert response.status_code == 404 and code(response) == "conversation_not_found"


# ----------------------------------------------------------------- refusals (before the model)
async def test_no_assistant_consent_is_refused(api, db, model) -> None:
    user, patient = await make_patient(db)
    response = await ask(api, user, patient)
    assert response.status_code == 403 and code(response) == "consent_required"
    assert model.requests == []
    assert await db.scalar(select(func.count()).select_from(AgentConversation)) == 0


async def test_no_access_to_the_patient_is_not_found(api, db, model) -> None:
    user, _ = await family(db)
    _, stranger_patient = await make_patient(db)
    response = await ask(api, user, stranger_patient)
    assert response.status_code == 404
    assert model.requests == []
    assert await db.scalar(select(func.count()).select_from(AgentConversation)) == 0


async def test_a_slot_in_the_past_is_refused(api, db, model) -> None:
    user, patient = await family(db)
    starts_at = slot()[0] - timedelta(days=5)
    response = await ask(
        api,
        user,
        patient,
        starts_at=starts_at.astimezone(LAGOS).isoformat(),
        ends_at=(starts_at + timedelta(hours=2)).isoformat(),
    )
    assert response.status_code == 400 and code(response) == "slot_in_past"
    assert model.requests == []


async def test_caregivers_cannot_use_the_agent(api, db, model) -> None:
    _, patient = await make_patient(db)
    caregiver = await make_user(db, UserRole.CAREGIVER)
    response = await ask(api, caregiver, patient)
    assert response.status_code == 403 and code(response) == "role_forbidden"


async def test_matching_needs_a_login(api, db, model) -> None:
    _, patient = await make_patient(db)
    response = await api.post(URL, json=body(patient))
    assert response.status_code == 401


async def test_an_empty_message_is_rejected(api, db, model) -> None:
    user, patient = await family(db)
    response = await ask(api, user, patient, "   ")
    assert response.status_code == 422
