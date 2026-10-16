"""crisis routes (kickoff 2.6, PRD 7.2).

POST /crisis/events                   press the button
GET  /crisis/events/{id}              status, including each alert's delivery
GET  /crisis/events/{id}/facilities   nearest verified SCD-capable facilities
POST /crisis/events/{id}/resolve      close it, optionally into the passport
POST /crisis/alert                    resend failed alerts and alert new contacts
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status

from app import database
from app.core.permissions import UserRole, require_roles
from app.database import DbSession
from app.domains.auth.models import User
from app.domains.crisis import service
from app.domains.crisis.schemas import (
    CrisisAlertRetryIn,
    CrisisAlertRetryOut,
    CrisisEventOut,
    CrisisResolveIn,
    CrisisResolveOut,
    CrisisTriggerIn,
    CrisisTriggerOut,
    FacilitiesOut,
)
from app.integrations.sms import SMSClient, get_sms_client

router = APIRouter(prefix="/crisis", tags=["crisis"])

# Patients and guardians press the button; ops and (if shared) family follow it.
CanTrigger = Annotated[User, require_roles(UserRole.PATIENT, UserRole.GUARDIAN)]
CanFollow = Annotated[
    User,
    require_roles(UserRole.PATIENT, UserRole.GUARDIAN, UserRole.FAMILY_ABROAD, UserRole.OPS),
]
CanManage = Annotated[User, require_roles(UserRole.PATIENT, UserRole.GUARDIAN, UserRole.OPS)]
Sms = Annotated[SMSClient, Depends(get_sms_client)]

_errors: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not logged in"},
    403: {"description": "Account type or relationship not allowed"},
    404: {"description": "Not found, or not yours to see"},
}


def _send_alerts(
    background: BackgroundTasks, sms: SMSClient, event_id: uuid.UUID, **kwargs: Any
) -> None:
    """Deliver in this process after the response; Celery Beat retries what fails."""
    background.add_task(
        service.deliver_pending_alerts, database.SessionLocal, sms, event_id=event_id, **kwargs
    )


@router.post(
    "/events",
    response_model=CrisisTriggerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Press the crisis button",
    responses={
        200: {
            "description": "Same press sent again, or a crisis is already active: the "
            "existing event is returned and nobody is alerted twice."
        },
        409: {"description": "client_event_id was already used for another patient"},
        **_errors,
    },
)
async def trigger_crisis(
    data: CrisisTriggerIn,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: DbSession,
    user: CanTrigger,
    sms: Sms,
) -> dict[str, Any]:
    result = await service.trigger_crisis(db, user, data, request)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    # Also on replays: picks up alerts left queued if an earlier attempt was cut off.
    _send_alerts(background, sms, result.event_id)
    event = await service.load_event(db, result.event_id)
    nearby = await service.nearby_facilities(db, event)
    return {**service.event_out(event), "nearby": service.facilities_out(event, nearby)}


@router.get("/events/{event_id}", response_model=CrisisEventOut, responses=_errors)
async def get_crisis_event(event_id: uuid.UUID, db: DbSession, user: CanFollow) -> dict[str, Any]:
    event = await service.event_for(db, user, event_id, "follow")
    return service.event_out(event)


@router.get("/events/{event_id}/facilities", response_model=FacilitiesOut, responses=_errors)
async def get_crisis_facilities(
    event_id: uuid.UUID, db: DbSession, user: CanFollow
) -> dict[str, Any]:
    event = await service.event_for(db, user, event_id, "follow")
    nearby = await service.nearby_facilities(db, event)
    return service.facilities_out(event, nearby)


@router.post(
    "/events/{event_id}/resolve",
    response_model=CrisisResolveOut,
    responses={409: {"description": "Already closed"}, **_errors},
)
async def resolve_crisis(
    event_id: uuid.UUID,
    data: CrisisResolveIn,
    request: Request,
    db: DbSession,
    user: CanManage,
) -> dict[str, Any]:
    entry_id = await service.resolve_crisis(db, user, event_id, data, request)
    event = await service.load_event(db, event_id)
    return {**service.event_out(event), "passport_entry_id": entry_id}


@router.post(
    "/alert",
    response_model=CrisisAlertRetryOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resend failed crisis alerts and alert newly added contacts",
    responses={409: {"description": "Crisis already closed"}, **_errors},
)
async def retry_crisis_alerts(
    data: CrisisAlertRetryIn,
    request: Request,
    background: BackgroundTasks,
    db: DbSession,
    user: CanManage,
    sms: Sms,
) -> dict[str, Any]:
    result = await service.retry_alerts(db, user, data.crisis_event_id, request)
    _send_alerts(background, sms, data.crisis_event_id, ignore_max_attempts=True)
    event = await service.load_event(db, data.crisis_event_id)
    return {
        "crisis_event_id": event.id,
        "alerts": [service.alert_out(a) for a in event.alerts],
        "new_recipients": result.new_recipients,
        "queued": result.queued,
    }
