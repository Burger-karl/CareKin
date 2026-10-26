"""Agent routes (Days 14 and 15). Thin: parse the request, call the agent, return the schema.

Three routers: /agents (the agents), /patients/{id}/reminders (what the companion
sends; also its tools), and /webhooks/whatsapp (Meta calls it, no login).
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, status
from fastapi.responses import PlainTextResponse
from redis.asyncio import Redis

from app.agents import companion, matching, reminders
from app.agents import schemas as s
from app.core.exceptions import BadRequestError, ForbiddenError
from app.core.permissions import UserRole, require_roles
from app.core.redis import get_redis
from app.database import DbSession
from app.domains.auth.models import User
from app.integrations.llm import LLMClient, get_llm_client
from app.integrations.whatsapp import (
    WebhookVerificationFailed,
    WhatsAppClient,
    get_whatsapp_client,
    parse_incoming,
)

router = APIRouter(prefix="/agents", tags=["agents"])
reminders_router = APIRouter(prefix="/patients", tags=["reminders"])
webhooks_router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# The patient or a guardian (whoever may book), and ops: the same as /caregivers/match.
Matcher = Annotated[User, require_roles(UserRole.PATIENT, UserRole.GUARDIAN, UserRole.OPS)]
# The companion is for patients and their families.
Companion = Annotated[User, require_roles(UserRole.PATIENT, UserRole.GUARDIAN)]
# Owners read their own conversations; ops read handed-off ones.
HistoryReader = Annotated[User, require_roles(UserRole.PATIENT, UserRole.GUARDIAN, UserRole.OPS)]
LLM = Annotated[LLMClient, Depends(get_llm_client)]
WhatsApp = Annotated[WhatsAppClient, Depends(get_whatsapp_client)]


@router.post("/match", response_model=s.AgentMatchOut)
async def match(
    data: s.AgentMatchRequest,
    user: Matcher,
    db: DbSession,
    request: Request,
    llm: LLM,
) -> s.AgentMatchOut:
    """Rank caregivers from what the family wrote, and explain the ranking (PRD section 8).

    Slow on CPU: allow up to a minute or two. Needs `llm_processing` consent.
    Medical questions or emergencies hand off to a person instead (`status: handed_off`).
    Never books.
    """
    return await matching.run_match(db, user, data, app=request.app, llm=llm)


@router.post("/companion/message", response_model=s.CompanionMessageOut)
async def companion_message(
    data: s.CompanionMessageRequest,
    user: Companion,
    db: DbSession,
    request: Request,
    llm: LLM,
) -> s.CompanionMessageOut:
    """One message to the care companion (PRD section 8): reminders and check-ins.

    Slow on CPU: allow up to a minute or two. Needs `llm_processing` consent. Never
    gives medical advice: an emergency or a medical question hands off to a person
    (`status: handed_off`).
    """
    return await companion.run_message(db, user, data, app=request.app, llm=llm)


@router.get("/companion/history/{session_id}", response_model=s.ConversationHistoryOut)
async def companion_history(
    session_id: uuid.UUID, user: HistoryReader, db: DbSession, request: Request
) -> s.ConversationHistoryOut:
    """A conversation's messages, oldest first. Owners see what they were shown; ops see
    the full log of a conversation handed to a person. Every read is audited."""
    return await companion.history(db, user, session_id, request=request)


# ---------------------------------------------------------------- reminders
@reminders_router.get("/{patient_id}/reminders", response_model=list[s.ReminderRead])
async def list_reminders(
    patient_id: uuid.UUID, user: Companion, db: DbSession
) -> list[s.ReminderRead]:
    """Your reminders for this patient (each person gets the ones they set), active first."""
    return await reminders.list_reminders(db, user, patient_id)


@reminders_router.post(
    "/{patient_id}/reminders", response_model=s.ReminderRead, status_code=status.HTTP_201_CREATED
)
async def create_reminder(
    patient_id: uuid.UUID, data: s.ReminderCreate, user: Companion, db: DbSession, request: Request
) -> s.ReminderRead:
    """Set a reminder. It is sent to you, by WhatsApp or SMS, in fixed wording."""
    return await reminders.create_reminder(db, user, patient_id, data, request=request)


@reminders_router.patch("/{patient_id}/reminders/{reminder_id}", response_model=s.ReminderRead)
async def update_reminder(
    patient_id: uuid.UUID,
    reminder_id: uuid.UUID,
    data: s.ReminderUpdate,
    user: Companion,
    db: DbSession,
    request: Request,
) -> s.ReminderRead:
    """Change a reminder's time, days or channel, or switch it off (`active: false`)."""
    return await reminders.update_reminder(db, user, patient_id, reminder_id, data, request=request)


# ---------------------------------------------------------------- WhatsApp webhook
@webhooks_router.get("/whatsapp", response_class=PlainTextResponse)
async def whatsapp_verify(
    whatsapp: WhatsApp,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str, Query(alias="hub.challenge")] = "",
) -> str:
    """Meta's subscription handshake: echo the challenge if the verify token matches."""
    try:
        return whatsapp.verify_subscription(mode, token, challenge)
    except WebhookVerificationFailed:
        raise ForbiddenError(
            "WhatsApp webhook verification failed", code="webhook_verification_failed"
        ) from None


@webhooks_router.post("/whatsapp")
async def whatsapp_inbound(
    request: Request,
    background: BackgroundTasks,
    whatsapp: WhatsApp,
    llm: LLM,
    redis: Annotated[Redis, Depends(get_redis)],
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Inbound WhatsApp messages into the companion. Signed by Meta (X-Hub-Signature-256).

    Answers 200 at once and replies by WhatsApp after: the model takes longer than Meta
    waits. A message Meta sends twice is answered once.
    """
    body = await request.body()
    if not whatsapp.verify_signature(body, x_hub_signature_256):
        raise ForbiddenError("Invalid webhook signature", code="invalid_signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise BadRequestError("Webhook body is not JSON", code="invalid_payload") from None
    if not isinstance(payload, dict):
        raise BadRequestError("Webhook body is not a JSON object", code="invalid_payload")
    messages = parse_incoming(payload)
    fresh = await companion.accept_whatsapp(redis, messages)
    for message in fresh:
        background.add_task(
            companion.handle_whatsapp, request.app, message, whatsapp=whatsapp, llm=llm
        )
    return {"received": len(messages), "accepted": len(fresh)}
