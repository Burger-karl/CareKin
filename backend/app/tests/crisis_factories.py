"""Test data builders for the crisis tests. Each commits so the API sees the rows."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, date, datetime
from typing import Any

from geoalchemy2 import WKTElement
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.permissions import UserRole
from app.core.security import create_access_token
from app.domains.auth.models import ConsentRecord, ConsentType, User
from app.domains.facilities.models import (
    Facility,
    FacilityService,
    FacilityType,
    ServiceType,
    VerificationStatus,
)
from app.domains.patients.models import EmergencyContact, FamilyLink, Guardian, Patient

# Central Enugu. 0.01 degrees of latitude is about 1.1 km.
ENUGU = (6.4413, 7.4988)


def point(lat: float, lng: float) -> WKTElement:
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


def phone() -> str:
    return "+234803" + "".join(random.choices("0123456789", k=7))


def auth(user: User) -> dict[str, str]:
    token, _expires_at = create_access_token(user.id, user.role.value)
    return {"Authorization": f"Bearer {token}"}


async def make_user(db: AsyncSession, role: UserRole, name: str = "Test User") -> User:
    user = User(phone=phone(), full_name=name, role=role, phone_verified_at=datetime.now(UTC))
    db.add(user)
    await db.commit()
    return user


async def make_patient(
    db: AsyncSession,
    *,
    user: User | None,
    created_by: User,
    name: str = "Chidi Okafor",
    home: tuple[float, float] | None = None,
) -> Patient:
    patient = Patient(
        user_id=user.id if user else None,
        full_name=name,
        date_of_birth=date(1996, 5, 17),
        created_by=created_by.id,
        home_location=point(*home) if home else None,
    )
    db.add(patient)
    await db.commit()
    return patient


async def add_guardian(
    db: AsyncSession, patient: Patient, user: User, *, alerts: bool = True, primary: bool = False
) -> Guardian:
    guardian = Guardian(
        patient_id=patient.id,
        user_id=user.id,
        relationship="parent",
        is_primary=primary,
        receives_crisis_alerts=alerts,
    )
    db.add(guardian)
    await db.commit()
    return guardian


async def add_contact(
    db: AsyncSession, patient: Patient, name: str, number: str, priority: int = 1
) -> EmergencyContact:
    contact = EmergencyContact(
        patient_id=patient.id, name=name, phone=number, relationship="friend", priority=priority
    )
    db.add(contact)
    await db.commit()
    return contact


async def add_family_link(
    db: AsyncSession, patient: Patient, user: User, scope: dict[str, bool]
) -> FamilyLink:
    link = FamilyLink(patient_id=patient.id, user_id=user.id, share_scope=scope)
    db.add(link)
    await db.commit()
    return link


async def make_facility(
    db: AsyncSession,
    name: str,
    lat: float,
    lng: float,
    services: tuple[ServiceType, ...] = (ServiceType.EMERGENCY_24H,),
    status: VerificationStatus = VerificationStatus.VERIFIED,
    available: bool = True,
) -> Facility:
    facility = Facility(
        name=name,
        facility_type=FacilityType.GENERAL_HOSPITAL,
        address=f"{name}, Enugu",
        phone=phone(),
        emergency_phone=phone(),
        location=point(lat, lng),
        verification_status=status,
        last_verified_at=datetime.now(UTC) if status is VerificationStatus.VERIFIED else None,
    )
    db.add(facility)
    await db.flush()
    for service in services:
        db.add(
            FacilityService(facility_id=facility.id, service_type=service, is_available=available)
        )
    await db.commit()
    return facility


async def grant_consent(
    db: AsyncSession,
    user: User,
    *,
    granted: bool = True,
    revoked: bool = False,
    policy_version: str | None = None,
    when: datetime | None = None,
) -> ConsentRecord:
    granted_at = when or datetime.now(UTC)
    record = ConsentRecord(
        user_id=user.id,
        consent_type=ConsentType.HEALTH_DATA,
        policy_version=policy_version or get_settings().CONSENT_POLICY_VERSION,
        granted=granted,
        granted_at=granted_at,
        revoked_at=granted_at if revoked else None,
    )
    db.add(record)
    await db.commit()
    return record


def trigger_body(
    patient: Patient, *, location: tuple[float, float] | None = ENUGU, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {"patient_id": str(patient.id), "client_event_id": str(uuid.uuid4())}
    if location is not None:
        body["location"] = {"latitude": location[0], "longitude": location[1], "accuracy_m": 12}
    body.update(extra)
    return body
