"""crisis routes (kickoff 2.6, PRD 7.2).

POST /crisis/events                   press the button
GET  /crisis/events/{id}              status, including each alert's delivery
GET  /crisis/events/{id}/facilities   nearest verified SCD-capable facilities
POST /crisis/events/{id}/resolve      close it, optionally into the passport
POST /crisis/alert                    resend failed alerts and alert new contacts

One-tap crisis without signing in (a device the patient or a guardian set up):
POST   /crisis/device-keys                       turn it on for this device (signed in)
GET    /crisis/device-keys?patient_id=           devices that can send alerts (signed in)
DELETE /crisis/device-keys/{id}                  switch a device off (signed in)
POST   /crisis/device-press                      press, with X-Crisis-Key
GET    /crisis/device-press/{event_id}           follow that crisis, with X-Crisis-Key
POST   /crisis/device-press/{event_id}/cancel    pressed by mistake, with X-Crisis-Key
DELETE /crisis/device-press                      the device forgets its own key
POST   /crisis/anonymous-press                   no key: logged for ops, nobody alerted
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, Response, status
from redis.exceptions import RedisError

from app import database
from app.core.audit import client_ip
from app.core.exceptions import RateLimitedError
from app.core.permissions import UserRole, require_roles
from app.core.redis import RedisClient
from app.database import DbSession
from app.domains.auth.models import User
from app.domains.crisis import service
from app.domains.crisis.schemas import (
    AnonymousPressIn,
    AnonymousPressOut,
    CrisisAlertRetryIn,
    CrisisAlertRetryOut,
    CrisisEventOut,
    CrisisResolveIn,
    CrisisResolveOut,
    CrisisTriggerIn,
    CrisisTriggerOut,
    DeviceEventOut,
    DeviceKeyCreatedOut,
    DeviceKeyCreateIn,
    DeviceKeyOut,
    DevicePressIn,
    DevicePressOut,
    FacilitiesOut,
)
from app.integrations.sms import SMSClient, get_sms_client

logger = logging.getLogger(__name__)

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


# ------------------------------------------------------------------ device keys
CrisisKey = Annotated[
    str | None,
    Header(
        alias="X-Crisis-Key",
        description="The key this device got when one-tap crisis was turned on.",
    ),
]
# Generous: the real protection against repeat alerts is the duplicate window in the service.
DEVICE_PRESSES_PER_HOUR = 20
ANONYMOUS_PRESSES_PER_HOUR = 10


async def _limit(redis, bucket: str, limit: int) -> None:
    """Count a request against an hourly limit. Fails open: Redis being down must never
    stop a crisis alert."""
    try:
        count = await redis.incr(bucket)
        if count == 1:
            await redis.expire(bucket, 3600)
        if count > limit:
            ttl = await redis.ttl(bucket)
            raise RateLimitedError(
                "Too many presses from this device. Call the emergency number now.",
                code="crisis_rate_limited",
                retry_after=ttl if ttl > 0 else 3600,
            )
    except RedisError:
        logger.warning("Redis unavailable; crisis rate limit skipped")


def _key_bucket(raw: str | None) -> str:
    # Hashed, so the raw key never lands in Redis.
    return "crisis:device:" + hashlib.sha256((raw or "").encode()).hexdigest()[:32]


@router.post(
    "/device-keys",
    response_model=DeviceKeyCreatedOut,
    status_code=status.HTTP_201_CREATED,
    summary="Turn on one-tap crisis on this device",
    responses={409: {"description": "Too many devices for this patient"}, **_errors},
)
async def create_device_key(
    data: DeviceKeyCreateIn, request: Request, db: DbSession, user: CanTrigger
) -> dict[str, Any]:
    key, raw = await service.create_device_key(db, user, data, request)
    return {**service.device_key_out(key, user.id), "key": raw}


@router.get("/device-keys", response_model=list[DeviceKeyOut], responses=_errors)
async def list_device_keys(
    db: DbSession, user: CanTrigger, patient_id: Annotated[uuid.UUID, Query()]
) -> list[dict[str, Any]]:
    keys = await service.list_device_keys(db, user, patient_id)
    return [service.device_key_out(k, user.id) for k in keys]


@router.delete("/device-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_errors)
async def revoke_device_key(
    key_id: uuid.UUID, request: Request, db: DbSession, user: CanTrigger
) -> Response:
    await service.revoke_device_key(db, user, key_id, request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/device-press",
    response_model=DevicePressOut,
    status_code=status.HTTP_201_CREATED,
    summary="Press the crisis button from a set-up device, without signing in",
    responses={
        200: {"description": "Same press again, or a crisis is already active"},
        401: {"description": "No key, or it was switched off"},
        429: {"description": "Too many presses from this device"},
    },
)
async def device_press(
    data: DevicePressIn,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: DbSession,
    redis: RedisClient,
    sms: Sms,
    crisis_key: CrisisKey = None,
) -> dict[str, Any]:
    await _limit(redis, _key_bucket(crisis_key), DEVICE_PRESSES_PER_HOUR)
    result, first_name = await service.trigger_with_device_key(db, crisis_key, data, request)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    _send_alerts(background, sms, result.event_id)
    event = await service.load_event(db, result.event_id)
    nearby = await service.nearby_facilities(db, event)
    return {
        **service.event_out(event),
        "nearby": service.facilities_out(event, nearby),
        "patient_first_name": first_name,
    }


@router.get(
    "/device-press/{event_id}",
    response_model=DeviceEventOut,
    responses={
        401: {"description": "No key, or it was switched off"},
        404: {"description": "Not found"},
    },
)
async def device_event(
    event_id: uuid.UUID, db: DbSession, crisis_key: CrisisKey = None
) -> dict[str, Any]:
    event, first_name = await service.device_event(db, crisis_key, event_id)
    nearby = await service.nearby_facilities(db, event)
    return {
        **service.event_out(event),
        "nearby": service.facilities_out(event, nearby),
        "patient_first_name": first_name,
    }


@router.post(
    "/device-press/{event_id}/cancel",
    response_model=DeviceEventOut,
    summary="Pressed by mistake: close it as a false alarm",
    responses={409: {"description": "Already closed"}, 401: {"description": "Bad key"}},
)
async def cancel_device_event(
    event_id: uuid.UUID, request: Request, db: DbSession, crisis_key: CrisisKey = None
) -> dict[str, Any]:
    await service.cancel_device_event(db, crisis_key, event_id, request)
    event, first_name = await service.device_event(db, crisis_key, event_id)
    nearby = await service.nearby_facilities(db, event)
    return {
        **service.event_out(event),
        "nearby": service.facilities_out(event, nearby),
        "patient_first_name": first_name,
    }


@router.delete(
    "/device-press",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="This device stops sending crisis alerts",
    responses={401: {"description": "No key, or already switched off"}},
)
async def forget_device_key(
    request: Request, db: DbSession, crisis_key: CrisisKey = None
) -> Response:
    await service.forget_device_key(db, crisis_key, request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/anonymous-press",
    response_model=AnonymousPressOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="A press from a device that isn't set up: logged for ops, nobody is alerted",
    responses={429: {"description": "Too many presses from this network"}},
)
async def anonymous_press(
    data: AnonymousPressIn, request: Request, db: DbSession, redis: RedisClient
) -> dict[str, Any]:
    await _limit(
        redis, f"crisis:anon:{client_ip(request) or 'unknown'}", ANONYMOUS_PRESSES_PER_HOUR
    )
    await service.log_anonymous_press(db, data, request)
    return {"logged": True}
