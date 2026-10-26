"""The matching agent (Day 14, PRD section 8): POST /agents/match.

A family describes who they want in their own words; the agent ranks caregivers and
explains the ranking. Guardian or ops approval for minors is unchanged: the agent
never books, and says when a booking will need a guardian's approval.

One request:
1. Refuse early, before any slow model call: a slot in the past, no access to the
   patient, no `llm_processing` consent.
2. Open or continue the conversation. A handed-off conversation stays with a person.
3. Screen the message. Distress or a medical question hands off (guardrails.py).
4. Run the loop (runner.py) with one tool, match_caregivers: POST /caregivers/match
   as the user. The app's patient and time slot are fixed; the model only sets
   languages and minimum tier from what the family wrote.
5. Reply with the model's explanation, or a reply built from the results when the
   model is down, slow, cut off or off-topic. The guardian note is always ours.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp

from app.agents import schemas as s
from app.agents.guardrails import ensure_llm_consent, hand_off, screen_message
from app.agents.models import (
    AgentChannel,
    AgentConversation,
    AgentKind,
    ConversationStatus,
    GuardrailFlag,
    MessageRole,
)
from app.agents.runner import ConversationLog, complete_sentences, run_agent
from app.agents.tools import AgentTool, ApiCaller
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.domains.auth.models import User
from app.domains.caregivers.matching import match_context
from app.domains.caregivers.models import CaregiverTier
from app.domains.caregivers.schemas import CaregiverMatch, Languages, MatchOut
from app.integrations.llm import LLMClient

TOOL = "match_caregivers"

# Short on purpose: the model reads ~14 tokens/s on CPU.
SYSTEM_PROMPT = (
    "You help a family choose a caregiver on CareKin. Call match_caregivers once. "
    "Set languages or min_tier only if the family asked for them. Then, in two or three "
    "short sentences, say who fits best and why, using only the tool's data. "
    "Never give medical advice. Never say a booking is made."
)

# Same rule as bookings (TS-3): a minor, or a patient without their own login.
GUARDIAN_NOTE = "A guardian will need to approve the caregiver before this booking goes ahead."

_TIER_WORDS = {
    "companion": CaregiverTier.COMPANION,
    "trained": CaregiverTier.TRAINED_CARER,
    "trained carer": CaregiverTier.TRAINED_CARER,
    "carer": CaregiverTier.TRAINED_CARER,
    "nurse": CaregiverTier.LICENSED_NURSE,
    "licensed nurse": CaregiverTier.LICENSED_NURSE,
}


class MatchPreferences(BaseModel):
    """What the model may set. Forgiving: a 3B model writes "nurse" and "igbo"."""

    model_config = ConfigDict(extra="ignore")

    languages: Languages = Field(default_factory=list)
    min_tier: CaregiverTier | None = None

    @field_validator("languages", mode="before")
    @classmethod
    def _one_language(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part for part in re.split(r"\s*(?:,|\band\b)\s*", value) if part]
        return [] if value is None else value

    @field_validator("min_tier", mode="before")
    @classmethod
    def _tier_words(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        words = " ".join(value.replace("_", " ").lower().split())
        if words in ("", "any", "none", "null"):
            return None
        return _TIER_WORDS.get(words, value)


def _for_model(result: dict[str, Any]) -> dict[str, Any]:
    """Only what the explanation needs, best first: no ids, prices or internal scores.

    The live model quoted "a score of 79.7" to a family when it saw the score.
    """
    return {
        "caregivers": [
            {
                "name": match["full_name"],
                "tier": match["tier"],
                "km": match["distance_km"],
                "rating": match["avg_rating"],
                "reviews": match["rating_count"],
                "years": match["years_experience"],
                "languages": match["languages"],
                "matches_languages": match["languages_matched"],
            }
            for match in result["results"]
        ],
    }


MATCH_TOOL = AgentTool(
    name=TOOL,
    description=(
        "Rank caregivers who are free for this patient's booking. The patient and time "
        "are already filled in."
    ),
    args=MatchPreferences,
    method="POST",
    path="/caregivers/match",
    for_model=_for_model,
    parameters={
        "type": "object",
        "properties": {
            "languages": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Languages the family asked for, e.g. ["igbo"]. Omit if none.',
            },
            "min_tier": {
                "type": "string",
                "enum": [tier.value for tier in CaregiverTier],
                "description": "Only if they asked for a trained carer or a nurse.",
            },
        },
        "required": [],
    },
)


# ---------------------------------------------------------------- replies
def _naira(minor: int) -> str:
    return f"₦{minor / 100:,.0f}"


def fallback_reply(out: MatchOut) -> str:
    """The reply built without the model, from the results alone."""
    if not out.results:
        return (
            "No caregiver is free for that time near the patient. "
            "Try another time, or a different location."
        )
    best: CaregiverMatch = out.results[0]
    tier = best.tier.value.replace("_", " ")
    rating = (
        f"rated {best.avg_rating} by {best.rating_count} families"
        if best.avg_rating is not None and best.rating_count
        else "new to CareKin"
    )
    speaks = ", ".join(lang.title() for lang in best.languages) or "not listed"
    reply = (
        f"Best match: {best.full_name}, a {tier}, {best.distance_km} km away, {rating}. "
        f"Speaks {speaks}. About {_naira(best.estimated_total_minor)} for this booking."
    )
    others = len(out.results) - 1
    if others:
        reply += f" {others} more caregiver{'s' if others > 1 else ''} also fit{'' if others > 1 else 's'}."
    return reply


# ---------------------------------------------------------------- the request
async def _conversation(
    db: AsyncSession, user: User, data: s.AgentMatchRequest
) -> AgentConversation:
    if data.session_id is None:
        conversation = AgentConversation(
            agent=AgentKind.MATCHING,
            user_id=user.id,
            patient_id=data.patient_id,
            channel=AgentChannel(data.channel),
        )
        db.add(conversation)
        await db.commit()
        return conversation
    conversation = await db.get(AgentConversation, data.session_id)
    if (
        conversation is None
        or conversation.user_id != user.id
        or conversation.agent is not AgentKind.MATCHING
    ):
        raise NotFoundError("Conversation not found", code="conversation_not_found")
    if conversation.status is ConversationStatus.HANDED_OFF:
        raise ConflictError(
            "A member of the CareKin team has taken over this conversation. "
            "Start a new search to look for caregivers again.",
            code="conversation_handed_off",
        )
    if conversation.patient_id != data.patient_id:
        raise ConflictError(
            "This conversation is about another patient. Start a new search.",
            code="conversation_patient_mismatch",
        )
    return conversation


async def run_match(
    db: AsyncSession, user: User, data: s.AgentMatchRequest, *, app: ASGIApp, llm: LLMClient
) -> s.AgentMatchOut:
    # 1. Cheap refusals before the slow part.
    if data.starts_at <= datetime.now(UTC):
        raise BadRequestError("The time slot must start in the future", code="slot_in_past")
    await ensure_llm_consent(db, user.id)
    context = await match_context(db, user, data.patient_id)  # 404/403, like /caregivers/match

    # 2. The conversation.
    conversation = await _conversation(db, user, data)
    log = await ConversationLog.open(db, conversation)

    # 3. Guardrails on the message.
    flag = screen_message(data.message)
    if flag is not GuardrailFlag.NONE:
        await log.add(MessageRole.USER, data.message, flag=flag)
        reply, _ = await hand_off(db, log, flag)
        return s.AgentMatchOut(
            session_id=conversation.id,
            status="handed_off",
            reply=reply,
            reply_source="handoff",
            results=[],
            guardian_approval_required=context.guardian_approval_required,
            min_tier_applied=None,
        )

    # 4. The loop, with the booking fixed from the request.
    fixed: dict[str, Any] = {
        "patient_id": str(data.patient_id),
        "booking_type": data.booking_type.value,
        "starts_at": data.starts_at.isoformat(),
        "ends_at": data.ends_at.isoformat(),
        "limit": data.limit,
    }
    if data.service_location is not None:
        fixed["service_location"] = data.service_location.model_dump()
    outcome = await run_agent(
        llm=llm,
        log=log,
        caller=ApiCaller(app, user.id, user.role),
        system=SYSTEM_PROMPT,
        user_text=data.message,
        tools=[MATCH_TOOL],
        required=TOOL,
        fixed={TOOL: fixed},
    )
    out = MatchOut.model_validate(outcome.result)

    # 5. The reply.
    text = outcome.text
    if text is not None and outcome.truncated:
        text = complete_sentences(text)
    if text is not None:
        source = "model"
    else:
        text, source = fallback_reply(out), "fallback"
        await log.add(MessageRole.SYSTEM, text)
    if out.guardian_approval_required:
        await log.add(MessageRole.SYSTEM, GUARDIAN_NOTE)
        text = f"{text}\n\n{GUARDIAN_NOTE}"
    return s.AgentMatchOut(
        session_id=conversation.id,
        status="answered",
        reply=text,
        reply_source=source,
        results=out.results,
        guardian_approval_required=out.guardian_approval_required,
        min_tier_applied=out.min_tier_applied,
    )
