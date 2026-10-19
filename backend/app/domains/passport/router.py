"""passport routes: entries, the record, the one-page PDF, and shares (Day 9, HP-1 to HP-4)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import AwareDatetime

from app.database import DbSession
from app.domains.auth.dependencies import CurrentUser
from app.domains.passport import service
from app.domains.passport.models import PassportEntryType
from app.domains.passport.schemas import (
    PassportEntryCreate,
    PassportEntryRead,
    PassportRead,
    PassportShareCreate,
    PassportShareRead,
    ReceivedShareRead,
)

router = APIRouter(prefix="/passport", tags=["passport"])


def get_actor(request: Request, user: CurrentUser) -> service.Actor:
    return service.Actor(user_id=user.id, role=user.role, request=request)


Actor = Annotated[service.Actor, Depends(get_actor)]
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


# Declared before /{patient_id} so "shares" isn't read as a patient id.
@router.get("/shares/received", response_model=list[ReceivedShareRead])
async def received_shares(db: DbSession, actor: Actor):
    """Facility portal: patients who are sharing their passport with your facility now."""
    return await service.received_shares(db, actor)


@router.get("/{patient_id}", response_model=PassportRead)
async def get_passport(
    patient_id: uuid.UUID,
    db: DbSession,
    actor: Actor,
    response: Response,
    entry_type: Annotated[list[PassportEntryType] | None, Query()] = None,
    since: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """The record, newest first. Every view is audited (HP-4)."""
    response.headers.update(NO_STORE)
    return await service.get_passport(
        db, actor, patient_id, entry_types=entry_type, since=since, limit=limit, offset=offset
    )


@router.post(
    "/{patient_id}/entries",
    response_model=PassportEntryRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_entry(patient_id: uuid.UUID, data: PassportEntryCreate, db: DbSession, actor: Actor):
    """Add a crisis, trigger, visit, transfusion or medication entry (encrypted)."""
    return await service.add_entry(db, actor, patient_id, data)


@router.get(
    "/{patient_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "One-page PDF"}},
)
async def export_passport(patient_id: uuid.UUID, db: DbSession, actor: Actor):
    """One-page PDF for a doctor (HP-2). Not cached anywhere; audited."""
    content, result = await service.export_passport(db, actor, patient_id)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            **NO_STORE,
            "Content-Disposition": 'attachment; filename="carekin-health-passport.pdf"',
            "X-Entries-Shown": str(result.entries_shown),
            "X-Entries-Total": str(result.entries_total),
        },
    )


@router.get("/{patient_id}/shares", response_model=list[PassportShareRead])
async def list_shares(patient_id: uuid.UUID, db: DbSession, actor: Actor):
    """Every share, active or not, for the share management screen."""
    return await service.list_shares(db, actor, patient_id)


@router.post(
    "/{patient_id}/share",
    response_model=PassportShareRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_share(
    patient_id: uuid.UUID, data: PassportShareCreate, db: DbSession, actor: Actor
):
    """Share with a verified facility for a limited time (HP-3). Records the consent."""
    return await service.create_share(db, actor, patient_id, data)


@router.delete("/{patient_id}/share/{share_id}", response_model=PassportShareRead)
async def revoke_share(patient_id: uuid.UUID, share_id: uuid.UUID, db: DbSession, actor: Actor):
    """Revoke a share now. The facility gets 403 from then on."""
    return await service.revoke_share(db, actor, patient_id, share_id)
