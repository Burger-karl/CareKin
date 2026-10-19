"""passport business logic: the patient-controlled health record (HP-1 to HP-4).

Who may do what:
- patient / guardian (self, guardian):  add entries, read, export, share, revoke.
- family member (FamilyLink):           read and export, only if the patient shared
                                        health data with them (share_scope.health_data).
- facility admin:                       read and export, only through an active share to a
                                        facility they manage, and only the entry types
                                        (and profile) that share covers. A share that was
                                        revoked or has expired answers 403.
- caregivers and ops:                   no access (404). Caregiver access is open decision
                                        Topic 4; ops can't read health records.
- anyone else:                          404, so the patient's existence isn't leaked.

Every read and export writes a `passport.view` / `passport.export` audit row (HP-4),
committed with the response. Entries and profile fields are encrypted at rest.

Sharing (HP-3): each share creates its own consent record (consent_type=passport_share)
granted by the person sharing, and the share points at it. Revoking stamps revoked_at on
both. Shares are time-limited (1 hour to 30 days) and only to verified facilities.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.audit import client_ip, write_audit
from app.core.exceptions import (
    CareKinError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.permissions import UserRole
from app.domains.auth.models import ConsentRecord, ConsentType
from app.domains.auth.service import has_consent
from app.domains.facilities.models import Facility, VerificationStatus
from app.domains.passport import export as pdf
from app.domains.passport.models import PassportEntry, PassportEntryType, PassportShare
from app.domains.passport.schemas import (
    PassportAccessRead,
    PassportEntryCreate,
    PassportEntryRead,
    PassportPatient,
    PassportProfile,
    PassportRead,
    PassportShareCreate,
    PassportShareRead,
    ReceivedShareRead,
)
from app.domains.patients.models import CareProfile, Patient
from app.domains.patients.service import PatientNotFound, get_access

ALL_TYPES: tuple[PassportEntryType, ...] = tuple(PassportEntryType)
# A device clock may run this far ahead of ours.
CLOCK_SKEW = timedelta(minutes=5)
DISCLAIMER = pdf.DISCLAIMER


# ---------------------------------------------------------------- errors
class PassportNotFound(NotFoundError):
    code = "passport_not_found"


class PassportAccessDenied(ForbiddenError):
    code = "passport_forbidden"


class PassportConflict(ConflictError):
    code = "passport_conflict"


class PassportRuleViolation(CareKinError):
    status_code = 422
    code = "passport_rule_violation"


# ---------------------------------------------------------------- actor and access
@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    role: UserRole
    request: Request | None = None


Via = Literal["self", "guardian", "family", "facility"]


@dataclass(frozen=True)
class PassportAccess:
    patient: Patient
    via: Via
    entry_types: tuple[PassportEntryType, ...]
    include_profile: bool
    share: PassportShare | None = None

    @property
    def can_manage(self) -> bool:
        return self.via in ("self", "guardian")


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(
    db: AsyncSession,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    metadata: dict[str, object] | None = None,
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


async def _facility_access(db: AsyncSession, actor: Actor, patient_id: uuid.UUID) -> PassportAccess:
    """A facility admin reads through the newest share to a facility they manage."""
    managed = select(Facility.id).where(Facility.claimed_by == actor.user_id)
    shares = list(
        await db.scalars(
            select(PassportShare)
            .where(PassportShare.patient_id == patient_id, PassportShare.facility_id.in_(managed))
            .order_by(PassportShare.created_at.desc())
        )
    )
    if not shares:
        raise PassportNotFound("Passport not found")
    now = _now()
    active = next((s for s in shares if s.is_active(now)), None)
    if active is None:
        latest = shares[0]
        if latest.revoked_at is not None:
            raise PassportAccessDenied(
                "The patient revoked this share",
                code="share_revoked",
                details={"share_id": str(latest.id)},
            )
        raise PassportAccessDenied(
            "This share has expired", code="share_expired", details={"share_id": str(latest.id)}
        )
    patient = await db.get(Patient, patient_id)
    if patient is None:
        raise PassportNotFound("Passport not found")
    return PassportAccess(
        patient=patient,
        via="facility",
        entry_types=tuple(PassportEntryType(t) for t in active.entry_types),
        include_profile=active.include_profile,
        share=active,
    )


async def get_passport_access(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID
) -> PassportAccess:
    if actor.role is UserRole.FACILITY_ADMIN:
        return await _facility_access(db, actor, patient_id)
    if actor.role in (UserRole.CAREGIVER, UserRole.OPS):
        raise PassportNotFound("Passport not found")
    try:
        access = await get_access(db, patient_id, actor.user_id)
    except PatientNotFound:
        raise PassportNotFound("Passport not found") from None
    if access.role == "family":
        if not access.sees_health_data:
            raise PassportAccessDenied(
                "The patient hasn't shared their health record with you",
                code="health_data_not_shared",
            )
        return PassportAccess(access.patient, "family", ALL_TYPES, True)
    via: Via = "self" if access.role == "self" else "guardian"
    return PassportAccess(access.patient, via, ALL_TYPES, True)


def _require_manage(access: PassportAccess) -> None:
    if not access.can_manage:
        raise PassportAccessDenied("Only the patient or a guardian can do this")


# ---------------------------------------------------------------- reads
async def _facility_names(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await db.execute(select(Facility.id, Facility.name).where(Facility.id.in_(ids)))
    return dict(rows.all())


def _entry_read(entry: PassportEntry, names: dict[uuid.UUID, str]) -> PassportEntryRead:
    read = PassportEntryRead.model_validate(entry)
    read.facility_name = names.get(entry.facility_id) if entry.facility_id else None
    return read


def _access_read(access: PassportAccess) -> PassportAccessRead:
    share = access.share
    return PassportAccessRead(
        via=access.via,
        can_manage=access.can_manage,
        share_id=share.id if share else None,
        facility_id=share.facility_id if share else None,
        expires_at=share.expires_at if share else None,
        entry_types=list(access.entry_types),
    )


def _visible(
    access: PassportAccess, types: list[PassportEntryType] | None
) -> list[PassportEntryType]:
    allowed = list(access.entry_types)
    if types:
        allowed = [t for t in allowed if t in types]
    return allowed


async def _profile(db: AsyncSession, access: PassportAccess) -> CareProfile | None:
    if not access.include_profile:
        return None
    return await db.scalar(select(CareProfile).where(CareProfile.patient_id == access.patient.id))


async def get_passport(
    db: AsyncSession,
    actor: Actor,
    patient_id: uuid.UUID,
    *,
    entry_types: list[PassportEntryType] | None = None,
    since: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> PassportRead:
    """HP-1, HP-4: the record, newest first. Every view is audited."""
    access = await get_passport_access(db, actor, patient_id)
    types = _visible(access, entry_types)
    where = [PassportEntry.patient_id == patient_id, PassportEntry.entry_type.in_(types)]
    if since is not None:
        where.append(PassportEntry.occurred_at >= since)
    total = (
        await db.scalar(select(func.count()).select_from(PassportEntry).where(*where))
        if types
        else 0
    )
    entries = (
        list(
            await db.scalars(
                select(PassportEntry)
                .where(*where)
                .order_by(PassportEntry.occurred_at.desc(), PassportEntry.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        if types
        else []
    )
    names = await _facility_names(db, {e.facility_id for e in entries if e.facility_id})
    profile = await _profile(db, access)
    patient = access.patient
    result = PassportRead(
        patient=PassportPatient(
            id=patient.id,
            full_name=patient.full_name,
            date_of_birth=patient.date_of_birth,
            sex=patient.sex,
            genotype=patient.genotype if access.include_profile else None,
            blood_group=patient.blood_group if access.include_profile else None,
        ),
        profile=PassportProfile(
            known_triggers=profile.known_triggers,
            medications_overview=profile.medications_overview,
            allergies=profile.allergies,
            care_instructions=profile.care_instructions,
        )
        if profile
        else None,
        entries=[_entry_read(e, names) for e in entries],
        total_entries=total or 0,
        access=_access_read(access),
        disclaimer=DISCLAIMER,
    )
    _audit(
        db,
        actor,
        "passport.view",
        "patient",
        patient_id,
        {
            "via": access.via,
            "share_id": str(access.share.id) if access.share else None,
            "entries": len(entries),
        },
    )
    await db.commit()
    return result


async def export_passport(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID
) -> tuple[bytes, pdf.ExportResult]:
    """HP-2: one-page PDF for a doctor. Same access and scope as reading. Audited."""
    access = await get_passport_access(db, actor, patient_id)
    types = list(access.entry_types)
    entries = list(
        await db.scalars(
            select(PassportEntry)
            .where(PassportEntry.patient_id == patient_id, PassportEntry.entry_type.in_(types))
            .order_by(PassportEntry.occurred_at.desc(), PassportEntry.created_at.desc())
            # More than one page can ever hold; the renderer fits what it can.
            .limit(200)
        )
    )
    total = await db.scalar(
        select(func.count())
        .select_from(PassportEntry)
        .where(PassportEntry.patient_id == patient_id, PassportEntry.entry_type.in_(types))
    )
    names = await _facility_names(db, {e.facility_id for e in entries if e.facility_id})
    profile = await _profile(db, access)
    patient = access.patient
    if access.via == "facility" and access.share is not None:
        facility = await db.get(Facility, access.share.facility_id)
        exported_for = f"Shared with {facility.name if facility else 'a facility'}"
    else:
        exported_for = {
            "self": "Exported by the patient",
            "guardian": "Exported by a guardian",
            "family": "Exported by family (shared by the patient)",
        }[access.via]
    result = pdf.render(
        pdf.ExportInput(
            full_name=patient.full_name,
            date_of_birth=patient.date_of_birth,
            sex=patient.sex,
            genotype=patient.genotype if access.include_profile else None,
            blood_group=patient.blood_group if access.include_profile else None,
            entries=[
                pdf.ExportEntry(
                    occurred_at=e.occurred_at,
                    entry_type=e.entry_type.value,
                    summary=e.summary,
                    facility_name=names.get(e.facility_id) if e.facility_id else None,
                )
                for e in entries
            ],
            exported_at=_now(),
            exported_for=exported_for,
            known_triggers=profile.known_triggers if profile else None,
            medications_overview=profile.medications_overview if profile else None,
            allergies=profile.allergies if profile else None,
            care_instructions=profile.care_instructions if profile else None,
            show_profile=access.include_profile,
        )
    )
    _audit(
        db,
        actor,
        "passport.export",
        "patient",
        patient_id,
        {
            "via": access.via,
            "share_id": str(access.share.id) if access.share else None,
            "entries_shown": result.entries_shown,
            "entries_total": total or 0,
        },
    )
    await db.commit()
    return result.pdf, result


# ---------------------------------------------------------------- entries
async def add_entry(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID, data: PassportEntryCreate
) -> PassportEntryRead:
    """HP-1: the patient or a guardian records a crisis, trigger, visit, transfusion or
    medication. Needs the writer's health-data consent (ON-2)."""
    access = await get_passport_access(db, actor, patient_id)
    _require_manage(access)
    if not await has_consent(db, actor.user_id, ConsentType.HEALTH_DATA):
        raise PassportAccessDenied(
            "Consent is required before this information can be stored.",
            code="consent_required",
            details={"consent_type": ConsentType.HEALTH_DATA.value, "next": "/auth/consent"},
        )
    if data.occurred_at > _now() + CLOCK_SKEW:
        raise PassportRuleViolation("occurred_at is in the future", code="occurred_in_future")
    if data.occurred_at.date() < access.patient.date_of_birth - timedelta(days=1):
        raise PassportRuleViolation(
            "occurred_at is before the patient was born", code="occurred_before_birth"
        )
    names: dict[uuid.UUID, str] = {}
    if data.facility_id is not None:
        facility = await db.get(Facility, data.facility_id)
        if facility is None:
            raise NotFoundError("Facility not found", code="facility_not_found")
        names[facility.id] = facility.name
    entry = PassportEntry(
        patient_id=patient_id,
        entry_type=data.entry_type,
        occurred_at=data.occurred_at,
        summary=data.summary,
        details=data.details,
        facility_id=data.facility_id,
        created_by=actor.user_id,
    )
    db.add(entry)
    _audit(
        db,
        actor,
        "passport.entry.create",
        "passport_entry",
        entry.id,
        {"patient_id": str(patient_id), "entry_type": data.entry_type.value, "source": "manual"},
    )
    await db.commit()
    await db.refresh(entry)
    return _entry_read(entry, names)


