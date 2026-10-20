
"""Database operations and spatial queries for CareKin facilities."""

import uuid

from geoalchemy2 import Geography, Geometry
from geoalchemy2.elements import WKTElement
from sqlalchemy import cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.domains.facilities.models import (
    Facility,
    FacilityService,
    ServiceAvailability,
    VerificationStatus,
)
from app.domains.facilities.schemas import (
    FacilityCreate,
    FacilityRead,
    FacilitySearchParams,
    FacilitySearchResult,
    FacilityServiceCreate,
    FacilityServiceRead,
    FacilityServiceUpdate,
    FacilityUpdate,
)


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def make_location(
    latitude: float,
    longitude: float,
) -> WKTElement:
    """Convert GPS coordinates into a PostGIS-compatible point."""

    return WKTElement(
        f"POINT({longitude} {latitude})",
        srid=4326,
    )


def normalize_service_type(value: str) -> str:
    """Normalize service names for consistent matching."""

    return " ".join(value.strip().lower().split())


def build_facility_read(
    facility: Facility,
    latitude: float | None,
    longitude: float | None,
) -> FacilityRead:
    """Build an API response without exposing spatial objects."""

    return FacilityRead(
        id=facility.id,
        name=facility.name,
        address=facility.address,
        phone_number=facility.phone_number,
        latitude=latitude,
        longitude=longitude,
        verification_status=facility.verification_status,
        location_verified=facility.location_verified,
        last_verified_at=facility.last_verified_at,
        created_at=facility.created_at,
        updated_at=facility.updated_at,
    )


async def facility_to_read(
    db: AsyncSession,
    facility: Facility,
) -> FacilityRead:
    """Extract GPS coordinates from a stored facility location."""

    latitude = None
    longitude = None

    if facility.location is not None:
        geometry = cast(Facility.location, Geometry)

        stmt = (
            select(
                func.ST_Y(geometry),
                func.ST_X(geometry),
            )
            .where(Facility.id == facility.id)
        )

        latitude, longitude = (
            await db.execute(stmt)
        ).one()

    return build_facility_read(
        facility,
        latitude,
        longitude,
    )


async def commit_and_refresh(
    db: AsyncSession,
    instance: Facility | FacilityService,
) -> None:
    """Commit a write and refresh its ORM instance."""

    try:
        await db.commit()
        await db.refresh(instance)
    except Exception:
        await db.rollback()
        raise


# ==========================================================
# FACILITY OPERATIONS
# ==========================================================

async def create_facility(
    db: AsyncSession,
    data: FacilityCreate,
    claimed_by: uuid.UUID | None = None,
) -> Facility:
    """Register a facility with an initially unverified status.

    `claimed_by` is the facility admin who will manage it; None for ops-created listings.
    """

    location = None

    # FacilityCreate guarantees both coordinates or neither.
    if data.latitude is not None and data.longitude is not None:
        location = make_location(
            data.latitude,
            data.longitude,
        )

    facility = Facility(
        name=data.name,
        address=data.address,
        phone_number=data.phone_number,
        location=location,
        verification_status=VerificationStatus.PENDING,
        location_verified=False,
        claimed_by=claimed_by,
    )

    db.add(facility)
    await commit_and_refresh(db, facility)

    return facility


async def get_facility(
    db: AsyncSession,
    facility_id: uuid.UUID,
) -> Facility:
    """Retrieve a facility by UUID or raise NotFoundError"""

    facility = await db.get(Facility, facility_id)

    if facility is None:
        raise NotFoundError(
            "Facility not found"
        )

    return facility


