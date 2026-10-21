"""How the crisis button finds facilities, using the facilities domain's models (Day 7).

A facility is shown in a crisis only if the facilities domain has fully verified it:
    Facility          verification_status = verified, location_verified, has a location
    FacilityService   verification_status = verified, availability = available,
                      and its service type can receive a patient in crisis (CR-2)
In an emergency an unverified claim of "24h haematology" is worse than no answer.

Service types are free text in the facilities domain (stored lowercased by
facilities.service.normalize_service_type). CRISIS_SERVICE_NAMES lists the names that
count, compared with underscores read as spaces. Agree the canonical names with the
facilities owner (the ERD uses emergency_24h, haematologist, transfusion).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from geoalchemy2 import Geography, Geometry
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domains.facilities.models import (
    Facility,
    FacilityService,
    ServiceAvailability,
    VerificationStatus,
)

CRISIS_SERVICE_NAMES = (
    "emergency 24h",
    "24h emergency",
    "24 hour emergency",
    "haematologist",
    "haematology",
    "hematologist",
    "hematology",
    "transfusion",
    "blood transfusion",
)


@dataclass
class NearbyResult:
    facilities: list[dict[str, Any]]
    radius_km: float | None
    widened: bool
    available: bool = True


def _crisis_service():
    service_name = func.replace(FacilityService.service_type, "_", " ")
    return (
        FacilityService.verification_status == VerificationStatus.VERIFIED,
        FacilityService.availability == ServiceAvailability.AVAILABLE,
        service_name.in_(CRISIS_SERVICE_NAMES),
    )


async def nearby(db: AsyncSession, latitude: float | None, longitude: float | None) -> NearbyResult:
    """Fully verified, crisis-capable facilities, nearest first. Widens the search once."""
    if latitude is None or longitude is None:
        return NearbyResult([], None, False)
    settings = get_settings()
    radii = [(settings.CRISIS_FACILITY_RADIUS_KM, False)]
    if settings.CRISIS_FACILITY_FALLBACK_RADIUS_KM > settings.CRISIS_FACILITY_RADIUS_KM:
        radii.append((settings.CRISIS_FACILITY_FALLBACK_RADIUS_KM, True))
    for radius_km, widened in radii:
        rows = await _within(db, latitude, longitude, radius_km)
        if rows:
            return NearbyResult(rows, radius_km, widened)
    return NearbyResult([], radii[-1][0], len(radii) > 1)


async def _within(
    db: AsyncSession, latitude: float, longitude: float, radius_km: float
) -> list[dict[str, Any]]:
    origin = cast(func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326), Geography)
    point = cast(Facility.location, Geometry)
    distance = func.ST_Distance(Facility.location, origin)
    service_type = FacilityService.service_type
    stmt = (
        select(
            Facility.id,
            Facility.name,
            Facility.address,
            Facility.phone_number,
            func.ST_Y(point),
            func.ST_X(point),
            distance,
            func.array_agg(aggregate_order_by(service_type, service_type)),
            Facility.last_verified_at,
        )
        .join(FacilityService, FacilityService.facility_id == Facility.id)
        .where(
            Facility.verification_status == VerificationStatus.VERIFIED,
            Facility.location_verified.is_(True),
            Facility.location.is_not(None),
            func.ST_DWithin(Facility.location, origin, radius_km * 1000),
            *_crisis_service(),
        )
        .group_by(Facility.id)
        .order_by(distance, Facility.id)
        .limit(get_settings().CRISIS_FACILITY_LIMIT)
    )
    results = []
    for facility_id, name, address, phone, lat, lng, distance_m, services, verified_at in (
        await db.execute(stmt)
    ).all():
        phone = phone or None  # the seed leaves unconfirmed numbers empty
        results.append(
            {
                "id": facility_id,
                "name": name,
                "facility_type": None,  # not in the facilities model yet
                "address": address,
                "latitude": lat,
                "longitude": lng,
                "distance_m": round(distance_m),
                "call_number": phone,
                "phone": phone,
                "emergency_phone": None,  # not in the facilities model yet
                "crisis_services": list(services),
                "verification_status": VerificationStatus.VERIFIED.value,
                "last_verified_at": verified_at,  # stamped by ops verification (OP-3)
                "directions_url": (
                    f"https://www.google.com/maps/dir/?api=1&destination={lat:.6f},{lng:.6f}"
                ),
            }
        )
    return results


async def facility_name(db: AsyncSession, facility_id: uuid.UUID) -> str | None:
    """The facility's name, or None if it doesn't exist."""
    return await db.scalar(select(Facility.name).where(Facility.id == facility_id))