# ---------------------------------------------------------------- shares
def _status(share: PassportShare, now: datetime) -> Literal["active", "expired", "revoked"]:
    if share.revoked_at is not None:
        return "revoked"
    return "active" if share.expires_at > now else "expired"


def _share_read(
    share: PassportShare, facility_name: str | None, now: datetime
) -> PassportShareRead:
    return PassportShareRead(
        id=share.id,
        patient_id=share.patient_id,
        facility_id=share.facility_id,
        facility_name=facility_name,
        consent_record_id=share.consent_record_id,
        granted_by=share.granted_by,
        entry_types=[PassportEntryType(t) for t in share.entry_types],
        include_profile=share.include_profile,
        expires_at=share.expires_at,
        revoked_at=share.revoked_at,
        created_at=share.created_at,
        status=_status(share, now),
    )


async def create_share(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID, data: PassportShareCreate
) -> PassportShareRead:
    """HP-3: a consented, time-limited share with one verified facility."""
    access = await get_passport_access(db, actor, patient_id)
    _require_manage(access)
    facility = await db.get(Facility, data.facility_id, with_for_update=True)
    if facility is None:
        raise NotFoundError("Facility not found", code="facility_not_found")
    if facility.verification_status != VerificationStatus.VERIFIED:
        raise PassportRuleViolation(
            "Passports can only be shared with a facility CareKin has verified",
            code="facility_not_verified",
        )
    now = _now()
    existing = await db.scalar(
        select(PassportShare)
        .where(
            PassportShare.patient_id == patient_id,
            PassportShare.facility_id == facility.id,
            PassportShare.revoked_at.is_(None),
            PassportShare.expires_at > now,
        )
        .limit(1)
    )
    if existing is not None:
        raise PassportConflict(
            "This passport is already shared with that facility. Revoke the share to change it.",
            code="share_exists",
            details={"share_id": str(existing.id)},
        )
    consent = ConsentRecord(
        user_id=actor.user_id,
        patient_id=patient_id,
        consent_type=ConsentType.PASSPORT_SHARE,
        policy_version=get_settings().CONSENT_POLICY_VERSION,
        granted=True,
        granted_at=now,
        ip_address=client_ip(actor.request),
    )
    db.add(consent)
    types = [t.value for t in (data.entry_types or ALL_TYPES)]
    share = PassportShare(
        patient_id=patient_id,
        facility_id=facility.id,
        consent_record_id=consent.id,
        granted_by=actor.user_id,
        entry_types=types,
        include_profile=data.include_profile,
        expires_at=now + timedelta(hours=data.expires_in_hours),
        created_at=now,
    )
    db.add(share)
    _audit(
        db, actor, "consent.grant", "consent_record", consent.id, {"consent_type": "passport_share"}
    )
    _audit(
        db,
        actor,
        "passport.share",
        "passport_share",
        share.id,
        {
            "patient_id": str(patient_id),
            "facility_id": str(facility.id),
            "entry_types": types,
            "include_profile": data.include_profile,
            "expires_in_hours": data.expires_in_hours,
        },
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise PassportConflict("The share conflicts with existing data") from exc
    await db.refresh(share)
    return _share_read(share, facility.name, now)


async def list_shares(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID
) -> list[PassportShareRead]:
    access = await get_passport_access(db, actor, patient_id)
    _require_manage(access)
    shares = list(
        await db.scalars(
            select(PassportShare)
            .where(PassportShare.patient_id == patient_id)
            .order_by(PassportShare.created_at.desc())
        )
    )
    names = await _facility_names(db, {s.facility_id for s in shares})
    now = _now()
    return [_share_read(s, names.get(s.facility_id), now) for s in shares]


async def revoke_share(
    db: AsyncSession, actor: Actor, patient_id: uuid.UUID, share_id: uuid.UUID
) -> PassportShareRead:
    """HP-3: stop sharing now. Revoking twice is fine; the first revocation stands."""
    access = await get_passport_access(db, actor, patient_id)
    _require_manage(access)
    share = await db.get(PassportShare, share_id, with_for_update=True)
    if share is None or share.patient_id != patient_id:
        raise NotFoundError("Share not found", code="share_not_found")
    now = _now()
    if share.revoked_at is None:
        share.revoked_at = now
        share.revoked_by = actor.user_id
        consent = await db.get(ConsentRecord, share.consent_record_id, with_for_update=True)
        if consent is not None and consent.revoked_at is None:
            consent.revoked_at = now
        _audit(
            db,
            actor,
            "passport.unshare",
            "passport_share",
            share.id,
            {"patient_id": str(patient_id), "facility_id": str(share.facility_id)},
        )
        await db.commit()
        await db.refresh(share)
    facility = await db.get(Facility, share.facility_id)
    return _share_read(share, facility.name if facility else None, now)


async def received_shares(db: AsyncSession, actor: Actor) -> list[ReceivedShareRead]:
    """For the facility portal: active shares to the facilities this admin manages."""
    if actor.role is not UserRole.FACILITY_ADMIN:
        raise PassportAccessDenied("Only facility accounts receive shared passports")
    now = _now()
    rows = await db.execute(
        select(PassportShare, Patient.full_name, Facility.name)
        .join(Patient, Patient.id == PassportShare.patient_id)
        .join(Facility, Facility.id == PassportShare.facility_id)
        .where(
            Facility.claimed_by == actor.user_id,
            PassportShare.revoked_at.is_(None),
            PassportShare.expires_at > now,
        )
        .order_by(PassportShare.created_at.desc())
    )
    return [
        ReceivedShareRead(
            share_id=share.id,
            patient_id=share.patient_id,
            patient_name=patient_name,
            facility_id=share.facility_id,
            facility_name=facility_name,
            entry_types=[PassportEntryType(t) for t in share.entry_types],
            include_profile=share.include_profile,
            expires_at=share.expires_at,
            created_at=share.created_at,
        )
        for share, patient_name, facility_name in rows.all()
    ]


__all__ = [
    "Actor",
    "PassportAccess",
    "add_entry",
    "create_share",
    "export_passport",
    "get_passport",
    "get_passport_access",
    "list_shares",
    "received_shares",
    "revoke_share",
]
