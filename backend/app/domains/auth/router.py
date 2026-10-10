"""auth routes. JWT, OAuth2, OTP, consent. Models: User, ConsentRecord."""
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
