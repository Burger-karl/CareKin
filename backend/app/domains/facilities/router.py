"""facilities routes. Facility, FacilityService, SCD filters.

Reads are public so anyone can browse the facility finder. Writes need a login:
facility admins and ops can register a facility, and only the admin who claimed it
(or ops) can change it or its services.

Errors raised by the service (NotFoundError, ConflictError, ...) are turned into the
team's {"error": {"code", "message"}} responses by the handlers in app/core/exceptions.py,
so no custom route class is needed.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.exceptions import ForbiddenError
from app.core.permissions import UserRole, require_roles
from app.database import DbSession
from app.domains.auth.models import User
from app.domains.facilities import service
from app.domains.facilities.schemas import (
    FacilityCreate,
    FacilityFilters,
    FacilityListItem,
    FacilityRead,
    FacilitySearchParams,
    FacilitySearchResult,
    FacilityServiceCreate,
    FacilityServiceRead,
    FacilityServiceUpdate,
    FacilityUpdate,
)

router = APIRouter(prefix="/facilities", tags=["facilities"])


# ==========================================================
# AUTHORIZATION
# ==========================================================

# Logged in as a facility admin or ops (401 without a token, 403 for other roles).
FacilityRegistrar = Annotated[
    User,
    require_roles(UserRole.FACILITY_ADMIN, UserRole.OPS),
]


async def require_facility_manager(
    facility_id: uuid.UUID,
    db: DbSession,
    user: FacilityRegistrar,
) -> User:
    """Let through ops, or the facility admin who claimed this facility."""

    if user.role == UserRole.OPS:
        return user

    facility = await service.get_facility(db, facility_id)

    if facility.claimed_by != user.id:
        raise ForbiddenError(
            "You can only manage facilities you registered.",
            code="facility_not_owned",
        )

    return user


FacilityManager = Annotated[
    User,
    Depends(require_facility_manager),
]

# 0. FACILITY FINDER (FF-1, FF-2)

@router.get(
    "",
    response_model=list[FacilityListItem],
    status_code=status.HTTP_200_OK,
    summary="Search facilities with SCD filters",
)
async def search_facilities(
    db: DbSession,
    response: Response,
    filters: Annotated[FacilityFilters, Query()],
):
    """Verified facilities, filtered by SCD service, HMO, text and distance.

    Every filter given must match. With latitude and longitude the results are nearest
    first and carry `distance_km`; otherwise they're sorted by name. The total number of
    matches is in the `X-Total-Count` header, for paging.
    """

    items, total = await service.search_facilities(db, filters)
    response.headers["X-Total-Count"] = str(total)
    return items


# 1. FIND NEARBY FACILITIES

@router.get(
    "/nearby",
    response_model=list[FacilitySearchResult],
    status_code=status.HTTP_200_OK,
    summary="Find nearby facilities",
)
async def find_nearby_facilities(
    db: DbSession,
    params: Annotated[FacilitySearchParams, Depends()],
):
    """Find verified facilities with available services nearby."""

    return await service.find_nearby_facilities(
        db=db,
        params=params,
    )


# 2. REGISTER FACILITY

@router.post(
    "",
    response_model=FacilityRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a facility",
)
async def register_facility(
    data: FacilityCreate,
    db: DbSession,
    user: FacilityRegistrar,
):
    """Register a new facility.

    A facility admin becomes its owner; ops create it unclaimed.
    """

    facility = await service.create_facility(
        db=db,
        data=data,
        claimed_by=user.id if user.role == UserRole.FACILITY_ADMIN else None,
    )

    return await service.facility_to_read(
        db,
        facility,
    )

# 3. GET FACILITY

@router.get(
    "/{facility_id}",
    response_model=FacilityRead,
    status_code=status.HTTP_200_OK,
    summary="Get facility details",
)
async def get_facility(
    facility_id: uuid.UUID,
    db: DbSession,
):
    """Retrieve facility information by UUID."""

    facility = await service.get_facility(
        db=db,
        facility_id=facility_id,
    )

    return await service.facility_to_read(
        db,
        facility,
    )

# 4. UPDATE FACILITY

@router.patch(
    "/{facility_id}",
    response_model=FacilityRead,
    status_code=status.HTTP_200_OK,
    summary="Update facility",
)
async def update_facility(
    facility_id: uuid.UUID,
    data: FacilityUpdate,
    db: DbSession,
    _manager: FacilityManager,
):
    """Update facility details."""

    facility = await service.update_facility(
        db=db,
        facility_id=facility_id,
        data=data,
    )

    return await service.facility_to_read(
        db,
        facility,
    )

# 5. ADD FACILITY SERVICE

@router.post(
    "/{facility_id}/service",
    response_model=FacilityServiceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add facility service",
)
async def add_facility_service(
    facility_id: uuid.UUID,
    data: FacilityServiceCreate,
    db: DbSession,
    _manager: FacilityManager,
):
    """Add a service offered by a facility."""

    facility_service = await service.add_facility_service(
        db=db,
        facility_id=facility_id,
        data=data,
    )

    return FacilityServiceRead.model_validate(
        facility_service
    )

# 6. LIST FACILITY SERVICES

@router.get(
    "/{facility_id}/service",
    response_model=list[FacilityServiceRead],
    status_code=status.HTTP_200_OK,
    summary="List facility services",
)
async def list_facility_services(
    facility_id: uuid.UUID,
    db: DbSession,
):
    """Retrieve all services belonging to a facility."""

    facility_services = await service.list_facility_services(
        db=db,
        facility_id=facility_id,
    )

    return [
        FacilityServiceRead.model_validate(facility_service)
        for facility_service in facility_services
    ]

# 7. UPDATE FACILITY SERVICE

@router.patch(
    "/{facility_id}/service/{service_id}",
    response_model=FacilityServiceRead,
    status_code=status.HTTP_200_OK,
    summary="Update service availability",
)
async def update_facility_service(
    facility_id: uuid.UUID,
    service_id: uuid.UUID,
    data: FacilityServiceUpdate,
    db: DbSession,
    _manager: FacilityManager,
):
    """Update an existing facility service's availability."""

    facility_service = await service.update_facility_service(
        db=db,
        facility_id=facility_id,
        service_id=service_id,
        data=data,
    )

    return FacilityServiceRead.model_validate(
        facility_service
    )
