from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from geoalchemy2 import Geography
from geoalchemy2.elements import WKBElement

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum as SAEnum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import (
    Base,
    UUIDPrimaryKeyMixin,
    TimestampMixin,
)


class VerificationStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class ServiceAvailability(str, Enum):
    AVAILABLE = "available"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


# ---------------- FACILITY ----------------

# Most HMOs a facility can list (FF-1). Keeps one listing from bloating every search row.
MAX_ACCEPTED_HMOS = 50


class Facility(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "facilities"
    __table_args__ = (
        CheckConstraint(
            f"cardinality(accepted_hmos) <= {MAX_ACCEPTED_HMOS}",
            name="accepted_hmos_limit",
        ),
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    address: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    phone_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    location: Mapped[WKBElement | None] = mapped_column(
        Geography(
            geometry_type="POINT",
            srid=4326,
            spatial_index=True,
        ),
        nullable=True,
    )

    verification_status: Mapped[VerificationStatus] = mapped_column(
        SAEnum(
            VerificationStatus,
            name="verification_status",
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        nullable=False,
        default=VerificationStatus.PENDING,
        server_default=text("'pending'"),
    )

    location_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    # Facility admin who registered the listing and may manage it.
    # Null for listings created by ops and not yet claimed.
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Stamped by POST /ops/facilities/{id}/verify (OP-3) and shown on every listing (FF-3),
    # so families can see how fresh the information is. Null until ops first checks it.
    last_verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # The ops user who last checked the listing.
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # FF-1: HMOs the facility accepts, as the facility names them ("Hygeia HMO").
    # Stored trimmed and de-duplicated ignoring case; search compares ignoring case.
    accepted_hmos: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )

    services: Mapped[list[FacilityService]] = relationship(
        back_populates="facility",
        lazy="raise",
    )


# ---------------- FACILITY SERVICE ----------------

class FacilityService(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "facility_services"

    __table_args__ = (
        UniqueConstraint(
            "facility_id",
            "service_type",
            name="uq_facility_services_facility_service_type",
        ),
    )

    facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("facilities.id"),
        nullable=False,
        index=True,
    )

    service_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    availability: Mapped[ServiceAvailability] = mapped_column(
        SAEnum(
            ServiceAvailability,
            name="service_availability",
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        nullable=False,
        default=ServiceAvailability.AVAILABLE,
        server_default=text("'available'"),
    )

    verification_status: Mapped[VerificationStatus] = mapped_column(
        SAEnum(
            VerificationStatus,
            name="verification_status",
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        nullable=False,
        default=VerificationStatus.PENDING,
        server_default=text("'pending'"),
    )

    facility: Mapped[Facility] = relationship(
        back_populates="services",
        lazy="raise",
    )
