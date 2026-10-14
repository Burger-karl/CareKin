"""Shared application exceptions."""


class CareKinError(Exception):
    """Base exception for CareKin."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(CareKinError):
    """The requested resource does not exist."""


class ConflictError(CareKinError):
    """The request clashes with the current state (duplicate, invalid transition)."""
