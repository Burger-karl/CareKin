"""Seed Enugu SCD-relevant facilities (build plan Day 3; used by the finder and crisis button).

    python -m seed.facilities                  # all listings unverified (the honest default)
    python -m seed.facilities --demo-verified  # mark them verified so demos show results

These are real, public hospitals, but every detail here is UNCONFIRMED:
- coordinates are approximate and services are what we expect, not what we checked;
- phone numbers are left empty on purpose: a wrong number in a crisis is worse than none.
Facility verification (Day 7) replaces them with checked data. Until then they stay
`unverified`, and the crisis button only lists verified facilities.

`--demo-verified` exists for local demos only and is refused outside development/test.
Safe to run repeatedly: facilities that already exist (by name) are left alone.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from geoalchemy2 import WKTElement
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Environment, get_settings
from app.database import SessionLocal
from seed._common import close, prepare, refuse_unsafe_environment

UNCONFIRMED = "Seed data, unconfirmed: check during facility verification (Day 7)."


@dataclass(frozen=True)
class SeedFacility:
    name: str
    facility_type: str
    address: str
    lga: str
    lat: float  # approximate
    lng: float
    services: tuple[str, ...]


FACILITIES: tuple[SeedFacility, ...] = (
    SeedFacility(
        "University of Nigeria Teaching Hospital (UNTH)",
        "teaching_hospital",
        "Enugu-Port Harcourt Expressway, Ituku-Ozalla",
        "Nkanu West",
        6.3238,
        7.4820,
        ("emergency_24h", "haematologist", "transfusion", "blood_bank", "genotype_testing"),
    ),
    SeedFacility(
        "ESUT Teaching Hospital, Parklane",
        "teaching_hospital",
        "Parklane, GRA, Enugu",
        "Enugu North",
        6.4512,
        7.5033,
        ("emergency_24h", "haematologist", "transfusion", "genotype_testing"),
    ),
    SeedFacility(
        "National Orthopaedic Hospital, Enugu",
        "general_hospital",
        "Enugu-Abakaliki Road, Enugu",
        "Enugu East",
        6.4620,
        7.5450,
        ("emergency_24h",),
    ),
    SeedFacility(
        "Mother of Christ Specialist Hospital",
        "private_hospital",
        "Ogui Road, Enugu",
        "Enugu North",
        6.4350,
        7.4960,
        ("emergency_24h", "genotype_testing"),
    ),
    SeedFacility(
        "Niger Foundation Hospital",
        "private_hospital",
        "Independence Layout, Enugu",
        "Enugu North",
        6.4570,
        7.5160,
        ("emergency_24h", "transfusion"),
    ),
    SeedFacility(
        "Annunciation Specialist Hospital",
        "private_hospital",
        "Emene, Enugu",
        "Enugu East",
        6.4720,
        7.5970,
        ("emergency_24h", "transfusion"),
    ),
    SeedFacility(
        "Enugu State Polyclinic, Asata",
        "general_hospital",
        "Asata, Enugu",
        "Enugu North",
        6.4400,
        7.4880,
        ("emergency_24h", "genotype_testing"),
    ),
    SeedFacility(
        "Bishop Shanahan Hospital",
        "general_hospital",
        "Nsukka",
        "Nsukka",
        6.8600,
        7.3950,
        ("emergency_24h", "transfusion"),
    ),
    SeedFacility(
        "University of Nigeria Medical Centre",
        "clinic",
        "University of Nigeria, Nsukka",
        "Nsukka",
        6.8670,
        7.4050,
        ("genotype_testing",),
    ),
)


async def seed_facilities(db: AsyncSession, *, demo_verified: bool = False) -> int:
    """Insert missing facilities. Returns how many were added."""
    from app.domains.facilities.models import (
        Facility,
        FacilityService,
        FacilityType,
        ServiceType,
        VerificationStatus,
    )

    if demo_verified and get_settings().ENVIRONMENT not in (
        Environment.DEVELOPMENT,
        Environment.TEST,
    ):
        raise SystemExit("--demo-verified is only allowed in development and test.")

    existing = set(await db.scalars(select(Facility.name)))
    added = 0
    now = datetime.now(UTC)
    for item in FACILITIES:
        if item.name in existing:
            continue
        facility = Facility(
            name=item.name,
            facility_type=FacilityType(item.facility_type),
            address=item.address,
            lga=item.lga,
            location=WKTElement(f"POINT({item.lng} {item.lat})", srid=4326),
            verification_status=(
                VerificationStatus.VERIFIED if demo_verified else VerificationStatus.UNVERIFIED
            ),
            last_verified_at=now if demo_verified else None,
        )
        db.add(facility)
        await db.flush()
        for service in item.services:
            db.add(
                FacilityService(
                    facility_id=facility.id,
                    service_type=ServiceType(service),
                    notes=UNCONFIRMED,
                )
            )
        added += 1
    await db.commit()
    return added


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--demo-verified",
        action="store_true",
        help="mark seeded facilities verified (development/test only)",
    )
    parser.add_argument("--allow-staging", action="store_true")
    args = parser.parse_args(argv)
    refuse_unsafe_environment(allow_staging=args.allow_staging)
    prepare()
    try:
        async with SessionLocal() as db:
            added = await seed_facilities(db, demo_verified=args.demo_verified)
    finally:
        await close()
    print(f"Facilities: {added} added, {len(FACILITIES) - added} already present.")
    return added


if __name__ == "__main__":
    asyncio.run(main())
