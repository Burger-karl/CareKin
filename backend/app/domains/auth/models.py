"""auth SQLAlchemy models: User, RefreshToken, ConsentRecord (ERD section 2.1).

OTP codes are not stored here: they live in Redis with a TTL (see service.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.core.permissions import UserRole
from app.database import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ConsentType(StrEnum):
    TERMS = "terms"  # terms of service and privacy policy
    HEALTH_DATA = "health_data"  # storing health data at all (ON-2, NDPA)
    LOCATION = "location"  # crisis button and GPS check-in
    PASSPORT_SHARE = "passport_share"  # one record per share to a facility (HP-3)
    LLM_PROCESSING = "llm_processing"  # AI companion and matching agent


class PreferredLanguage(StrEnum):
    ENGLISH = "en"
    IGBO = "ig"
    PIDGIN = "pcm"


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Store the enum's lowercase values (not member names) in a named Postgres enum."""
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # E.164, e.g. +2348031234567. The login identifier.
    phone: Mapped[str] = mapped_column(String(16), unique=True)
    email: Mapped[str | None] = mapped_column(String(254))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[UserRole] = mapped_column(_pg_enum(UserRole, "user_role"), index=True)
    preferred_language: Mapped[PreferredLanguage] = mapped_column(
        _pg_enum(PreferredLanguage, "preferred_language"),
        default=PreferredLanguage.ENGLISH,
        server_default=PreferredLanguage.ENGLISH.value,
    )
    phone_verified_at: Mapped[datetime | None]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    last_login_at: Mapped[datetime | None]

    @property
    def is_phone_verified(self) -> bool:
        return self.phone_verified_at is not None


class RefreshToken(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One row per issued refresh token. Rotation revokes the old row (refresh_tokens)."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # never the raw token
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    user_agent: Mapped[str | None] = mapped_column(String(512))

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class ConsentRecord(UUIDPrimaryKeyMixin, Base):
    """Append-only history of consent decisions (ON-2).

    A user's current position on a consent type is their latest row for it.
    Granting writes a row with granted=true; withdrawing writes granted=false and
    stamps revoked_at on the earlier grant. Rows are never deleted.
    """

    __tablename__ = "consent_records"
    __table_args__ = (
        Index("ix_consent_records_user_type", "user_id", "consent_type", "granted_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    # Set when a guardian consents on behalf of a patient (minor). The foreign key to
    # patients.id is added by the Day 5 migration that creates the patients table.
    patient_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    consent_type: Mapped[ConsentType] = mapped_column(_pg_enum(ConsentType, "consent_type"))
    policy_version: Mapped[str] = mapped_column(String(32))
    granted: Mapped[bool] = mapped_column(Boolean)
    granted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]
    ip_address: Mapped[str | None] = mapped_column(INET)
