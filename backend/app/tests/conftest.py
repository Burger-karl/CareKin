"""Test fixtures.

Tests run against real Postgres and Redis (no SQLite stand-ins: the schema uses
Postgres enums, INET, JSONB and later PostGIS). Locally:

    docker compose up -d db redis
    docker compose exec db createdb -U carekin carekin_test
    cd backend && pytest

Override with TEST_DATABASE_URL / TEST_REDIS_URL if your services live elsewhere.
Redis database 15 is used and flushed between tests.
"""

import os

# Must run before anything imports app.config.
os.environ["ENVIRONMENT"] = "test"
os.environ["USE_MOCK_INTEGRATIONS"] = "true"
os.environ["LLM_PROVIDER"] = "mock"  # tests never call a real model
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://carekin:carekin@localhost:5432/carekin_test"
)
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")

from collections.abc import AsyncIterator, Callable, Iterator  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.redis import get_redis_client  # noqa: E402
from app.database import Base, SessionLocal, engine, import_all_models  # noqa: E402
from app.integrations.sms import MockSMSClient, get_sms_client  # noqa: E402
from app.main import app  # noqa: E402

API = get_settings().API_V1_PREFIX


@pytest.fixture(scope="session", autouse=True)
async def _schema() -> AsyncIterator[None]:
    """Build the schema once per run from the models."""
    import_all_models()
    async with engine.begin() as conn:
        try:
            async with conn.begin_nested():
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        except Exception:  # noqa: BLE001 - only spatial tables need it, and none exist yet
            pass
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await get_redis_client().aclose()
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_state() -> AsyncIterator[None]:
    yield
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    if tables:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await get_redis_client().flushdb()


@pytest.fixture
def sms() -> Iterator[MockSMSClient]:
    """A fresh mock SMS outbox per test, wired into the app."""
    mock = MockSMSClient()
    app.dependency_overrides[get_sms_client] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_sms_client, None)


@pytest.fixture
async def api(sms: MockSMSClient) -> AsyncIterator[httpx.AsyncClient]:
    """Async client against the app, rooted at /api/v1."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://test{API}") as client:
        yield client


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@pytest.fixture
def client() -> TestClient:
    """Synchronous client for simple endpoint checks (e.g. /health)."""
    return TestClient(app)


@pytest.fixture
def latest_code(sms: MockSMSClient) -> Callable[[str], str]:
    """Pull the last code texted to a phone out of the mock outbox."""

    def _get(phone: str) -> str:
        message = sms.last_to(phone)
        assert message is not None, f"no SMS sent to {phone}"
        return message.body.split("code is ")[1].split(".")[0]

    return _get
