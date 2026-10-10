"""CareKin FastAPI app: init, middleware, routers."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.domains.auth.router import router as auth_router
from app.domains.patients.router import router as patients_router
from app.domains.caregivers.router import router as caregivers_router
from app.domains.facilities.router import router as facilities_router
from app.domains.bookings.router import router as bookings_router
from app.domains.crisis.router import router as crisis_router
from app.domains.passport.router import router as passport_router
from app.domains.trust.router import router as trust_router
from app.domains.ops.router import router as ops_router

app = FastAPI(title="CareKin API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before staging
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
app.include_router(patients_router, prefix=settings.API_V1_PREFIX)
app.include_router(caregivers_router, prefix=settings.API_V1_PREFIX)
app.include_router(facilities_router, prefix=settings.API_V1_PREFIX)
app.include_router(bookings_router, prefix=settings.API_V1_PREFIX)
app.include_router(crisis_router, prefix=settings.API_V1_PREFIX)
app.include_router(passport_router, prefix=settings.API_V1_PREFIX)
app.include_router(trust_router, prefix=settings.API_V1_PREFIX)
app.include_router(ops_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
