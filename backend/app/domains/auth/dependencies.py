"""Auth dependencies every other domain uses.

    from app.domains.auth.dependencies import CurrentUser, require_consent
    from app.domains.auth.models import ConsentType

    @router.patch("/patients/{id}/care-profile",
                  dependencies=[require_consent(ConsentType.HEALTH_DATA)])
    async def update_care_profile(id: UUID, user: CurrentUser, db: DbSession): ...

For role checks use app.core.permissions.require_roles.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import TokenError, decode_access_token
from app.database import DbSession
from app.domains.auth.models import ConsentType, User

# auto_error=False so a missing token returns our JSON error shape, not FastAPI's.
bearer_scheme = HTTPBearer(auto_error=False, description="Access token from /auth/login")


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Log in to continue.", code="not_authenticated")
    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError as exc:
        if exc.reason == "expired":
            # Clients should call /auth/refresh when they see this code.
            raise UnauthorizedError("Your session has expired.", code="token_expired") from exc
        raise UnauthorizedError("Your session is invalid. Log in again.", code="token_invalid") from exc

    user = await db.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("This account is not active.", code="account_disabled")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_consent(consent_type: ConsentType) -> Any:
    """Refuse the request (403 consent_required) unless the user has granted `consent_type`.

    ON-2: put require_consent(ConsentType.HEALTH_DATA) on every endpoint that stores
    health data (care profiles, passport entries, genotype).
    """
    # Imported here: service imports schemas, which import this package's models.
    from app.domains.auth.service import has_consent

    async def _check(user: CurrentUser, db: DbSession) -> User:
        if not await has_consent(db, user.id, consent_type):
            raise ForbiddenError(
                "Consent is required before this information can be stored.",
                code="consent_required",
                details={"consent_type": consent_type.value, "next": "/auth/consent"},
            )
        return user

    return Depends(_check)
