"""Agent routes (Day 14). Thin: parse the request, call the agent, return the schema."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.agents import matching
from app.agents import schemas as s
from app.core.permissions import UserRole, require_roles
from app.database import DbSession
from app.domains.auth.models import User
from app.integrations.llm import LLMClient, get_llm_client

router = APIRouter(prefix="/agents", tags=["agents"])

# The patient or a guardian (whoever may book), and ops: the same as /caregivers/match.
Matcher = Annotated[User, require_roles(UserRole.PATIENT, UserRole.GUARDIAN, UserRole.OPS)]


@router.post("/match", response_model=s.AgentMatchOut)
async def match(
    data: s.AgentMatchRequest,
    user: Matcher,
    db: DbSession,
    request: Request,
    llm: Annotated[LLMClient, Depends(get_llm_client)],
) -> s.AgentMatchOut:
    """Rank caregivers from what the family wrote, and explain the ranking (PRD section 8).

    Slow on CPU: allow up to a minute or two. Needs `llm_processing` consent.
    Medical questions or emergencies hand off to a person instead (`status: handed_off`).
    Never books.
    """
    return await matching.run_match(db, user, data, app=request.app, llm=llm)
