"""Role-based access control.

One role per account (ERD section 8). The kickoff's letter codes map to the
enum values stored in Postgres:

    P  patient          G  guardian        C  caregiver
    F  facility_admin   O  ops             A  family_abroad (ON-5, P1)

Usage in a router:

    from app.core.permissions import UserRole, require_roles

    @router.get("/ops/dashboard", dependencies=[require_roles(UserRole.OPS)])
    async def dashboard(): ...

    # or, when the handler needs the user object:
    @router.post("/caregivers")
    async def create(user: Annotated[User, require_roles(UserRole.CAREGIVER)]): ...
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import Depends


class UserRole(StrEnum):
    PATIENT = "patient"
    GUARDIAN = "guardian"
    FAMILY_ABROAD = "family_abroad"
    CAREGIVER = "caregiver"
    FACILITY_ADMIN = "facility_admin"
    OPS = "ops"

    @property
    def code(self) -> str:
        """The single-letter code used in the kickoff documents."""
        return ROLE_CODES[self]


ROLE_CODES: dict[UserRole, str] = {
    UserRole.PATIENT: "P",
    UserRole.GUARDIAN: "G",
    UserRole.CAREGIVER: "C",
    UserRole.FACILITY_ADMIN: "F",
    UserRole.OPS: "O",
    UserRole.FAMILY_ABROAD: "A",
}

# Roles a person can choose when signing up. Ops accounts are created by an admin
# (seed script or the ops console), and family-abroad accounts wait for ON-5 (P1).
SELF_REGISTRATION_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.PATIENT, UserRole.GUARDIAN, UserRole.CAREGIVER, UserRole.FACILITY_ADMIN}
)


def require_roles(*roles: UserRole) -> Any:
    """Dependency that lets the request through only for users holding one of `roles`.

    Returns the current user, so it can be used both in `dependencies=[...]` and as
    a parameter annotation. Raises 401 without a valid token and 403 for other roles.
    """
    if not roles:
        raise ValueError("require_roles() needs at least one role")
    allowed = frozenset(roles)

    # Imported here to avoid a circular import: auth.dependencies imports UserRole.
    from app.domains.auth.dependencies import get_current_user
    from app.core.exceptions import ForbiddenError

    async def _check(user=Depends(get_current_user)):  # noqa: ANN001, ANN202
        if user.role not in allowed:
            raise ForbiddenError(
                "Your account type cannot use this endpoint.",
                code="role_forbidden",
                details={"allowed_roles": sorted(r.value for r in allowed)},
            )
        return user

    return Depends(_check)
