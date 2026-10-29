"""caregivers business logic (Day 6): onboarding, certifications, availability, verification.

Rules enforced here, not in the routers:
- A caregiver profile belongs to one user (role: caregiver) and starts as `applied`.
- Only the owner or an ops user can change a profile, add certifications or set availability.
- Other users see a caregiver only when `approved`, and only the public-safe view.
- Verification is started by ops. Running checks moves a caregiver to `verifying`;
  only a human ops decision moves it to `approved` (TS-1), which is not part of Day 6.
- Every sensitive action writes an audit entry in the same transaction.
- The NIN goes to the provider and is never stored, logged or audited.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, UploadFile
from geoalchemy2 import Geometry, WKTElement
from sqlalchemy import cast, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.core.permissions import UserRole
from app.domains.auth.models import User
from app.domains.caregivers import schemas as s
from app.domains.caregivers.models import (
    AvailabilityWindow,
    Caregiver,
    CaregiverStatus,
    Certification,
    CertType,
    CheckStatus,
    CheckType,
    Guarantor,
    ReviewStatus,
    VerificationCheck,
)
from app.integrations.background_check import BackgroundCheckClient
from app.integrations.document_store import DocumentStore
from app.integrations.nin import NINClient

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
# File type is decided from the first bytes, not from the name or the client's header.
_DOCUMENT_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"%PDF", "pdf", "application/pdf"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
)
_VERIFIABLE_STATUSES = (CaregiverStatus.APPLIED, CaregiverStatus.VERIFYING)


# ----------------------------------------------------------------- helpers
def _point(latitude: float, longitude: float) -> WKTElement:
    return WKTElement(f"POINT({longitude} {latitude})", srid=4326)


async def _load(db: AsyncSession, caregiver_id: uuid.UUID) -> Caregiver:
    """Fetch a caregiver with everything its views need, refreshed from the database."""
    stmt = (
        select(Caregiver)
        .where(Caregiver.id == caregiver_id)
        .options(
            selectinload(Caregiver.guarantors),
            selectinload(Caregiver.certifications),
            selectinload(Caregiver.verification_checks),
            selectinload(Caregiver.availability_windows),
        )
        .execution_options(populate_existing=True)
    )
    caregiver = (await db.execute(stmt)).scalar_one_or_none()
    if caregiver is None:
        raise NotFoundError("Caregiver not found.", code="caregiver_not_found")
    return caregiver


async def _get(db: AsyncSession, caregiver_id: uuid.UUID) -> Caregiver:
    caregiver = await db.get(Caregiver, caregiver_id)
    if caregiver is None:
        raise NotFoundError("Caregiver not found.", code="caregiver_not_found")
    return caregiver


def _can_manage(user: User, caregiver: Caregiver) -> bool:
    return user.role == UserRole.OPS or caregiver.user_id == user.id


def _require_manage(user: User, caregiver: Caregiver) -> None:
    if not _can_manage(user, caregiver):
        raise ForbiddenError(
            "You can only manage your own caregiver profile.", code="not_your_profile"
        )


async def _read_location(
    db: AsyncSession, caregiver_id: uuid.UUID
) -> tuple[float | None, float | None]:
    geometry = cast(Caregiver.base_location, Geometry)
    row = (
        await db.execute(
            select(func.ST_Y(geometry), func.ST_X(geometry)).where(Caregiver.id == caregiver_id)
        )
    ).one()
    return row[0], row[1]


async def _owner_view(db: AsyncSession, caregiver: Caregiver) -> s.CaregiverOut:
    latitude, longitude = await _read_location(db, caregiver.id)
    out = s.CaregiverOut.model_validate(caregiver)
    return out.model_copy(update={"base_latitude": latitude, "base_longitude": longitude})


async def _public_view(db: AsyncSession, caregiver: Caregiver) -> s.CaregiverPublicOut:
    owner = await db.get(User, caregiver.user_id)
    assert owner is not None
    windows = sorted(caregiver.availability_windows, key=lambda w: (w.day_of_week, w.start_time))
    return s.CaregiverPublicOut(
        id=caregiver.id,
        full_name=owner.full_name,
        tier=caregiver.tier,
        bio=caregiver.bio,
        languages=caregiver.languages,
        years_experience=caregiver.years_experience,
        hourly_rate_minor=caregiver.hourly_rate_minor,
        service_radius_km=caregiver.service_radius_km,
        avg_rating=caregiver.avg_rating,
        rating_count=caregiver.rating_count,
        certifications=[
            s.PublicCertificationOut.model_validate(c)
            for c in caregiver.certifications
            if c.status == ReviewStatus.VERIFIED
        ],
        availability_windows=[s.AvailabilityWindowOut.model_validate(w) for w in windows],
    )


# --------------------------------------------------------------- caregivers
async def create_caregiver(
    db: AsyncSession, user: User, data: s.CaregiverCreateRequest, request: Request | None
) -> s.CaregiverOut:
    if await db.scalar(select(Caregiver.id).where(Caregiver.user_id == user.id)):
        raise ConflictError("You already have a caregiver profile.", code="caregiver_exists")
    if any(g.phone == user.phone for g in data.guarantors):
        raise BadRequestError(
            "A guarantor cannot be you. Use two other people.", code="guarantor_is_self"
        )

    located = data.base_latitude is not None and data.base_longitude is not None
    caregiver = Caregiver(
        user_id=user.id,
        tier=data.tier,
        status=CaregiverStatus.APPLIED,
        bio=data.bio,
        languages=data.languages,
        years_experience=data.years_experience,
        hourly_rate_minor=data.hourly_rate_minor,
        service_radius_km=data.service_radius_km,
        base_location=_point(data.base_latitude, data.base_longitude) if located else None,
    )
    caregiver.guarantors = [Guarantor(**g.model_dump()) for g in data.guarantors]
    db.add(caregiver)
    try:
        await db.flush()
    except IntegrityError as exc:  # two requests raced past the check above
        await db.rollback()
        raise ConflictError(
            "You already have a caregiver profile.", code="caregiver_exists"
        ) from exc

    write_audit(
        db,
        action="caregiver.create",
        actor_id=user.id,
        entity_type="caregiver",
        entity_id=caregiver.id,
        request=request,
        metadata={"tier": data.tier.value},
    )
    await db.commit()
    return await _owner_view(db, await _load(db, caregiver.id))


async def get_caregiver(
    db: AsyncSession, user: User, caregiver_id: uuid.UUID
) -> s.CaregiverOut | s.CaregiverPublicOut:
    caregiver = await _load(db, caregiver_id)
    if _can_manage(user, caregiver):
        return await _owner_view(db, caregiver)
    if caregiver.status != CaregiverStatus.APPROVED:
        # Same answer as a missing caregiver, so unapproved profiles are not discoverable.
        raise NotFoundError("Caregiver not found.", code="caregiver_not_found")
    return await _public_view(db, caregiver)


async def get_my_caregiver(db: AsyncSession, user: User) -> s.CaregiverOut:
    caregiver_id = await db.scalar(select(Caregiver.id).where(Caregiver.user_id == user.id))
    if caregiver_id is None:
        raise NotFoundError("You haven't applied as a caregiver yet.", code="caregiver_not_found")
    return await _owner_view(db, await _load(db, caregiver_id))


async def update_caregiver(
    db: AsyncSession,
    user: User,
    caregiver_id: uuid.UUID,
    data: s.CaregiverUpdateRequest,
    request: Request | None,
) -> s.CaregiverOut:
    caregiver = await _get(db, caregiver_id)
    _require_manage(user, caregiver)

    changes = data.model_dump(exclude_unset=True)
    latitude = changes.pop("base_latitude", None)
    longitude = changes.pop("base_longitude", None)
    changed: list[str] = []
    for field, value in changes.items():
        if field == "languages" and value is None:
            continue  # languages cannot be empty-null; send [] to clear
        setattr(caregiver, field, value)
        changed.append(field)
    if "base_latitude" in data.model_fields_set:
        caregiver.base_location = _point(latitude, longitude) if latitude is not None else None
        changed.append("base_location")

    write_audit(
        db,
        action="caregiver.update",
        actor_id=user.id,
        entity_type="caregiver",
        entity_id=caregiver.id,
        request=request,
        metadata={"fields": sorted(changed)},  # field names only, never values
    )
    await db.commit()
    return await _owner_view(db, await _load(db, caregiver.id))


# ----------------------------------------------------------- certifications
async def _read_document(upload: UploadFile) -> tuple[bytes, str, str]:
    content = await upload.read(MAX_DOCUMENT_BYTES + 1)
    if not content:
        raise BadRequestError("The uploaded file is empty.", code="document_empty")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise BadRequestError("The file is larger than 5 MB.", code="document_too_large")
    for signature, extension, content_type in _DOCUMENT_SIGNATURES:
        if content.startswith(signature):
            return content, extension, content_type
    raise BadRequestError("Upload a PDF, JPG or PNG file.", code="document_type_unsupported")


async def add_certification(
    db: AsyncSession,
    user: User,
    caregiver_id: uuid.UUID,
    data: s.CertificationCreateRequest,
    upload: UploadFile | None,
    store: DocumentStore,
    request: Request | None,
) -> s.CertificationOut:
    caregiver = await _get(db, caregiver_id)
    _require_manage(user, caregiver)

    # Academy module completion has no file; everything else needs its document (CA-1).
    if upload is None and data.cert_type != CertType.ACADEMY_MODULE:
        raise BadRequestError(
            "Upload the document for this certification.", code="document_required"
        )

    document_key: str | None = None
    if upload is not None:
        content, extension, content_type = await _read_document(upload)
        document_key = f"caregivers/{caregiver.id}/certifications/{uuid.uuid4()}.{extension}"
        await store.put(document_key, content, content_type)

    certification = Certification(
        caregiver_id=caregiver.id,
        cert_type=data.cert_type,
        title=data.title,
        issuer=data.issuer,
        licence_number=data.licence_number,
        document_key=document_key,
        status=ReviewStatus.PENDING,  # never auto-verified: a human or a trusted system decides
        expires_at=data.expires_at,
    )
    db.add(certification)
    await db.flush()
    write_audit(
        db,
        action="certification.create",
        actor_id=user.id,
        entity_type="certification",
        entity_id=certification.id,
        request=request,
        metadata={"caregiver_id": str(caregiver.id), "cert_type": data.cert_type.value},
    )
    await db.commit()
    await db.refresh(certification)
    return s.CertificationOut.model_validate(certification)


# ------------------------------------------------------------- availability
async def _windows(db: AsyncSession, caregiver_id: uuid.UUID) -> s.AvailabilityOut:
    rows = await db.scalars(
        select(AvailabilityWindow)
        .where(AvailabilityWindow.caregiver_id == caregiver_id)
        .order_by(AvailabilityWindow.day_of_week, AvailabilityWindow.start_time)
        .execution_options(populate_existing=True)
    )
    return s.AvailabilityOut(
        windows=[s.AvailabilityWindowOut.model_validate(w) for w in rows.all()]
    )


async def get_availability(
    db: AsyncSession, user: User, caregiver_id: uuid.UUID
) -> s.AvailabilityOut:
    caregiver = await _get(db, caregiver_id)
    if not _can_manage(user, caregiver) and caregiver.status != CaregiverStatus.APPROVED:
        raise NotFoundError("Caregiver not found.", code="caregiver_not_found")
    return await _windows(db, caregiver.id)


async def set_availability(
    db: AsyncSession,
    user: User,
    caregiver_id: uuid.UUID,
    data: s.AvailabilitySetRequest,
    request: Request | None,
) -> s.AvailabilityOut:
    caregiver = await _get(db, caregiver_id)
    _require_manage(user, caregiver)

    await db.execute(
        delete(AvailabilityWindow).where(AvailabilityWindow.caregiver_id == caregiver.id)
    )
    db.add_all(
        AvailabilityWindow(caregiver_id=caregiver.id, **window.model_dump())
        for window in data.windows
    )
    write_audit(
        db,
        action="caregiver.availability_set",
        actor_id=user.id,
        entity_type="caregiver",
        entity_id=caregiver.id,
        request=request,
        metadata={"windows": len(data.windows)},
    )
    await db.commit()
    return await _windows(db, caregiver.id)


# ------------------------------------------------------------- verification
async def _run_check(
    check: VerificationCheck,
    subject: User,
    data: s.VerifyRequest,
    nin_client: NINClient,
    background_client: BackgroundCheckClient,
) -> None:
    """Call the provider and record the outcome on `check`. Provider outages become `error`."""
    try:
        if check.check_type == CheckType.NIN:
            assert data.nin is not None  # the request schema guarantees it
            outcome: Any = await nin_client.verify(
                data.nin.get_secret_value(), full_name=subject.full_name
            )
        else:
            outcome = await background_client.verify(
                full_name=subject.full_name, phone=subject.phone
            )
    except ServiceUnavailableError:
        check.status = CheckStatus.ERROR
        check.result = {"error": "provider_unavailable"}
    else:
        check.status = CheckStatus.PASSED if outcome.passed else CheckStatus.FAILED
        check.provider_ref = outcome.reference
        check.result = {"passed": outcome.passed, "detail": outcome.detail}
    check.completed_at = datetime.now(UTC)


async def verify_caregiver(
    db: AsyncSession,
    user: User,
    caregiver_id: uuid.UUID,
    data: s.VerifyRequest,
    nin_client: NINClient,
    background_client: BackgroundCheckClient,
    request: Request | None,
) -> s.VerifyOut:
    caregiver = await _get(db, caregiver_id)
    if caregiver.status not in _VERIFIABLE_STATUSES:
        raise ConflictError(
            "This caregiver can no longer be verified.",
            code="caregiver_not_verifiable",
            details={"status": caregiver.status.value},
        )
    subject = await db.get(User, caregiver.user_id)
    assert subject is not None

    checks: list[VerificationCheck] = []
    for check_type in data.checks:
        provider = nin_client if check_type == CheckType.NIN else background_client
        check = VerificationCheck(
            caregiver_id=caregiver.id,
            check_type=check_type,
            provider=provider.name,
            status=CheckStatus.REQUESTED,
            requested_by=user.id,
        )
        db.add(check)
        await _run_check(check, subject, data, nin_client, background_client)
        write_audit(
            db,
            action=f"verification.{check_type.value}",
            actor_id=user.id,
            entity_type="verification_check",
            entity_id=check.id,
            request=request,
            metadata={"caregiver_id": str(caregiver.id), "status": check.status.value},
        )
        checks.append(check)

    caregiver.status = CaregiverStatus.VERIFYING
    await db.commit()
    for check in checks:
        await db.refresh(check)
    return s.VerifyOut(
        caregiver_id=caregiver.id,
        status=caregiver.status,
        checks=[s.VerificationCheckOut.model_validate(c) for c in checks],
    )