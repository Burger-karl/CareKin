# """facilities SQLAlchemy models: Facility, FacilityService (ERD section 2.4).

# Added with the crisis button, which ranks verified facilities by distance (CR-2).
# The facilities domain build (finder, portal, ops verification endpoints) adds its
# routes on top of these models.
# """

# from __future__ import annotations

# import uuid
# from datetime import datetime
# from enum import StrEnum
# from typing import Any

# from geoalchemy2 import Geography, Geometry
# from sqlalchemy import Boolean, Enum, ForeignKey, String, Text, UniqueConstraint, cast, func, text
# from sqlalchemy.dialects.postgresql import ARRAY
# from sqlalchemy.orm import Mapped, column_property, mapped_column

# from app.database import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


# def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
#     return [member.value for member in enum_cls]


# class FacilityType(StrEnum):
#     TEACHING_HOSPITAL = "teaching_hospital"
#     GENERAL_HOSPITAL = "general_hospital"
#     PRIVATE_HOSPITAL = "private_hospital"
#     CLINIC = "clinic"
#     LAB = "lab"
#     BLOOD_BANK = "blood_bank"


# class VerificationStatus(StrEnum):
#     UNVERIFIED = "unverified"
#     PENDING = "pending"
#     VERIFIED = "verified"
#     STALE = "stale"  # was verified, now due for re-verification (FF-5)


# class ServiceType(StrEnum):
#     HAEMATOLOGIST = "haematologist"
#     EMERGENCY_24H = "emergency_24h"
#     TRANSFUSION = "transfusion"
#     BLOOD_BANK = "blood_bank"
#     GENOTYPE_TESTING = "genotype_testing"


# # Services that let a facility receive a patient in crisis (CR-2).
# CRISIS_CAPABLE_SERVICES: frozenset[ServiceType] = frozenset(
#     {ServiceType.EMERGENCY_24H, ServiceType.HAEMATOLOGIST, ServiceType.TRANSFUSION}
# )


# class Facility(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
#     __tablename__ = "facilities"

#     name: Mapped[str] = mapped_column(String(200))
#     facility_type: Mapped[FacilityType] = mapped_column(
#         Enum(FacilityType, name="facility_type", values_callable=_enum_values)
#     )
#     address: Mapped[str] = mapped_column(Text)
#     lga: Mapped[str | None] = mapped_column(String(80))  # local government area
#     phone: Mapped[str | None] = mapped_column(String(20))
#     emergency_phone: Mapped[str | None] = mapped_column(String(20))  # one-tap call (CR-2)
#     location: Mapped[Any] = mapped_column(
#         Geography(geometry_type="POINT", srid=4326, spatial_index=True)
#     )
#     accepted_hmos: Mapped[list[str]] = mapped_column(  # FF-1 filter
#         ARRAY(String(80)), default=list, server_default=text("'{}'::varchar[]")
#     )
#     claimed_by: Mapped[uuid.UUID | None] = mapped_column(
#         ForeignKey("users.id", ondelete="SET NULL"), index=True
#     )
#     verification_status: Mapped[VerificationStatus] = mapped_column(
#         Enum(VerificationStatus, name="verification_status", values_callable=_enum_values),
#         default=VerificationStatus.UNVERIFIED,
#         server_default=VerificationStatus.UNVERIFIED.value,
#         index=True,
#     )
#     last_verified_at: Mapped[datetime | None]  # shown on every listing (FF-3)
#     verified_by: Mapped[uuid.UUID | None] = mapped_column(
#         ForeignKey("users.id", ondelete="SET NULL"), index=True
#     )

#     # Read-only coordinates, computed by PostGIS when the row loads.
#     latitude: Mapped[float] = column_property(func.ST_Y(cast(location, Geometry)))
#     longitude: Mapped[float] = column_property(func.ST_X(cast(location, Geometry)))


# class FacilityService(UUIDPrimaryKeyMixin, TimestampMixin, Base):
#     __tablename__ = "facility_services"
#     __table_args__ = (UniqueConstraint("facility_id", "service_type"),)

#     facility_id: Mapped[uuid.UUID] = mapped_column(
#         ForeignKey("facilities.id", ondelete="CASCADE"), index=True
#     )
#     service_type: Mapped[ServiceType] = mapped_column(
#         Enum(ServiceType, name="service_type", values_callable=_enum_values)
#     )
#     is_available: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
#     hours: Mapped[str | None] = mapped_column(String(80))
#     notes: Mapped[str | None] = mapped_column(Text)
