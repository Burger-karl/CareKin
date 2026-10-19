"""Trust and safety rules shared with other domains (no imports from their services, so
payments can open an incident without a circular import).

Human response targets (TS-2). The PRD leaves the number open ("target value to be
set"); these are the team's starting values, to confirm in the pilot.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.trust.models import Incident, IncidentCategory, IncidentStatus, Severity

RESPONSE_TARGETS: dict[Severity, timedelta] = {
    Severity.CRITICAL: timedelta(hours=1),
    Severity.HIGH: timedelta(hours=4),
    Severity.MEDIUM: timedelta(hours=24),
    Severity.LOW: timedelta(hours=72),
}


def response_due(severity: Severity, opened_at: datetime) -> datetime:
    return opened_at + RESPONSE_TARGETS[severity]


def open_system_incident(
    db: AsyncSession,
    *,
    booking_id: uuid.UUID | None,
    category: IncidentCategory,
    severity: Severity,
    description: str,
) -> Incident:
    """An incident CareKin opened itself (e.g. money that needs a refund). Caller commits."""
    now = datetime.now(UTC)
    incident = Incident(
        booking_id=booking_id,
        reported_by=None,
        category=category,
        severity=severity,
        is_dispute=False,
        status=IncidentStatus.OPEN,
        description=description,
        response_due_at=response_due(severity, now),
    )
    db.add(incident)
    return incident
