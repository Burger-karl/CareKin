"""trust routes: incidents and disputes (Day 11 in the endpoint plan, TS-2 and TS-5)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.database import DbSession
from app.domains.auth.dependencies import CurrentUser
from app.domains.bookings.notifications import Notifier
from app.domains.bookings.router import get_notifier
from app.domains.trust import service
from app.domains.trust.models import IncidentStatus
from app.domains.trust.schemas import IncidentCreate, IncidentRead, IncidentUpdate
from app.integrations.escrow import EscrowClient, get_escrow_client

router = APIRouter(prefix="/incidents", tags=["trust"])


def get_actor(request: Request, user: CurrentUser) -> service.Actor:
    return service.Actor(user_id=user.id, role=user.role, request=request)


Actor = Annotated[service.Actor, Depends(get_actor)]
Notices = Annotated[Notifier, Depends(get_notifier)]
Escrow = Annotated[EscrowClient, Depends(get_escrow_client)]


@router.post("", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
async def create_incident(
    data: IncidentCreate, db: DbSession, actor: Actor, escrow: Escrow, notifier: Notices
):
    """Report an incident. With is_dispute, the booking's payment is held (TS-5)."""
    return await service.create_incident(db, actor, data, escrow_client=escrow, notifier=notifier)


@router.get("", response_model=list[IncidentRead])
async def list_incidents(
    db: DbSession,
    actor: Actor,
    status_filter: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    booking_id: uuid.UUID | None = None,
    disputes_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Ops: the incident queue by response deadline. Others: the incidents they reported."""
    return await service.list_incidents(
        db,
        actor,
        status=status_filter,
        booking_id=booking_id,
        disputes_only=disputes_only,
        limit=limit,
        offset=offset,
    )


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(incident_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.get_incident(db, actor, incident_id)


@router.patch("/{incident_id}", response_model=IncidentRead)
async def update_incident(
    incident_id: uuid.UUID,
    data: IncidentUpdate,
    db: DbSession,
    actor: Actor,
    escrow: Escrow,
    notifier: Notices,
):
    """Ops: update status, severity, assignment or resolution, or flag a dispute. Closing
    the last open dispute on a booking ends the payment hold."""
    return await service.update_incident(
        db, actor, incident_id, data, escrow_client=escrow, notifier=notifier
    )
