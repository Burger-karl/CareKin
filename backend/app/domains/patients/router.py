"""patients routes. Patient, Guardian, CareProfile."""
from fastapi import APIRouter

router = APIRouter(prefix="/patients", tags=["patients"])
