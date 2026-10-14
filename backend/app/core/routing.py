"""Router-level translation of CareKin domain errors into HTTP responses."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute

from app.core.exceptions import CareKinError, ConflictError, NotFoundError

ERROR_STATUS: dict[type[CareKinError], int] = {
    NotFoundError: 404,
    ConflictError: 409,
}


class CareKinRoute(APIRoute):
    """Turns CareKinError raised by services into HTTP errors.

    Use with APIRouter(route_class=CareKinRoute).
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except CareKinError as exc:
                status_code = next(
                    (code for cls, code in ERROR_STATUS.items() if isinstance(exc, cls)),
                    400,
                )
                raise HTTPException(status_code=status_code, detail=exc.message) from exc

        return route_handler
