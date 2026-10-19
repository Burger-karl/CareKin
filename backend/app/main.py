"""CareKin FastAPI app: init, middleware, routers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.health import router as health_router
from app.core.redis import close_redis
from app.database import dispose_engine
from app.domains.auth.router import router as auth_router
from app.domains.bookings.router import router as bookings_router
from app.domains.caregivers.router import router as caregivers_router
from app.domains.crisis.router import router as crisis_router
from app.domains.facilities.router import router as facilities_router
from app.domains.ops.router import router as ops_router
from app.domains.passport.router import router as passport_router
from app.domains.patients.router import router as patients_router
from app.domains.payments.router import router as payments_router
from app.domains.trust.router import router as trust_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await close_redis()
    await dispose_engine()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
    # Hide the interactive docs in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # explicit list from settings; "*" is refused when deployed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(health_router)  # /health and /health/ready, outside /api/v1 for load balancers
app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
app.include_router(patients_router, prefix=settings.API_V1_PREFIX)
app.include_router(caregivers_router, prefix=settings.API_V1_PREFIX)
app.include_router(facilities_router, prefix=settings.API_V1_PREFIX)
app.include_router(bookings_router, prefix=settings.API_V1_PREFIX)
app.include_router(payments_router, prefix=settings.API_V1_PREFIX)
app.include_router(crisis_router, prefix=settings.API_V1_PREFIX)
app.include_router(passport_router, prefix=settings.API_V1_PREFIX)
app.include_router(trust_router, prefix=settings.API_V1_PREFIX)
app.include_router(ops_router, prefix=settings.API_V1_PREFIX)
