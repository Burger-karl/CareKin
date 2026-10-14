"""patients business logic: Patient, Guardian, EmergencyContact, CareProfile, FamilyLink.

Access rules (who may do what with a patient record):
- self:      the patient's own account. Reads and edits everything; an adult also
             manages guardians and family links.
- guardian:  reads and edits the record. Only a primary guardian manages guardians
             and family links.
- family:    a family link. Sees the basic profile; health data and home location
             only if the patient's share_scope grants them.
- anyone else gets 404, so a patient's existence isn't leaked.

A patient with no login, or a minor, always keeps a primary guardian (ON-3).

Every write, and every read that returns health data, adds an audit_logs row
in the same transaction (HP-4). Metadata records which fields changed, never values.

Writing health data (genotype, blood group, care profile) needs the caller's
`health_data` consent (ON-2).
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import Request
from geoalchemy2.elements import WKBElement, WKTElement
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.exceptions import CareKinError, ConflictError, ForbiddenError, NotFoundError
from app.core.permissions import UserRole
from app.domains.auth.models import ConsentType
from app.domains.auth.service import has_consent
from app.domains.patients.models import (
    ADULT_AGE,
    CareProfile,
    EmergencyContact,
    FamilyLink,
    Guardian,
    Patient,
)
from app.domains.patients.schemas import (
    CareProfileWrite,
    EmergencyContactCreate,
    EmergencyContactUpdate,
    FamilyLinkCreate,
    FamilyLinkUpdate,
    GeoPoint,
    GuardianCreate,
    GuardianUpdate,
    PatientCreate,
    PatientRead,
    PatientUpdate,
)


# ---------------------------------------------------------------- errors
class PatientNotFound(NotFoundError):
    """Doesn't exist, or the caller has no access to it."""

    code = "patient_not_found"


class PatientAccessDenied(ForbiddenError):
    """The caller can see the patient but may not do this."""

    code = "patient_forbidden"


class PatientConflict(ConflictError):
    """Clashes with an existing row or references one that doesn't exist."""

    code = "patient_conflict"


class PatientRuleViolation(CareKinError):
    """Breaks a business rule, e.g. removing a minor's last primary guardian."""

    status_code = 422
    code = "patient_rule_violation"


# ---------------------------------------------------------------- actor
@dataclass(frozen=True)
class Actor:
    """The caller. `request` supplies IP and user agent for the audit log."""

    user_id: uuid.UUID
    role: UserRole
    request: Request | None = None


def _audit(
    db: AsyncSession,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    metadata: dict[str, Any] | None = None,
) -> None:
    write_audit(
        db,
        action=action,
        actor_id=actor.user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        request=actor.request,
        metadata=metadata,
    )


async def _require_health_consent(db: AsyncSession, actor: Actor) -> None:
    """ON-2: no health data is stored until the caller has consented."""
    if not await has_consent(db, actor.user_id, ConsentType.HEALTH_DATA):
        raise ForbiddenError(
            "Consent is required before this information can be stored.",
            code="consent_required",
            details={"consent_type": ConsentType.HEALTH_DATA.value, "next": "/auth/consent"},
        )


# ---------------------------------------------------------------- access
Role = Literal["self", "guardian", "family"]


@dataclass(frozen=True)
class PatientAccess:
    patient: Patient
    role: Role
    is_primary_guardian: bool = False
    share_scope: dict[str, bool] = field(default_factory=dict)

    @property
    def can_edit(self) -> bool:
        return self.role in ("self", "guardian")

    @property
    def can_manage_access(self) -> bool:
        """Add or remove guardians and family links."""
        if self.role == "self":
            return not self.patient.is_minor
        return self.is_primary_guardian

    @property
    def sees_health_data(self) -> bool:
        return self.can_edit or bool(self.share_scope.get("health_data"))

    @property
    def sees_location(self) -> bool:
        return self.can_edit or bool(self.share_scope.get("location"))


