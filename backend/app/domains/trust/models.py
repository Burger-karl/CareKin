"""trust SQLAlchemy models: Incident (TS-2, TS-5). AuditLog lives in app/core/audit.py.

Schema follows docs/architecture/ERD.md section 2.9.
- `description` and `resolution` can hold health or safety details, so they're encrypted.
- `response_due_at` is the human response target (TS-2), set from the severity.
- `is_dispute` holds the booking's escrow (TS-5). Not in the ERD: `booking_status_before`
  remembers the booking's status when this incident put it into `disputed`, so closing
  the dispute can put it back.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Enum, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from app.core.encryption import EncryptedText
from app.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.domains.bookings.models import BookingStatus


class IncidentCategory(StrEnum):
    SAFETY = "safety"
    NEGLECT = "neglect"
    CONDUCT = "conduct"
    PAYMENT = "payment"
    OTHER = "other"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


OPEN_STATUSES = (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING)


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=_enum_values)


class Incident(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        # The ops queue: open incidents by response deadline (ERD section 6).
        Index(
            "ix_incidents_response_due_at_open",
            "response_due_at",
            postgresql_where=text("status IN ('open', 'investigating')"),
        ),
    )

    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"), index=True
    )
    # Null when the system opened it (e.g. a payment that needs a refund).
    reported_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    against_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    category: Mapped[IncidentCategory] = mapped_column(
        _pg_enum(IncidentCategory, "incident_category")
    )
    severity: Mapped[Severity] = mapped_column(_pg_enum(Severity, "severity"))
    is_dispute: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    status: Mapped[IncidentStatus] = mapped_column(
        _pg_enum(IncidentStatus, "incident_status"), index=True
    )
    description: Mapped[str] = mapped_column(EncryptedText)
    response_due_at: Mapped[datetime]
    first_response_at: Mapped[datetime | None]
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    resolved_at: Mapped[datetime | None]
    resolution: Mapped[str | None] = mapped_column(EncryptedText)
    # Reuses the bookings type (created with the bookings table), so never creates or
    # drops it.
    booking_status_before: Mapped[BookingStatus | None] = mapped_column(
        ENUM(
            BookingStatus,
            name="booking_status",
            values_callable=_enum_values,
            create_type=False,
        )
    )
