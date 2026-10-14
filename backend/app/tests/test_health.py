import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.core import health


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.fixture
def probe_client():
    probe_app = FastAPI()
    probe_app.include_router(health.router)
    return TestClient(probe_app)


def test_liveness(probe_client):
    resp = probe_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_when_database_and_postgis_ok(probe_client, monkeypatch):
    async def ok():
        return {"postgres": "16.4", "postgis": "3.4.2"}

    monkeypatch.setattr(database, "check_database", ok)
    resp = probe_client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok", "postgres": "16.4", "postgis": "3.4.2"}


def test_not_ready_when_database_unreachable(probe_client, monkeypatch):
    async def down():
        raise ConnectionRefusedError

    monkeypatch.setattr(database, "check_database", down)
    resp = probe_client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["database"] == "unreachable"


def test_not_ready_when_postgis_missing(probe_client, monkeypatch):
    async def no_postgis():
        return {"postgres": "16.4", "postgis": None}

    monkeypatch.setattr(database, "check_database", no_postgis)
    resp = probe_client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["postgis"] == "missing"
