"""Tests: seed scripts and the crisis simulator (build plan Day 3), against PostGIS."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from app import database
from app.config import Environment, get_settings
from app.domains.auth.models import User
from app.domains.crisis.facilities import facilities_available, facility_models
from app.domains.crisis.models import AlertStatus, CrisisAlert, CrisisEvent, CrisisStatus
from seed import crisis_simulator
from seed._common import refuse_unsafe_environment
from seed.facilities import FACILITIES, seed_facilities

FACILITIES_READY = facilities_available()
needs_facilities = pytest.mark.skipif(
    not FACILITIES_READY, reason="facilities models not built yet (facilities domain, Day 7)"
)


async def count(model) -> int:
    async with database.SessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(model))


@needs_facilities
async def test_facility_seed_is_unverified_by_default_and_idempotent():
    facility, service = facility_models()
    async with database.SessionLocal() as db:
        assert await seed_facilities(db) == len(FACILITIES)
    async with database.SessionLocal() as db:
        assert await seed_facilities(db) == 0  # second run adds nothing

    assert await count(facility) == len(FACILITIES)
    assert await count(service) == sum(len(f.services) for f in FACILITIES)
    async with database.engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT name, verification_status::text, phone, emergency_phone,"
                    " ST_Y(location::geometry), ST_X(location::geometry) FROM facilities"
                )
            )
        ).all()
    assert {r[1] for r in rows} == {"unverified"}
    assert all(r[2] is None and r[3] is None for r in rows)  # no guessed phone numbers
    unth = next(r for r in rows if r[0].startswith("University of Nigeria Teaching"))
    assert unth[4] == pytest.approx(6.3238) and unth[5] == pytest.approx(7.4820)


@needs_facilities
async def test_demo_verified_flag_only_outside_deployed_environments(monkeypatch):
    async with database.SessionLocal() as db:
        await seed_facilities(db, demo_verified=True)
    async with database.engine.connect() as conn:
        statuses = set(
            await conn.scalars(text("SELECT DISTINCT verification_status::text FROM facilities"))
        )
    assert statuses == {"verified"}

    monkeypatch.setattr(get_settings(), "ENVIRONMENT", Environment.STAGING)
    async with database.SessionLocal() as db:
        with pytest.raises(SystemExit):
            await seed_facilities(db, demo_verified=True)


@pytest.mark.skipif(FACILITIES_READY, reason="facilities models exist")
async def test_facility_seed_explains_it_is_waiting_for_the_facilities_models():
    async with database.SessionLocal() as db:
        with pytest.raises(SystemExit, match="facilities models aren't built yet"):
            await seed_facilities(db)


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
    if FACILITIES_READY:
        async with database.SessionLocal() as db:
            await seed_facilities(db, demo_verified=True)

    report = await crisis_simulator.simulate(
        database.SessionLocal, presses=4, fail_every=3, resolve=True
    )

    assert len(report.presses) == 4 and all(p.created for p in report.presses)
    assert all(p.alerts == 3 for p in report.presses)  # guardian + 2 contacts each
    # Demo facilities are in range once the facility directory exists.
    assert all(bool(p.nearest) is FACILITIES_READY for p in report.presses)
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
