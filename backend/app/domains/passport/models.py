"""passport SQLAlchemy models: PassportEntry (ERD section 2.8).

Added with the crisis button, which writes a crisis entry on resolve (CR-4).
PassportShare and the passport endpoints come with the passport build.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.encryption import EncryptedText
from app.database import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


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