async def get_access(db: AsyncSession, patient_id: uuid.UUID, actor_id: uuid.UUID) -> PatientAccess:
    patient = await db.get(Patient, patient_id)
    if patient is None:
        raise PatientNotFound("Patient not found")
    if patient.user_id == actor_id:
        return PatientAccess(patient, "self")
    guardian = await db.scalar(
        select(Guardian).where(Guardian.patient_id == patient_id, Guardian.user_id == actor_id)
    )
    if guardian is not None:
        return PatientAccess(patient, "guardian", is_primary_guardian=guardian.is_primary)
    link = await db.scalar(
        select(FamilyLink).where(
            FamilyLink.patient_id == patient_id, FamilyLink.user_id == actor_id
        )
    )
    if link is not None:
        return PatientAccess(patient, "family", share_scope=dict(link.share_scope or {}))
    raise PatientNotFound("Patient not found")


def _require_edit(access: PatientAccess) -> None:
    if not access.can_edit:
        raise PatientAccessDenied("Only the patient or a guardian can do this")


def _require_manage(access: PatientAccess) -> None:
    if not access.can_manage_access:
        raise PatientAccessDenied(
            "Only an adult patient or a primary guardian can manage who has access"
        )


def _needs_guardian(patient: Patient) -> bool:
    return patient.user_id is None or patient.is_minor


# ---------------------------------------------------------------- helpers
def _to_wkt(point: GeoPoint | None) -> WKTElement | None:
    if point is None:
        return None
    return WKTElement(f"POINT({point.longitude} {point.latitude})", srid=4326)


def _from_wkb(value: Any) -> GeoPoint | None:
    """Read a PostGIS point (WKB or EWKB) without needing shapely."""
    if value is None:
        return None
    data = value.data if isinstance(value, WKBElement) else value
    data = bytes.fromhex(data) if isinstance(data, str) else bytes(data)
    order = "<" if data[0] == 1 else ">"
    (geom_type,) = struct.unpack(f"{order}I", data[1:5])
    offset = 9 if geom_type & 0x20000000 else 5  # EWKB carries a 4-byte SRID
    x, y = struct.unpack(f"{order}dd", data[offset : offset + 16])
    return GeoPoint(latitude=y, longitude=x)


