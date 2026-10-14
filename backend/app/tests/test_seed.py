"""Tests: seed scripts and the crisis simulator (build plan Day 3), against PostGIS."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app import database
from app.config import Environment, get_settings
from app.domains.auth.models import User
from app.domains.crisis.models import AlertStatus, CrisisAlert, CrisisEvent, CrisisStatus
from app.domains.facilities.models import Facility, FacilityService, VerificationStatus
from seed import crisis_simulator
from seed._common import refuse_unsafe_environment
from seed.facilities import FACILITIES, seed_facilities


async def count(model) -> int:
    async with database.SessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(model))


async def test_facility_seed_is_unverified_by_default_and_idempotent():
    async with database.SessionLocal() as db:
        assert await seed_facilities(db) == len(FACILITIES)
    async with database.SessionLocal() as db:
        assert await seed_facilities(db) == 0  # second run adds nothing

    assert await count(Facility) == len(FACILITIES)
    assert await count(FacilityService) == sum(len(f.services) for f in FACILITIES)
    async with database.SessionLocal() as s:
        facilities = list(await s.scalars(select(Facility)))
    assert {f.verification_status for f in facilities} == {VerificationStatus.UNVERIFIED}
    assert all(
        f.phone is None and f.emergency_phone is None for f in facilities
    )  # no guessed numbers
    unth = next(f for f in facilities if f.name.startswith("University of Nigeria Teaching"))
    assert unth.latitude == pytest.approx(6.3238) and unth.longitude == pytest.approx(7.4820)


async def test_demo_verified_flag_only_outside_deployed_environments(monkeypatch):
    async with database.SessionLocal() as db:
        await seed_facilities(db, demo_verified=True)
    async with database.SessionLocal() as s:
        statuses = set(await s.scalars(select(Facility.verification_status)))
    assert statuses == {VerificationStatus.VERIFIED}

    monkeypatch.setattr(get_settings(), "ENVIRONMENT", Environment.STAGING)
    async with database.SessionLocal() as db:
        with pytest.raises(SystemExit):
            await seed_facilities(db, demo_verified=True)


@pytest.mark.parametrize(
    ("environment", "allow_staging", "refused"),
    [
        (Environment.PRODUCTION, True, True),
        (Environment.STAGING, False, True),
        (Environment.STAGING, True, False),
        (Environment.DEVELOPMENT, False, False),
    ],
)
def test_seeds_refuse_production(monkeypatch, environment, allow_staging, refused):
    monkeypatch.setattr(get_settings(), "ENVIRONMENT", environment)
    if refused:
        with pytest.raises(SystemExit):
            refuse_unsafe_environment(allow_staging=allow_staging)
    else:
        refuse_unsafe_environment(allow_staging=allow_staging)


async def test_crisis_simulator_end_to_end():
    async with database.SessionLocal() as db:
        await seed_facilities(db, demo_verified=True)

    report = await crisis_simulator.simulate(
        database.SessionLocal, presses=4, fail_every=3, resolve=True
    )

    assert len(report.presses) == 4 and all(p.created for p in report.presses)
    assert all(p.alerts == 3 for p in report.presses)  # guardian + 2 contacts each
    assert all(p.nearest for p in report.presses)  # demo facilities are in range
    assert all(p.trigger_ms < 2000 for p in report.presses)
    assert report.sent + report.failed == 12 and report.failed == 4  # every 3rd SMS failed
    assert report.replay_alerted_again is False
    assert report.resolved == 4

    async with database.SessionLocal() as s:
        statuses = set(await s.scalars(select(CrisisEvent.status)))
        failed = await s.scalar(
            select(func.count()).where(CrisisAlert.status == AlertStatus.FAILED)
        )
    assert statuses == {CrisisStatus.CANCELLED}
    assert failed == 4  # left for the sweep (which skips them now the crises are closed)


async def test_simulator_reuses_demo_people_on_a_second_run():
    first = await crisis_simulator.simulate(database.SessionLocal, presses=2, resolve=True)
    second = await crisis_simulator.simulate(database.SessionLocal, presses=2)
    assert {p.event_id for p in first.presses}.isdisjoint({p.event_id for p in second.presses})
    # Each family has two accounts (patient and guardian; contacts aren't users), reused.
    assert await count(User) == 2 * 2


def test_simulator_report_prints(capsys):
    report = crisis_simulator.SimulationReport(
        presses=[crisis_simulator.PressResult(uuid.uuid4(), True, 12.3, 3, "UNTH", 1.25)],
        sent=3,
    )
    crisis_simulator.print_report(report)
    out = capsys.readouterr().out
    assert "UNTH (1.2 km)" in out
    assert "Replay alerted anyone again: no" in out
