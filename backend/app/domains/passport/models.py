"""passport SQLAlchemy models: PassportEntry, PassportShare (ERD section 2.8).

PassportEntry came first, with the crisis button, which writes a crisis entry on
resolve (CR-4). PassportShare and the passport endpoints are the Day 9 build.

PassportShare (HP-3): a time-limited, patient-granted share of the passport with one
verified facility. It can't exist without the consent record that authorises it
(consent_records.consent_type = passport_share, one record per share). Revoking sets
revoked_at on both. Not in the ERD:
- include_profile: whether the facility also sees genotype, blood group, allergies,
  triggers and the medication list, not just the chosen entry types. A doctor usually
  needs them; the patient can say no.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.encryption import EncryptedText
from app.database import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

ENTRY_TYPE_VALUES = ("crisis", "trigger", "visit", "transfusion", "medication")


class PassportEntryType(StrEnum):
    CRISIS = "crisis"
    TRIGGER = "trigger"
    VISIT = "visit"
    TRANSFUSION = "transfusion"
    MEDICATION = "medication"


class PassportEntry(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "passport_entries"
    __table_args__ = (Index("ix_passport_entries_patient_occurred", "patient_id", "occurred_at"),)

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"))
    entry_type: Mapped[PassportEntryType] = mapped_column(
        Enum(
            PassportEntryType,
            name="passport_entry_type",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    occurred_at: Mapped[datetime]
    summary: Mapped[str] = mapped_column(EncryptedText)  # field-encrypted (HP-4)
    details: Mapped[str | None] = mapped_column(EncryptedText)
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("facilities.id", ondelete="SET NULL"), index=True
    )
    # Set when written from a resolved crisis (CR-4). At most one entry per crisis.
    crisis_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crisis_events.id", ondelete="RESTRICT"), unique=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )


class PassportShare(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "passport_shares"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        CheckConstraint("cardinality(entry_types) > 0", name="entry_types_not_empty"),
        # Text values, checked here, rather than an array of the Postgres enum: asyncpg
        # doesn't decode arrays of custom enum types without extra codecs.
        CheckConstraint(
            "entry_types <@ ARRAY['crisis','trigger','visit','transfusion','medication']::text[]",
            name="entry_types_known",
        ),
        Index("ix_passport_shares_patient_facility", "patient_id", "facility_id"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"))
    facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("facilities.id", ondelete="RESTRICT"), index=True
    )
    consent_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("consent_records.id", ondelete="RESTRICT"), unique=True
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    entry_types: Mapped[list[str]] = mapped_column(ARRAY(Text))
    include_profile: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    def is_active(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now


__all__ = ["ENTRY_TYPE_VALUES", "PassportEntry", "PassportEntryType", "PassportShare"]
