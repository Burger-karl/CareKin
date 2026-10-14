"""Shared application exceptions and the handlers that turn them into JSON.

Every error the API returns has the same shape, so web and mobile can handle
them in one place:

    {"error": {"code": "otp_invalid", "message": "The code is incorrect.", "details": {...}}}

Raise one of the subclasses below from services and dependencies. Never raise
HTTPException with free-form text for business errors; give it a stable `code`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class CareKinError(Exception):
    """Base exception for CareKin. Subclasses set a default status and code."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"
    message: str = "The request could not be processed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details
        self.headers = headers
        super().__init__(self.message)


class BadRequestError(CareKinError):
    pass


class UnauthorizedError(CareKinError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    message = "Authentication is required."

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class ForbiddenError(CareKinError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You do not have permission to do this."


class NotFoundError(CareKinError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The resource was not found."


class ConflictError(CareKinError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The resource already exists."


class RateLimitedError(CareKinError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Try again later."

    def __init__(
        self, message: str | None = None, *, retry_after: int | None = None, **kwargs: Any
    ) -> None:
        if retry_after is not None:
            kwargs.setdefault("headers", {"Retry-After": str(max(int(retry_after), 1))})
            details = kwargs.pop("details", None) or {}
            details["retry_after_seconds"] = max(int(retry_after), 1)
            kwargs["details"] = details
        super().__init__(message, **kwargs)


class ServiceUnavailableError(CareKinError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "A dependency is unavailable. Try again shortly."


def _error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"error": error}


async def _carekin_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, CareKinError)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.code, exc.message, exc.details),
        headers=exc.headers,
    )


async def _http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized"}.get(
        exc.status_code, "http_error"
    )
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(code, message),
        headers=getattr(exc, "headers", None),
    )


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Drop the submitted input from each error: it can contain phone numbers or codes.
    errors = [
        {"loc": err.get("loc"), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,  # constant name differs between Starlette versions
        content=_error_body(
            "validation_error", "The request body is invalid.", jsonable_encoder(errors)
        ),
    )


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body("internal_error", "Something went wrong."),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Called once from main.py."""
    app.add_exception_handler(CareKinError, _carekin_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
