"""caregivers SQLAlchemy models (ERD section 2.3, Day 6).

Layered vetting (TS-1): every verification step is its own row, two guarantors
are required, and only a human ops user moves a caregiver to `approved`.

Tables: caregivers, guarantors, certifications, verification_checks,
availability_windows.

Notes for readers:
- "Two guarantors" cannot be enforced by a column constraint. The service
  layer enforces it when a caregiver is created (ON-4, TS-1).
- Relationships use lazy="raise": async sessions cannot lazy-load, so services
  must load what they need explicitly with selectinload(...).
- `Caregiver.nin` (field-encrypted, see ERD) is intentionally not defined yet.
  It needs the encrypted column type from core/encryption.py, which is still
  a stub. Add it in a follow-up once that type exists.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import sqlalchemy.orm as orm
from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin, UUIDPrimaryKeyMixin


# ---------------------------------------------------------------- enums
class CaregiverTier(enum.StrEnum):
    COMPANION = "companion"
    TRAINED_CARER = "trained_carer"
    LICENSED_NURSE = "licensed_nurse"


class CaregiverStatus(enum.StrEnum):
    """Only `approved` is bookable. `approved` is set by a human ops user."""

    APPLIED = "applied"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    APPROVED = "approved"
    SUSPENDED = "suspended"
    REJECTED = "rejected"


class CertType(enum.StrEnum):
    ACADEMY_MODULE = "academy_module"  # CA-1: module completion counts here
    NURSING_LICENCE = "nursing_licence"
    OTHER = "other"


class ReviewStatus(enum.StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class CheckType(enum.StrEnum):
    NIN = "nin"
    BACKGROUND = "background"
    GUARANTOR = "guarantor"
    INTERVIEW = "interview"


class CheckStatus(enum.StrEnum):
    REQUESTED = "requested"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


def _pg_enum(enum_cls: type[enum.StrEnum], name: str) -> Enum:
    """Native Postgres enum that stores the lowercase values, not member names."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda e: [m.value for m in e],
    )


# ---------------------------------------------------------------- models
class Caregiver(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "caregivers"
    __table_args__ = (
        CheckConstraint("hourly_rate_minor >= 0", name="hourly_rate_non_negative"),
        CheckConstraint("years_experience >= 0", name="years_experience_non_negative"),
        CheckConstraint("service_radius_km > 0", name="service_radius_positive"),
        # Ops queue: caregivers still waiting for a decision.
        Index(
            "ix_caregivers_status_queue",
            "status",
            postgresql_where=text("status IN ('applied', 'verifying', 'verified')"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    tier: Mapped[CaregiverTier] = mapped_column(
        _pg_enum(CaregiverTier, "caregiver_tier"), nullable=False
    )
    status: Mapped[CaregiverStatus] = mapped_column(
        _pg_enum(CaregiverStatus, "caregiver_status"),
        nullable=False,
        default=CaregiverStatus.APPLIED,
        server_default=CaregiverStatus.APPLIED.value,
    )
    bio: Mapped[str | None] = mapped_column(Text)
    languages: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    years_experience: Mapped[int | None] = mapped_column(SmallInteger)
    hourly_rate_minor: Mapped[int | None] = mapped_column(BigInteger)  # kobo
    base_location: Mapped[Any | None] = mapped_column(Geography("POINT", srid=4326))
    service_radius_km: Mapped[int | None] = mapped_column(SmallInteger)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )  # ops user: human approval
    approved_at: Mapped[datetime | None] = mapped_column()
    avg_rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))  # from reviews
    rating_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    guarantors: Mapped[list[Guarantor]] = orm.relationship(
        back_populates="caregiver", cascade="all, delete-orphan", lazy="raise"
    )
    certifications: Mapped[list[Certification]] = orm.relationship(
        back_populates="caregiver", cascade="all, delete-orphan", lazy="raise"
    )
    verification_checks: Mapped[list[VerificationCheck]] = orm.relationship(
        back_populates="caregiver", lazy="raise"
    )
    availability_windows: Mapped[list[AvailabilityWindow]] = orm.relationship(
        back_populates="caregiver", cascade="all, delete-orphan", lazy="raise"
    )


class Guarantor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A person vouching for the caregiver. Two are required (TS-1)."""

    __tablename__ = "guarantors"

    caregiver_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    relationship: Mapped[str] = mapped_column(String(60), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column()

    caregiver: Mapped[Caregiver] = orm.relationship(
        back_populates="guarantors", lazy="raise"
    )


class Certification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A document or completed academy module (CA-1 records modules here)."""

    __tablename__ = "certifications"

    caregiver_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cert_type: Mapped[CertType] = mapped_column(
        _pg_enum(CertType, "cert_type"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(200))
    licence_number: Mapped[str | None] = mapped_column(String(50))  # nurses only
    document_key: Mapped[str | None] = mapped_column(String(512))  # S3 object key
    status: Mapped[ReviewStatus] = mapped_column(
        _pg_enum(ReviewStatus, "review_status"),
        nullable=False,
        default=ReviewStatus.PENDING,
        server_default=ReviewStatus.PENDING.value,
    )
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    verified_at: Mapped[datetime | None] = mapped_column()
    expires_at: Mapped[date | None] = mapped_column(Date)

    caregiver: Mapped[Caregiver] = orm.relationship(
        back_populates="certifications", lazy="raise"
    )


class VerificationCheck(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One vetting step (NIN, background, ...) and its outcome (TS-1)."""

    __tablename__ = "verification_checks"

    caregiver_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("caregivers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    check_type: Mapped[CheckType] = mapped_column(
        _pg_enum(CheckType, "check_type"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[CheckStatus] = mapped_column(
        _pg_enum(CheckStatus, "check_status"),
        nullable=False,
        default=CheckStatus.REQUESTED,
        server_default=CheckStatus.REQUESTED.value,
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # minimal response
    requested_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )  # ops user
    completed_at: Mapped[datetime | None] = mapped_column()

    caregiver: Mapped[Caregiver] = orm.relationship(
        back_populates="verification_checks", lazy="raise"
    )


class AvailabilityWindow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A recurring weekly slot, optionally limited to a date range."""

    __tablename__ = "availability_windows"
    __table_args__ = (
        CheckConstraint("start_time < end_time", name="start_before_end"),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="day_of_week_range"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_range_ordered",
        ),
    )

    caregiver_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0 = Monday
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)

    caregiver: Mapped[Caregiver] = orm.relationship(
        back_populates="availability_windows", lazy="raise"
    )