async def update_facility(
    db: AsyncSession,
    facility_id: uuid.UUID,
    data: FacilityUpdate,
) -> Facility:
    """Update facility details and invalidate changed location data."""

    facility = await get_facility(db, facility_id)

    # FacilityUpdate already rejects unknown fields, null values and
    # half-supplied coordinates, so only valid fields reach this point.
    updates = data.model_dump(exclude_unset=True)

    latitude = updates.pop("latitude", None)
    longitude = updates.pop("longitude", None)

    for field, value in updates.items():
        if field == "address" and value != facility.address:
            facility.location_verified = False

        setattr(facility, field, value)

    if latitude is not None and longitude is not None:
        facility.location = make_location(
            latitude,
            longitude,
        )

        facility.location_verified = False

    await commit_and_refresh(db, facility)

    return facility


# ==========================================================
# FACILITY SERVICE OPERATIONS
# ==========================================================

async def get_facility_service(
    db: AsyncSession,
    facility_id: uuid.UUID,
    service_id: uuid.UUID,
) -> FacilityService:
    """Fetch a service belonging to the specified facility."""

    stmt = select(FacilityService).where(
        FacilityService.id == service_id,
        FacilityService.facility_id == facility_id,
    )

    service = await db.scalar(stmt)

    if service is None:
        raise NotFoundError(
            "Facility service not found"
        )
    return service


async def list_facility_services(
    db: AsyncSession,
    facility_id: uuid.UUID,
) -> list[FacilityService]:
    """List a facility's services, including unavailable ones."""

    await get_facility(db, facility_id)

    stmt = (
        select(FacilityService)
        .where(FacilityService.facility_id == facility_id)
        .order_by(FacilityService.service_type)
    )

    result = await db.scalars(stmt)

    return list(result.all())


async def add_facility_service(
    db: AsyncSession,
    facility_id: uuid.UUID,
    data: FacilityServiceCreate,
) -> FacilityService:
    """Register a new service without creating duplicate service types."""

    await get_facility(db, facility_id)

    service_type = normalize_service_type(data.service_type)

    stmt = select(FacilityService).where(
        FacilityService.facility_id == facility_id,
        FacilityService.service_type == service_type,
    )

    existing = await db.scalar(stmt)

    if existing is not None:
        raise ConflictError(
            "Facility already offers this service",
        )

    service = FacilityService(
        facility_id=facility_id,
        service_type=service_type,
        availability=ServiceAvailability.AVAILABLE,
        verification_status=VerificationStatus.PENDING,
    )

    db.add(service)

    try:
        await db.commit()
        await db.refresh(service)

    except IntegrityError as exc:
        await db.rollback()

        constraint_name = getattr(
            getattr(exc.orig, "diag", None),
            "constraint_name",
            None,
        )

        if constraint_name == (
            "uq_facility_services_facility_service_type"
        ):
            raise ConflictError(
                "Facility already offers this service",
            ) from exc

        raise

    except Exception:
        await db.rollback()
        raise

    return service


async def update_facility_service(
    db: AsyncSession,
    facility_id: uuid.UUID,
    service_id: uuid.UUID,
    data: FacilityServiceUpdate,
) -> FacilityService:
    """Update the availability of an existing service."""

    service = await get_facility_service(
        db,
        facility_id,
        service_id,
    )

    service.availability = data.availability

    await commit_and_refresh(db, service)

    return service


# ==========================================================
# FACILITY VERIFICATION OPERATIONS
# ==========================================================

async def set_facility_verification(
    db: AsyncSession,
    facility_id: uuid.UUID,
    verification_status: VerificationStatus,
) -> Facility:
    """Set a facility's verification status."""

    facility = await get_facility(db, facility_id)

    facility.verification_status = verification_status

    await commit_and_refresh(db, facility)

    return facility


async def verify_facility_location(
    db: AsyncSession,
    facility_id: uuid.UUID,
) -> Facility:
    """Verify an existing geographic location."""

    facility = await get_facility(db, facility_id)

    if facility.location is None:
        raise ConflictError(
            "Facility has no coordinates to verify"
        )

    facility.location_verified = True

    await commit_and_refresh(db, facility)

    return facility


