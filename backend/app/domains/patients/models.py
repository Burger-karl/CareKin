"""patients SQLAlchemy models: Patient, Guardian, EmergencyContact, CareProfile, FamilyLink.

Schema follows docs/architecture/ERD.md section 2.2.
- Health fields are field-encrypted (PRD HP-4) and can't be filtered or sorted in SQL.
- FKs to `patients` are ON DELETE RESTRICT: health data is never removed by cascade.
- Minor status is derived from `date_of_birth`, never stored (ON-3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    ForeignKey,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.config import get_settings
from app.database import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

# Age of majority in Nigeria. Below this a patient needs a guardian (ON-3).
ADULT_AGE = 18


class AlertChannel(StrEnum):
    """`alert_channel` enum, shared with crisis alerts and reminders."""

    SMS = "sms"
    WHATSAPP = "whatsapp"
    PUSH = "push"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


# ---------------------------------------------------------------- encryption
# TODO: move to app/core/encryption.py once that module is built, and import it here.
@lru_cache
def _fernet() -> Fernet:
    key = get_settings().FIELD_ENCRYPTION_KEY
    if key is None:
        raise RuntimeError(
            "FIELD_ENCRYPTION_KEY is not set, so health data can't be encrypted. Generate one: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.get_secret_value().encode())


class EncryptedText(TypeDecorator[str]):
    """Text encrypted with Fernet before it reaches Postgres (stored as `bytea`)."""

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        return _fernet().encrypt(value.encode())

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        try:
            return _fernet().decrypt(bytes(value)).decode()
        except InvalidToken as exc:
            raise RuntimeError(
                "Could not decrypt a health field: FIELD_ENCRYPTION_KEY doesn't match the key "
                "the data was written with"
            ) from exc


# ---------------------------------------------------------------- models
class Patient(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "patients"

    # Null for a minor without their own login; the record is managed by a guardian.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    full_name: Mapped[str] = mapped_column(String(200))
    date_of_birth: Mapped[date] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(String(20))
    genotype: Mapped[str | None] = mapped_column(EncryptedText)
    blood_group: Mapped[str | None] = mapped_column(EncryptedText)
    home_location: Mapped[Any | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=True)
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    def age_on(self, day: date) -> int:
        dob = self.date_of_birth
        return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))

    @property
    def age(self) -> int:
        return self.age_on(datetime.now(UTC).date())

    @property
    def is_minor(self) -> bool:
        return self.age < ADULT_AGE


class Guardian(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "guardians"
    __table_args__ = (UniqueConstraint("patient_id", "user_id"),)

    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    relationship: Mapped[str] = mapped_column(String(50))  # parent, sibling, spouse
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    can_approve_caregivers: Mapped[bool] = mapped_column(  # TS-3
        Boolean, default=True, server_default="true"
    )
    receives_crisis_alerts: Mapped[bool] = mapped_column(  # CR-1
        Boolean, default=True, server_default="true"
    )


class EmergencyContact(UUIDPrimaryKeyMixin, Base):
    """A person alerted in a crisis. Contacts need no CareKin account."""

    __tablename__ = "emergency_contacts"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(20))  # E.164
    relationship: Mapped[str] = mapped_column(String(50))
    channel: Mapped[AlertChannel] = mapped_column(
        Enum(AlertChannel, name="alert_channel", values_callable=_enum_values),
        default=AlertChannel.SMS,
        server_default=AlertChannel.SMS.value,
    )
    priority: Mapped[int] = mapped_column(  # alert order, 1 first
        SmallInteger, default=1, server_default="1"
    )


class CareProfile(UUIDPrimaryKeyMixin, Base):
    """One per patient, fully field-encrypted."""

    __tablename__ = "care_profiles"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), unique=True
    )
    known_triggers: Mapped[str | None] = mapped_column(EncryptedText)
    medications_overview: Mapped[str | None] = mapped_column(  # a list only, never dosing advice
        EncryptedText
    )
    allergies: Mapped[str | None] = mapped_column(EncryptedText)
    care_instructions: Mapped[str | None] = mapped_column(EncryptedText)
    primary_facility_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("facilities.id", ondelete="SET NULL"), index=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False, sort_order=101
    )


class FamilyLink(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A family-abroad account following a patient (P1)."""

    __tablename__ = "family_links"
    __table_args__ = (UniqueConstraint("patient_id", "user_id"),)

    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    # What the patient lets them see, e.g. {"health_data": true, "location": false}.
    share_scope: Mapped[dict[str, bool]] = mapped_column(JSONB, default=dict, server_default="{}")
    can_fund: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
