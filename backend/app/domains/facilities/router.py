"""facilities routes. Facility, FacilityService, SCD filters."""
from fastapi import APIRouter

from app.core.routing import CareKinRoute

router = APIRouter(prefix="/facilities", tags=["facilities"], route_class=CareKinRoute)
