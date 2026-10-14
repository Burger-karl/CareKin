"""How the crisis button reads facilities, owned by the facilities domain (Day 7).

The crisis domain doesn't define facility tables. It looks up `Facility` and
`FacilityService` in app/domains/facilities/models.py at runtime:

- Until those models exist, crisis still works: alerts go out and the response says
  `facilities_available: false` with an empty list, and a resolve can't name a facility.
- As soon as they exist, nearby facilities appear, with no change here.

What crisis needs from the facilities models (ERD section 2.4). Facilities owner:
please keep these names, or tell the crisis owner if they change.

    Facility          id, name, facility_type, address, phone, emergency_phone,
                      location (Geography POINT, SRID 4326),
                      verification_status, last_verified_at
    FacilityService   facility_id, service_type, is_available

Enum columns must store the ERD's lowercase values ("verified", "emergency_24h", ...).
They are compared as text, so a native enum or a plain string column both work.

The facilities migration should also add the foreign keys left out until then:
    crisis_events.outcome_facility_id   -> facilities.id  ON DELETE SET NULL
    passport_entries.facility_id        -> facilities.id  ON DELETE SET NULL
    care_profiles.primary_facility_id   -> facilities.id  ON DELETE SET NULL
"""

from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass
from typing import Any

from geoalchemy2 import Geography, Geometry
from sqlalchemy import String, and_, cast, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

# Services that mean "can receive a patient in a sickle-cell crisis" (CR-2).
CRISIS_CAPABLE_SERVICES = ("emergency_24h", "haematologist", "transfusion")
# Only verified listings, or verified ones due for a recheck (FF-5).
LISTABLE_STATUSES = ("verified", "stale")


def facility_models() -> tuple[Any, Any] | None:
    """(Facility, FacilityService), or None until the facilities domain defines them."""
    module = importlib.import_module("app.domains.facilities.models")
    facility = getattr(module, "Facility", None)
    service = getattr(module, "FacilityService", None)
    if facility is None or service is None:
        return None
    return facility, service


def facilities_available() -> bool:
    return facility_models() is not None


@dataclass
class NearbyResult:
    facilities: list[dict[str, Any]]
    radius_km: float | None
    widened: bool
    available: bool = True


async def nearby(db: AsyncSession, latitude: float | None, longitude: float | None) -> NearbyResult:
    """Verified, crisis-capable facilities, nearest first. Widens the search once."""
    if not facilities_available():
        return NearbyResult([], None, False, available=False)
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
    facility, service = facility_models()  # type: ignore[misc]
    origin = cast(func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326), Geography)
    point = cast(facility.location, Geometry)
    distance = func.ST_Distance(facility.location, origin)
    service_text = cast(service.service_type, String)
    stmt = (
        select(
            facility.id,
            facility.name,
            cast(facility.facility_type, String),
            facility.address,
            func.ST_Y(point),
            func.ST_X(point),
            distance,
            facility.phone,
            facility.emergency_phone,
            cast(facility.verification_status, String),
            facility.last_verified_at,
            func.array_agg(aggregate_order_by(service_text, service_text)),
        )
        .join(
            service,
            and_(
                service.facility_id == facility.id,
                service.is_available.is_(True),
                service_text.in_(CRISIS_CAPABLE_SERVICES),
            ),
        )
        .where(
            cast(facility.verification_status, String).in_(LISTABLE_STATUSES),
            func.ST_DWithin(facility.location, origin, radius_km * 1000),
        )
        .group_by(facility.id)
        .order_by(distance, facility.id)
        .limit(get_settings().CRISIS_FACILITY_LIMIT)
    )
    results = []
    for (
        facility_id,
        name,
        facility_type,
        address,
        lat,
        lng,
        distance_m,
        phone,
        emergency_phone,
        status,
        last_verified_at,
        services,
    ) in (await db.execute(stmt)).all():
        results.append(
            {
                "id": facility_id,
                "name": name,
                "facility_type": facility_type,
                "address": address,
                "latitude": lat,
                "longitude": lng,
                "distance_m": round(distance_m),
                "call_number": emergency_phone or phone,
                "phone": phone,
                "emergency_phone": emergency_phone,
                "crisis_services": list(services),
                "verification_status": status,
                "last_verified_at": last_verified_at,
                "directions_url": (
                    f"https://www.google.com/maps/dir/?api=1&destination={lat:.6f},{lng:.6f}"
                ),
            }
        )
    return results


async def facility_name(db: AsyncSession, facility_id: uuid.UUID) -> str | None:
    """The facility's name, or None if it doesn't exist (or facilities aren't built yet)."""
    models = facility_models()
    if models is None:
        return None
    facility, _ = models
    return await db.scalar(select(facility.name).where(facility.id == facility_id))
