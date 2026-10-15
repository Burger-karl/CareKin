"""facilities routes. Facility, FacilityService, SCD filters.

Errors raised by the service (NotFoundError, ConflictError, ...) are turned into the
team's {"error": {"code", "message"}} responses by the handlers in app/core/exceptions.py,
so no custom route class is needed.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/facilities", tags=["facilities"])
