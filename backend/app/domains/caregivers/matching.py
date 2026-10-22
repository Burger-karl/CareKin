"""Caregiver matching (Day 14): rank caregivers for one patient and one time slot.

Deterministic, no LLM. This is the tool the matching agent calls (PRD section 8),
so every result carries the factors behind its score for the agent to explain.

Hard filters (a caregiver failing any of these is never returned):
- status `approved` and an hourly rate set: the same rules as booking (TS-1)
- the service location is inside the caregiver's service radius
- availability windows cover the whole slot, in Lagos time
- no accepted, paid or in-progress booking overlapping the slot
- tier at least `min_tier`, and at least MINOR_MIN_TIER for a minor (PRD 8: patient's age)
- not the patient or the caller themself

Score (0-100): weighted distance, rating, language and experience; see SCORE_WEIGHTS.

Only the patient or a guardian may match (the same people who may book), plus ops.
Each match is audited: it reads the patient's age and location.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from fastapi import Request
from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.exceptions import BadRequestError
from app.core.permissions import UserRole
from app.domains.auth.models import User
from app.domains.bookings.models import Booking
from app.domains.bookings.service import ACTIVE_STATUSES, LAGOS, price_minor
from app.domains.caregivers import schemas as s
from app.domains.caregivers.models import (
    AvailabilityWindow,
    Caregiver,
    CaregiverStatus,
    CaregiverTier,
)
from app.domains.patients.models import Guardian, Patient
from app.domains.patients.schemas import GeoPoint
from app.domains.patients.service import (
    PatientAccessDenied,
    PatientNotFound,
    _from_wkb,
    get_access,
)

# Lowest tier offered for a patient under 18. Companions are non-clinical (PRD 4.1);
# a trained carer can recognise crisis warning signs. Confirm with the team.
MINOR_MIN_TIER = CaregiverTier.TRAINED_CARER
TIER_ORDER = {
    CaregiverTier.COMPANION: 0,
    CaregiverTier.TRAINED_CARER: 1,
    CaregiverTier.LICENSED_NURSE: 2,
}

SCORE_WEIGHTS = {"distance": 0.40, "rating": 0.30, "language": 0.20, "experience": 0.10}
# Distance factor halves every this many km: 0 km -> 1.0, 5 km -> 0.5, 15 km -> 0.25.
DISTANCE_HALF_KM = 5.0
# New caregivers start near a 4-star average instead of 0, so they aren't buried.
PRIOR_RATING = 4.0
PRIOR_REVIEWS = 3
# Experience counts up to this many years.
EXPERIENCE_CAP_YEARS = 10
# A window ending at or after this time reaches midnight (end_time can't be 24:00).
END_OF_DAY = time(23, 59)
_DAY_SECONDS = 24 * 60 * 60


# ---------------------------------------------------------------- availability
def _seconds(value: time) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def _local_segments(starts_at: datetime, ends_at: datetime) -> Iterable[tuple[date, int, int]]:
    """Split a slot into (Lagos date, start second, end second) pieces, one per day."""
    start, end = starts_at.astimezone(LAGOS), ends_at.astimezone(LAGOS)
    day = start.date()
    while True:
        day_start = datetime.combine(day, time(0), LAGOS)
        day_end = day_start + timedelta(days=1)
        piece_start, piece_end = max(start, day_start), min(end, day_end)
        if piece_start < piece_end:
            yield (
                day,
                int((piece_start - day_start).total_seconds()),
                int((piece_end - day_start).total_seconds()),
            )
        if end <= day_end:
            return
        day += timedelta(days=1)


def _covers(windows: list[AvailabilityWindow], day: date, start: int, end: int) -> bool:
    """True if the windows valid on `day` cover [start, end) without a gap."""
    spans = sorted(
        (
            _seconds(w.start_time),
            _DAY_SECONDS if w.end_time >= END_OF_DAY else _seconds(w.end_time),
        )
        for w in windows
        if w.day_of_week == day.weekday()
        and (w.valid_from is None or w.valid_from <= day)
        and (w.valid_to is None or day <= w.valid_to)
    )
    reached = start
    for span_start, span_end in spans:
        if span_start > reached:
            break
        reached = max(reached, span_end)
        if reached >= end:
            return True
    return False


def is_available(windows: list[AvailabilityWindow], starts_at: datetime, ends_at: datetime) -> bool:
    """Weekly windows (Lagos time) cover every moment of the slot."""
    return all(
        _covers(windows, day, start, end) for day, start, end in _local_segments(starts_at, ends_at)
    )


# ---------------------------------------------------------------- scoring
@dataclass(frozen=True)
class Fit:
    score: float
    factors: s.MatchFactors
    languages_matched: list[str]


def score(
    *,
    distance_km: float,
    avg_rating: float | None,
    rating_count: int,
    caregiver_languages: list[str],
    wanted_languages: list[str],
    years_experience: int | None,
) -> Fit:
    reviews = rating_count if avg_rating is not None else 0
    rating_avg = ((avg_rating or 0) * reviews + PRIOR_RATING * PRIOR_REVIEWS) / (
        reviews + PRIOR_REVIEWS
    )
    matched = [lang for lang in wanted_languages if lang in caregiver_languages]
    factors = {
        "distance": 1 / (1 + distance_km / DISTANCE_HALF_KM),
        "rating": rating_avg / 5,
        # No preference: every caregiver fits equally, so the order doesn't change.
        "language": len(matched) / len(wanted_languages) if wanted_languages else 1.0,
        "experience": min(years_experience or 0, EXPERIENCE_CAP_YEARS) / EXPERIENCE_CAP_YEARS,
    }
    total = sum(SCORE_WEIGHTS[name] * value for name, value in factors.items())
    return Fit(
        score=round(100 * total, 1),
        factors=s.MatchFactors(**{name: round(value, 3) for name, value in factors.items()}),
        languages_matched=matched,
    )


# ---------------------------------------------------------------- access
@dataclass(frozen=True)
class MatchContext:
    patient: Patient
    guardian_approval_required: bool


async def match_context(db: AsyncSession, user: User, patient_id: uuid.UUID) -> MatchContext:
    """Who may match for this patient, and whether a booking would wait for a guardian."""
    if user.role == UserRole.OPS:
        patient = await db.get(Patient, patient_id)
        if patient is None:
            raise PatientNotFound("Patient not found")
        self_approved = False
    else:
        access = await get_access(db, patient_id, user.id)
        if not access.can_edit:
            raise PatientAccessDenied("Only the patient or a guardian can look for caregivers")
        patient = access.patient
        guardian = None
        if access.role == "guardian":
            guardian = await db.scalar(
                select(Guardian).where(
                    Guardian.patient_id == patient.id, Guardian.user_id == user.id
                )
            )
        self_approved = guardian is not None and guardian.can_approve_caregivers
    # Same rule as bookings: no login of their own, or a minor (ON-3, TS-3).
    needs_guardian = patient.user_id is None or patient.is_minor
    return MatchContext(patient, needs_guardian and not self_approved)


def _required_tier(patient: Patient, requested: CaregiverTier | None) -> CaregiverTier | None:
    tiers = [t for t in (requested, MINOR_MIN_TIER if patient.is_minor else None) if t]
    return max(tiers, key=TIER_ORDER.__getitem__, default=None)


# ---------------------------------------------------------------- matching
async def match_caregivers(
    db: AsyncSession, user: User, data: s.MatchRequest, request: Request | None = None
) -> s.MatchOut:
    if data.starts_at <= datetime.now(LAGOS):
        raise BadRequestError("The time slot must start in the future", code="slot_in_past")
    context = await match_context(db, user, data.patient_id)
    patient = context.patient

    location: GeoPoint | None = data.service_location or _from_wkb(patient.home_location)
    if location is None:
        raise BadRequestError(
            "Send service_location: this patient has no home location saved",
            code="service_location_required",
        )
    search_point = cast(
        func.ST_SetSRID(func.ST_MakePoint(location.longitude, location.latitude), 4326),
        Geography,
    )
    distance_m = func.ST_Distance(Caregiver.base_location, search_point)
    busy = select(Booking.caregiver_id).where(
        Booking.status.in_(ACTIVE_STATUSES),
        Booking.starts_at < data.ends_at,
        Booking.ends_at > data.starts_at,
    )
    min_tier = _required_tier(patient, data.min_tier)
    allowed_tiers = [
        tier
        for tier in CaregiverTier
        if min_tier is None or TIER_ORDER[tier] >= TIER_ORDER[min_tier]
    ]
    excluded_users = {user.id, *([patient.user_id] if patient.user_id else [])}

    stmt = (
        select(Caregiver, User.full_name, distance_m.label("distance_m"))
        .join(User, User.id == Caregiver.user_id)
        .where(
            Caregiver.status == CaregiverStatus.APPROVED,
            Caregiver.hourly_rate_minor.is_not(None),
            Caregiver.base_location.is_not(None),
            Caregiver.service_radius_km.is_not(None),
            Caregiver.tier.in_(allowed_tiers),
            Caregiver.user_id.not_in(excluded_users),
            Caregiver.id.not_in(busy),
            func.ST_DWithin(
                Caregiver.base_location, search_point, Caregiver.service_radius_km * 1000
            ),
        )
        .options(selectinload(Caregiver.availability_windows))
    )

    results: list[s.CaregiverMatch] = []
    for caregiver, full_name, distance in (await db.execute(stmt)).all():
        if not is_available(caregiver.availability_windows, data.starts_at, data.ends_at):
            continue
        distance_km = round(distance / 1000, 1)
        fit = score(
            distance_km=distance_km,
            avg_rating=float(caregiver.avg_rating) if caregiver.avg_rating is not None else None,
            rating_count=caregiver.rating_count,
            caregiver_languages=caregiver.languages,
            wanted_languages=data.languages,
            years_experience=caregiver.years_experience,
        )
        assert caregiver.hourly_rate_minor is not None  # filtered in SQL
        results.append(
            s.CaregiverMatch(
                caregiver_id=caregiver.id,
                full_name=full_name,
                tier=caregiver.tier,
                languages=caregiver.languages,
                years_experience=caregiver.years_experience,
                hourly_rate_minor=caregiver.hourly_rate_minor,
                avg_rating=caregiver.avg_rating,
                rating_count=caregiver.rating_count,
                distance_km=distance_km,
                languages_matched=fit.languages_matched,
                estimated_total_minor=price_minor(
                    caregiver.hourly_rate_minor, data.starts_at, data.ends_at
                ),
                score=fit.score,
                factors=fit.factors,
            )
        )
    # Best score first; ties go to the nearer caregiver, then a stable id order.
    results.sort(key=lambda m: (-m.score, m.distance_km, str(m.caregiver_id)))
    results = results[: data.limit]

    write_audit(
        db,
        action="caregiver.match",
        actor_id=user.id,
        entity_type="patient",
        entity_id=patient.id,
        request=request,
        metadata={
            "booking_type": data.booking_type.value,
            "min_tier": min_tier.value if min_tier else None,
            "results": len(results),
        },
    )
    await db.commit()
    return s.MatchOut(
        patient_id=patient.id,
        guardian_approval_required=context.guardian_approval_required,
        min_tier_applied=min_tier,
        results=results,
    )
