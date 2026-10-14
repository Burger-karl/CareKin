"""patients routes: Patient, Guardian, EmergencyContact, CareProfile, FamilyLink.

Errors are CareKinError subclasses, rendered by app.core.exceptions.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.database import DbSession
from app.domains.auth.dependencies import CurrentUser
from app.domains.patients import service
from app.domains.patients.schemas import (
    CareProfileRead,
    CareProfileWrite,
    EmergencyContactCreate,
    EmergencyContactRead,
    EmergencyContactUpdate,
    FamilyLinkCreate,
    FamilyLinkRead,
    FamilyLinkUpdate,
    GuardianCreate,
    GuardianRead,
    GuardianUpdate,
    PatientCreate,
    PatientRead,
    PatientUpdate,
)

router = APIRouter(prefix="/patients", tags=["patients"])


def get_actor(request: Request, user: CurrentUser) -> service.Actor:
    return service.Actor(user_id=user.id, role=user.role, request=request)


Actor = Annotated[service.Actor, Depends(get_actor)]


# ---------------------------------------------------------------- patients
@router.get("", response_model=list[PatientRead])
async def list_patients(db: DbSession, actor: Actor):
    """Patients the caller can see: their own record, guardianships and family links."""
    return await service.list_patients(db, actor)


@router.post("", response_model=PatientRead, status_code=status.HTTP_201_CREATED)
async def create_patient(data: PatientCreate, db: DbSession, actor: Actor):
    return await service.create_patient(db, actor, data)


@router.get("/{patient_id}", response_model=PatientRead)
async def get_patient(patient_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.get_patient(db, actor, patient_id)


@router.patch("/{patient_id}", response_model=PatientRead)
async def update_patient(patient_id: uuid.UUID, data: PatientUpdate, db: DbSession, actor: Actor):
    return await service.update_patient(db, actor, patient_id, data)


# ---------------------------------------------------------------- guardians
@router.get("/{patient_id}/guardians", response_model=list[GuardianRead])
async def list_guardians(patient_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.list_guardians(db, actor, patient_id)


@router.post(
    "/{patient_id}/guardians", response_model=GuardianRead, status_code=status.HTTP_201_CREATED
)
async def add_guardian(patient_id: uuid.UUID, data: GuardianCreate, db: DbSession, actor: Actor):
    return await service.add_guardian(db, actor, patient_id, data)


@router.patch("/{patient_id}/guardians/{guardian_id}", response_model=GuardianRead)
async def update_guardian(
    patient_id: uuid.UUID,
    guardian_id: uuid.UUID,
    data: GuardianUpdate,
    db: DbSession,
    actor: Actor,
):
    return await service.update_guardian(db, actor, patient_id, guardian_id, data)


@router.delete("/{patient_id}/guardians/{guardian_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_guardian(
    patient_id: uuid.UUID, guardian_id: uuid.UUID, db: DbSession, actor: Actor
):
    await service.remove_guardian(db, actor, patient_id, guardian_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- emergency contacts
@router.get("/{patient_id}/emergency-contacts", response_model=list[EmergencyContactRead])
async def list_emergency_contacts(patient_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.list_emergency_contacts(db, actor, patient_id)


@router.post(
    "/{patient_id}/emergency-contacts",
    response_model=EmergencyContactRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_emergency_contact(
    patient_id: uuid.UUID, data: EmergencyContactCreate, db: DbSession, actor: Actor
):
    return await service.add_emergency_contact(db, actor, patient_id, data)


@router.patch("/{patient_id}/emergency-contacts/{contact_id}", response_model=EmergencyContactRead)
async def update_emergency_contact(
    patient_id: uuid.UUID,
    contact_id: uuid.UUID,
    data: EmergencyContactUpdate,
    db: DbSession,
    actor: Actor,
):
    return await service.update_emergency_contact(db, actor, patient_id, contact_id, data)


@router.delete(
    "/{patient_id}/emergency-contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_emergency_contact(
    patient_id: uuid.UUID, contact_id: uuid.UUID, db: DbSession, actor: Actor
):
    await service.remove_emergency_contact(db, actor, patient_id, contact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- care profile
@router.get("/{patient_id}/care-profile", response_model=CareProfileRead)
async def get_care_profile(patient_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.get_care_profile(db, actor, patient_id)


@router.put("/{patient_id}/care-profile", response_model=CareProfileRead)
async def put_care_profile(
    patient_id: uuid.UUID, data: CareProfileWrite, db: DbSession, actor: Actor
):
    """Create or fully replace the care profile."""
    return await service.put_care_profile(db, actor, patient_id, data)


# ---------------------------------------------------------------- family links
@router.get("/{patient_id}/family-links", response_model=list[FamilyLinkRead])
async def list_family_links(patient_id: uuid.UUID, db: DbSession, actor: Actor):
    return await service.list_family_links(db, actor, patient_id)


@router.post(
    "/{patient_id}/family-links",
    response_model=FamilyLinkRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_family_link(
    patient_id: uuid.UUID, data: FamilyLinkCreate, db: DbSession, actor: Actor
):
    return await service.add_family_link(db, actor, patient_id, data)


@router.patch("/{patient_id}/family-links/{link_id}", response_model=FamilyLinkRead)
async def update_family_link(
    patient_id: uuid.UUID,
    link_id: uuid.UUID,
    data: FamilyLinkUpdate,
    db: DbSession,
    actor: Actor,
):
    return await service.update_family_link(db, actor, patient_id, link_id, data)


@router.delete("/{patient_id}/family-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_family_link(
    patient_id: uuid.UUID, link_id: uuid.UUID, db: DbSession, actor: Actor
):
    await service.remove_family_link(db, actor, patient_id, link_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
