"""crisis Celery tasks.

Each run opens its own engine without a connection pool: Celery runs each task in a
fresh event loop, and pooled asyncpg connections can't move between loops.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.domains.crisis import service
from app.integrations.sms import get_sms_client
from app.worker import celery_app


@asynccontextmanager
async def task_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


@celery_app.task(name="crisis.sweep_alerts")
def sweep_alerts() -> dict[str, int]:
    """Retry failed or stuck crisis alerts. Scheduled by Beat every 30 seconds."""

    async def run() -> service.DeliveryReport:
        async with task_sessions() as sessions:
            return await service.sweep_pending_alerts(sessions, get_sms_client())

    return asdict(asyncio.run(run()))


@celery_app.task(name="crisis.send_alerts")
def send_alerts(event_id: str) -> dict[str, int]:
    """Send one crisis's pending alerts. For ops tooling and manual re-runs."""

    async def run() -> service.DeliveryReport:
        async with task_sessions() as sessions:
            return await service.deliver_pending_alerts(
                sessions, get_sms_client(), event_id=uuid.UUID(event_id)
            )

    return asdict(asyncio.run(run()))