async def set_service_verification(
    db: AsyncSession,
    facility_id: uuid.UUID,
    service_id: uuid.UUID,
    verification_status: VerificationStatus,
) -> FacilityService:
    """Set the verification status of one facility service."""

    service = await get_facility_service(
        db,
        facility_id,
        service_id,
    )

    service.verification_status = verification_status

    await commit_and_refresh(db, service)

    return service


# ==========================================================
# GEOGRAPHIC FACILITY SEARCH
# ==========================================================

async def find_nearby_facilities(
    db: AsyncSession,
    params: FacilitySearchParams,
) -> list[FacilitySearchResult]:
    """Find verified facilities offering eligible services nearby."""

    # Build the search location in WGS84.
    search_point = cast(
        func.ST_SetSRID(
            func.ST_MakePoint(
                params.longitude,
                params.latitude,
            ),
            4326,
        ),
        Geography(geometry_type="POINT", srid=4326),
    )

    # PostGIS geography uses metres.
    radius_metres = params.radius_km * 1000

    requested_type = (
        normalize_service_type(params.service_type)
        if params.service_type
        else None
    )

    # Eligible services must be verified and available.
    service_conditions = [
        FacilityService.facility_id == Facility.id,
        FacilityService.verification_status
        == VerificationStatus.VERIFIED,
        FacilityService.availability
        == ServiceAvailability.AVAILABLE,
    ]

    if requested_type:
        service_conditions.append(
            FacilityService.service_type == requested_type
        )

    # EXISTS prevents duplicate facility rows.
    eligible_service_exists = (
        select(FacilityService.id)
        .where(*service_conditions)
        .correlate(Facility)
        .exists()
    )

    # Calculate actual distance in metres.
    distance_metres = func.ST_Distance(
        Facility.location,
        search_point,
    )

    # Geography -> Geometry for coordinate extraction.
    location_geometry = cast(
        Facility.location,
        Geometry(geometry_type="POINT", srid=4326),
    )

    # Search facilities, not individual service rows.
    stmt = (
        select(
            Facility,
            distance_metres.label("distance_metres"),
            func.ST_Y(location_geometry).label("latitude"),
            func.ST_X(location_geometry).label("longitude"),
        )
        .where(
            Facility.location.is_not(None),
            Facility.location_verified.is_(True),
            Facility.verification_status
            == VerificationStatus.VERIFIED,
            func.ST_DWithin(
                Facility.location,
                search_point,
                radius_metres,
            ),
            eligible_service_exists,
        )
        .order_by(
            distance_metres,
            Facility.id,
        )
        .limit(params.limit)
        .offset(params.offset)
    )

    rows = (await db.execute(stmt)).all()

    if not rows:
        return []

    facility_ids = [
        facility.id
        for facility, _, _, _ in rows
    ]

    # Fetch eligible services for only the selected facilities.
    services_stmt = select(FacilityService).where(
        FacilityService.facility_id.in_(facility_ids),
        FacilityService.verification_status
        == VerificationStatus.VERIFIED,
        FacilityService.availability
        == ServiceAvailability.AVAILABLE,
    )

    if requested_type:
        services_stmt = services_stmt.where(
            FacilityService.service_type == requested_type
        )

    services_stmt = services_stmt.order_by(
        FacilityService.service_type
    )

    services = list(
        (await db.scalars(services_stmt)).all()
    )

    # Group service records under their respective facilities.
    services_by_facility: dict[
        uuid.UUID,
        list[FacilityServiceRead],
    ] = {
        facility_id: []
        for facility_id in facility_ids
    }

    for service in services:
        services_by_facility[service.facility_id].append(
            FacilityServiceRead.model_validate(service)
        )

    # Assemble the final API response.
    results: list[FacilitySearchResult] = []

    for facility, distance, latitude, longitude in rows:
        facility_read = build_facility_read(
            facility,
            latitude,
            longitude,
        )

        results.append(
            FacilitySearchResult(
                facility=facility_read,
                distance_km=round(distance / 1000, 2),
                matching_services=services_by_facility[
                    facility.id
                ],
            )
        )

    return results
