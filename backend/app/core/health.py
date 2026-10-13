"""Liveness and readiness probes.

/health        liveness: the process is up. Never touches the database.
/health/ready  readiness: Postgres answers and PostGIS is installed.
               Returns 503 otherwise, so a load balancer stops sending traffic.
"""

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app import database

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness() -> JSONResponse:
    try:
        info = await database.check_database()
    except Exception as exc:  # noqa: BLE001 - any failure means not ready
        logger.warning("Readiness check failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "database": "unreachable"},
        )
    if info["postgis"] is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unavailable",
                "database": "ok",
                "postgis": "missing",
                "hint": "run `alembic upgrade head`",
            },
        )
    return JSONResponse(content={"status": "ok", "database": "ok", **info})
