"""bookings routes. Booking, Shift, CheckIn, Escrow."""
from fastapi import APIRouter

router = APIRouter(prefix="/bookings", tags=["bookings"])
