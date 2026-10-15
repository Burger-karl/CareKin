"""caregivers routes (Day 6). Thin: parse the request, call the service, return the schema."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.core.permissions import UserRole, require_roles
from app.database import DbSession
from app.domains.auth.dependencies import CurrentUser
from app.domains.auth.models import User
from app.domains.caregivers import schemas as s
from app.domains.caregivers import service
from app.domains.caregivers.models import CertType
from app.integrations.background_check import (
    BackgroundCheckClient,
    get_background_check_client,
)
from app.integrations.document_store import DocumentStore, get_document_store
from app.integrations.nin import NINClient, get_nin_client

router = APIRouter(prefix="/caregivers", tags=["caregivers"])

CaregiverUser = Annotated[User, require_roles(UserRole.CAREGIVER)]
CaregiverOrOps = Annotated[User, require_roles(UserRole.CAREGIVER, UserRole.OPS)]
OpsUser = Annotated[User, require_roles(UserRole.OPS)]


@router.post("", response_model=s.CaregiverOut, status_code=status.HTTP_201_CREATED)
async def create_caregiver(
    data: s.CaregiverCreateRequest, user: CaregiverUser, db: DbSession, request: Request
) -> s.CaregiverOut:
    """Apply as a caregiver with a tier and exactly two guarantors (ON-4)."""
    return await service.create_caregiver(db, user, data, request)


@router.get("/{caregiver_id}", response_model=s.CaregiverOut | s.CaregiverPublicOut)
async def get_caregiver(
    caregiver_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> s.CaregiverOut | s.CaregiverPublicOut:
    """The owner and ops see everything; anyone else sees an approved caregiver's public profile."""
    return await service.get_caregiver(db, user, caregiver_id)


@router.patch("/{caregiver_id}", response_model=s.CaregiverOut)
async def update_caregiver(
    caregiver_id: uuid.UUID,
    data: s.CaregiverUpdateRequest,
    user: CaregiverOrOps,
    db: DbSession,
    request: Request,
) -> s.CaregiverOut:
    return await service.update_caregiver(db, user, caregiver_id, data, request)


@router.post(
    "/{caregiver_id}/certifications",
    response_model=s.CertificationOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_certification(
    caregiver_id: uuid.UUID,
    user: CaregiverOrOps,
    db: DbSession,
    request: Request,
    store: Annotated[DocumentStore, Depends(get_document_store)],
    cert_type: Annotated[CertType, Form()],
    title: Annotated[str, Form()],
    issuer: Annotated[str | None, Form()] = None,
    licence_number: Annotated[str | None, Form()] = None,
    expires_at: Annotated[date | None, Form()] = None,
    file: Annotated[UploadFile | None, File(description="PDF, JPG or PNG, up to 5 MB")] = None,
) -> s.CertificationOut:
    """Upload a certification document, or record an academy module (no file needed)."""
    try:
        data = s.CertificationCreateRequest(
            cert_type=cert_type,
            title=title,
            issuer=issuer or None,
            licence_number=licence_number or None,
            expires_at=expires_at,
        )
    except ValidationError as exc:
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in exc.errors()]
        ) from exc
    return await service.add_certification(db, user, caregiver_id, data, file, store, request)


@router.get("/{caregiver_id}/availability", response_model=s.AvailabilityOut)
async def get_availability(
    caregiver_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> s.AvailabilityOut:
    return await service.get_availability(db, user, caregiver_id)


@router.post("/{caregiver_id}/availability", response_model=s.AvailabilityOut)
async def set_availability(
    caregiver_id: uuid.UUID,
    data: s.AvailabilitySetRequest,
    user: CaregiverOrOps,
    db: DbSession,
    request: Request,
) -> s.AvailabilityOut:
    """Replaces the caregiver's whole weekly schedule. Send an empty list to clear it."""
    return await service.set_availability(db, user, caregiver_id, data, request)


@router.post("/{caregiver_id}/verify", response_model=s.VerifyOut)
async def verify_caregiver(
    caregiver_id: uuid.UUID,
    data: s.VerifyRequest,
    user: OpsUser,
    db: DbSession,
    request: Request,
    nin_client: Annotated[NINClient, Depends(get_nin_client)],
    background_client: Annotated[BackgroundCheckClient, Depends(get_background_check_client)],
) -> s.VerifyOut:
    """Ops runs the NIN and background checks. Results are audited; the NIN is never stored."""
    return await service.verify_caregiver(
        db, user, caregiver_id, data, nin_client, background_client, request
    )