async def _commit(db: AsyncSession) -> None:
    """Commit, turning constraint failures into PatientConflict."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if sqlstate == "23503":
            raise PatientConflict("A referenced user doesn't exist") from exc
        if sqlstate == "23505":
            raise PatientConflict("That record already exists") from exc
        raise PatientConflict("The change conflicts with existing data") from exc


def to_read(access: PatientAccess) -> PatientRead:
    patient = access.patient
    health = access.sees_health_data
    return PatientRead(
        id=patient.id,
        user_id=patient.user_id,
        full_name=patient.full_name,
        date_of_birth=patient.date_of_birth,
        age=patient.age,
        is_minor=patient.is_minor,
        sex=patient.sex,
        genotype=patient.genotype if health else None,
        blood_group=patient.blood_group if health else None,
        home_location=_from_wkb(patient.home_location) if access.sees_location else None,
        created_by=patient.created_by,
        created_at=patient.created_at,
        access=access.role,
    )


# ---------------------------------------------------------------- patients
async def create_patient(db: AsyncSession, actor: Actor, data: PatientCreate) -> PatientRead:
    patient = Patient(
        user_id=actor.user_id if data.for_self else None,
        full_name=data.full_name,
        date_of_birth=data.date_of_birth,
        sex=data.sex,
        genotype=data.genotype,
        blood_group=data.blood_group,
        home_location=_to_wkt(data.home_location),
        created_by=actor.user_id,
    )
    expected_role = UserRole.PATIENT if data.for_self else UserRole.GUARDIAN
    if actor.role is not expected_role:
        raise PatientAccessDenied(
            "Only patient accounts register themselves"
            if data.for_self
            else "Only guardian accounts register a patient for someone else"
        )
    if data.genotype is not None or data.blood_group is not None:
        await _require_health_consent(db, actor)
    if data.for_self:
        if patient.is_minor:
            raise PatientRuleViolation(
                f"Patients under {ADULT_AGE} are registered by a guardian (for_self=false)"
            )
        existing = await db.scalar(select(Patient.id).where(Patient.user_id == actor.user_id))
        if existing is not None:
            raise PatientConflict("This account already has a patient record")
    db.add(patient)
    await db.flush()
    if not data.for_self:
        db.add(
            Guardian(
                patient_id=patient.id,
                user_id=actor.user_id,
                relationship=data.guardian_relationship,
                is_primary=True,
                can_approve_caregivers=True,
                receives_crisis_alerts=True,
            )
        )
    _audit(db, actor, "patient.create", "patient", patient.id, {"for_self": data.for_self})
    await _commit(db)
    await db.refresh(patient)
    role: Role = "self" if data.for_self else "guardian"
    return to_read(PatientAccess(patient, role, is_primary_guardian=not data.for_self))


async def list_patients(db: AsyncSession, actor: Actor) -> list[PatientRead]:
    """Every patient the caller can see: their own record, then guardianships, then family links."""
    accesses: list[PatientAccess] = []
    own = await db.scalar(select(Patient).where(Patient.user_id == actor.user_id))
    if own is not None:
        accesses.append(PatientAccess(own, "self"))
    guarded = await db.execute(
        select(Patient, Guardian.is_primary)
        .join(Guardian, Guardian.patient_id == Patient.id)
        .where(Guardian.user_id == actor.user_id)
        .order_by(Patient.full_name)
    )
    for patient, is_primary in guarded:
        accesses.append(PatientAccess(patient, "guardian", is_primary_guardian=is_primary))
    linked = await db.execute(
        select(Patient, FamilyLink.share_scope)
        .join(FamilyLink, FamilyLink.patient_id == Patient.id)
        .where(FamilyLink.user_id == actor.user_id)
        .order_by(Patient.full_name)
    )
    for patient, scope in linked:
        accesses.append(PatientAccess(patient, "family", share_scope=dict(scope or {})))
    for access in accesses:
        if access.sees_health_data:
            _audit(db, actor, "patient.view", "patient", access.patient.id)
    await _commit(db)
    return [to_read(access) for access in accesses]


async def get_patient(db: AsyncSession, actor: Actor, patient_id: uuid.UUID) -> PatientRead:
    access = await get_access(db, patient_id, actor.user_id)
    if access.sees_health_data:
        _audit(db, actor, "patient.view", "patient", patient_id)
        await _commit(db)
    return to_read(access)


async def update_patient(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID, data: PatientUpdate
) -> PatientRead:
    access = await get_access(db, patient_id, actor.user_id)
    _require_edit(access)
    patient = access.patient
    changes = data.model_dump(exclude_unset=True)
    if {"genotype", "blood_group"} & changes.keys():
        await _require_health_consent(db, actor)
    if "home_location" in changes:
        changes["home_location"] = _to_wkt(data.home_location)
    for name, value in changes.items():
        setattr(patient, name, value)
    if (
        "date_of_birth" in changes
        and _needs_guardian(patient)
        and not await _has_primary_guardian(db, patient.id)
    ):
        await db.rollback()
        raise PatientRuleViolation("A minor must have a primary guardian before this change")
    _audit(db, actor, "patient.update", "patient", patient_id, {"fields": sorted(changes)})
    await _commit(db)
    await db.refresh(patient)
    return to_read(access)


# ---------------------------------------------------------------- guardians
async def _has_primary_guardian(
    db: AsyncSession, patient_id: uuid.UUID, excluding: uuid.UUID | None = None
) -> bool:
    query = select(Guardian.id).where(Guardian.patient_id == patient_id, Guardian.is_primary)
    if excluding is not None:
        query = query.where(Guardian.id != excluding)
    return await db.scalar(query.limit(1)) is not None


async def _demote_other_primaries(db: AsyncSession, patient_id: uuid.UUID, keep: uuid.UUID) -> None:
    """One primary guardian per patient: promoting one demotes the rest."""
    others = await db.scalars(
        select(Guardian).where(
            Guardian.patient_id == patient_id, Guardian.is_primary, Guardian.id != keep
        )
    )
    for other in others:
        other.is_primary = False


async def _get_guardian(
    db: AsyncSession, patient_id: uuid.UUID, guardian_id: uuid.UUID
) -> Guardian:
    guardian = await db.get(Guardian, guardian_id)
    if guardian is None or guardian.patient_id != patient_id:
        raise PatientNotFound("Guardian not found")
    return guardian


async def list_guardians(db: AsyncSession, actor: Actor, patient_id: uuid.UUID) -> list[Guardian]:
    _require_edit(await get_access(db, patient_id, actor.user_id))
    result = await db.scalars(
        select(Guardian)
        .where(Guardian.patient_id == patient_id)
        .order_by(Guardian.is_primary.desc(), Guardian.created_at)
    )
    return list(result)


async def add_guardian(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID, data: GuardianCreate
) -> Guardian:
    access = await get_access(db, patient_id, actor.user_id)
    _require_manage(access)
    if data.user_id == access.patient.user_id:
        raise PatientRuleViolation("A patient can't be their own guardian")
    guardian = Guardian(patient_id=patient_id, **data.model_dump())
    db.add(guardian)
    if guardian.is_primary:
        await _demote_other_primaries(db, patient_id, keep=guardian.id)
    _audit(db, actor, "guardian.add", "guardian", guardian.id, {"patient_id": str(patient_id)})
    await _commit(db)
    await db.refresh(guardian)
    return guardian


async def update_guardian(
    db: AsyncSession,
    actor: Actor,
    patient_id: uuid.UUID,
    guardian_id: uuid.UUID,
    data: GuardianUpdate,
) -> Guardian:
    access = await get_access(db, patient_id, actor.user_id)
    _require_manage(access)
    guardian = await _get_guardian(db, patient_id, guardian_id)
    changes = data.model_dump(exclude_none=True)
    if (
        changes.get("is_primary") is False
        and guardian.is_primary
        and _needs_guardian(access.patient)
        and not await _has_primary_guardian(db, patient_id, excluding=guardian.id)
    ):
        raise PatientRuleViolation(
            "This is the only primary guardian. Make someone else primary first"
        )
    for name, value in changes.items():
        setattr(guardian, name, value)
    if changes.get("is_primary"):
        await _demote_other_primaries(db, patient_id, keep=guardian.id)
    _audit(
        db,
        actor,
        "guardian.update",
        "guardian",
        guardian.id,
        {"patient_id": str(patient_id), "fields": sorted(changes)},
    )
    await _commit(db)
    await db.refresh(guardian)
    return guardian


async def remove_guardian(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID, guardian_id: uuid.UUID
) -> None:
    access = await get_access(db, patient_id, actor.user_id)
    _require_manage(access)
    guardian = await _get_guardian(db, patient_id, guardian_id)
    if (
        guardian.is_primary
        and _needs_guardian(access.patient)
        and not await _has_primary_guardian(db, patient_id, excluding=guardian.id)
    ):
        raise PatientRuleViolation(
            "This is the only primary guardian. Make someone else primary first"
        )
    await db.delete(guardian)
    _audit(db, actor, "guardian.remove", "guardian", guardian.id, {"patient_id": str(patient_id)})
    await _commit(db)


# ---------------------------------------------------------------- emergency contacts
async def _get_contact(
    db: AsyncSession, patient_id: uuid.UUID, contact_id: uuid.UUID
) -> EmergencyContact:
    contact = await db.get(EmergencyContact, contact_id)
    if contact is None or contact.patient_id != patient_id:
        raise PatientNotFound("Emergency contact not found")
    return contact


async def list_emergency_contacts(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID
) -> list[EmergencyContact]:
    _require_edit(await get_access(db, patient_id, actor.user_id))
    result = await db.scalars(
        select(EmergencyContact)
        .where(EmergencyContact.patient_id == patient_id)
        .order_by(EmergencyContact.priority, EmergencyContact.name)
    )
    return list(result)


async def add_emergency_contact(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID, data: EmergencyContactCreate
) -> EmergencyContact:
    _require_edit(await get_access(db, patient_id, actor.user_id))
    contact = EmergencyContact(patient_id=patient_id, **data.model_dump())
    db.add(contact)
    _audit(
        db,
        actor,
        "emergency_contact.add",
        "emergency_contact",
        contact.id,
        {"patient_id": str(patient_id)},
    )
    await _commit(db)
    return contact


async def update_emergency_contact(
    db: AsyncSession,
    actor: Actor,
    patient_id: uuid.UUID,
    contact_id: uuid.UUID,
    data: EmergencyContactUpdate,
) -> EmergencyContact:
    _require_edit(await get_access(db, patient_id, actor.user_id))
    contact = await _get_contact(db, patient_id, contact_id)
    changes = data.model_dump(exclude_none=True)
    for name, value in changes.items():
        setattr(contact, name, value)
    _audit(
        db,
        actor,
        "emergency_contact.update",
        "emergency_contact",
        contact.id,
        {"patient_id": str(patient_id), "fields": sorted(changes)},
    )
    await _commit(db)
    return contact


async def remove_emergency_contact(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID, contact_id: uuid.UUID
) -> None:
    _require_edit(await get_access(db, patient_id, actor.user_id))
    contact = await _get_contact(db, patient_id, contact_id)
    await db.delete(contact)
    _audit(
        db,
        actor,
        "emergency_contact.remove",
        "emergency_contact",
        contact.id,
        {"patient_id": str(patient_id)},
    )
    await _commit(db)


# ---------------------------------------------------------------- care profile
async def get_care_profile(db: AsyncSession, actor: Actor, patient_id: uuid.UUID) -> CareProfile:
    access = await get_access(db, patient_id, actor.user_id)
    if not access.sees_health_data:
        raise PatientAccessDenied("The patient hasn't shared their care profile with you")
    profile = await db.scalar(select(CareProfile).where(CareProfile.patient_id == patient_id))
    if profile is None:
        raise PatientNotFound("This patient has no care profile yet")
    _audit(
        db, actor, "care_profile.view", "care_profile", profile.id, {"patient_id": str(patient_id)}
    )
    await _commit(db)
    return profile


async def put_care_profile(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID, data: CareProfileWrite
) -> CareProfile:
    _require_edit(await get_access(db, patient_id, actor.user_id))
    await _require_health_consent(db, actor)
    profile = await db.scalar(select(CareProfile).where(CareProfile.patient_id == patient_id))
    if profile is None:
        profile = CareProfile(patient_id=patient_id)
        db.add(profile)
    for name, value in data.model_dump().items():
        setattr(profile, name, value)
    profile.updated_by = actor.user_id
    _audit(
        db,
        actor,
        "care_profile.update",
        "care_profile",
        profile.id,
        {"patient_id": str(patient_id), "fields": sorted(data.model_fields_set)},
    )
    await _commit(db)
    await db.refresh(profile)
    return profile


# ---------------------------------------------------------------- family links
async def _get_link(db: AsyncSession, patient_id: uuid.UUID, link_id: uuid.UUID) -> FamilyLink:
    link = await db.get(FamilyLink, link_id)
    if link is None or link.patient_id != patient_id:
        raise PatientNotFound("Family link not found")
    return link


async def list_family_links(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID
) -> list[FamilyLink]:
    _require_manage(await get_access(db, patient_id, actor.user_id))
    result = await db.scalars(
        select(FamilyLink)
        .where(FamilyLink.patient_id == patient_id)
        .order_by(FamilyLink.created_at)
    )
    return list(result)


async def add_family_link(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID, data: FamilyLinkCreate
) -> FamilyLink:
    access = await get_access(db, patient_id, actor.user_id)
    _require_manage(access)
    if data.user_id == access.patient.user_id:
        raise PatientRuleViolation("A patient can't be linked to themselves")
    link = FamilyLink(patient_id=patient_id, **data.model_dump())
    db.add(link)
    _audit(
        db,
        actor,
        "family_link.add",
        "family_link",
        link.id,
        {"patient_id": str(patient_id), "share_scope": link.share_scope},
    )
    await _commit(db)
    await db.refresh(link)
    return link


async def update_family_link(
    db: AsyncSession,
    actor: Actor,
    patient_id: uuid.UUID,
    link_id: uuid.UUID,
    data: FamilyLinkUpdate,
) -> FamilyLink:
    _require_manage(await get_access(db, patient_id, actor.user_id))
    link = await _get_link(db, patient_id, link_id)
    changes = data.model_dump(exclude_none=True)
    for name, value in changes.items():
        setattr(link, name, value)
    _audit(
        db,
        actor,
        "family_link.update",
        "family_link",
        link.id,
        {"patient_id": str(patient_id), **changes},
    )
    await _commit(db)
    await db.refresh(link)
    return link


async def remove_family_link(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID, link_id: uuid.UUID
) -> None:
    _require_manage(await get_access(db, patient_id, actor.user_id))
    link = await _get_link(db, patient_id, link_id)
    await db.delete(link)
    _audit(db, actor, "family_link.remove", "family_link", link.id, {"patient_id": str(patient_id)})
    await _commit(db)
