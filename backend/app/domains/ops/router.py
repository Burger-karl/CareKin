"""ops routes. Operations console (admin)."""
from fastapi import APIRouter

router = APIRouter(prefix="/ops", tags=["ops"])
