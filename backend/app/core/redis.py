"""Shared async Redis client (OTP codes, rate limits; Celery uses its own connection).

Usage in a router:
    from app.core.redis import RedisClient

    async def handler(redis: RedisClient): ...
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: Redis | None = None


def get_redis_client() -> Redis:
    """Create the client lazily, so importing the app never opens a connection."""
    global _client
    if _client is None:
        _client = Redis.from_url(
            get_settings().REDIS_URL.unicode_string(),
            decode_responses=True,
            health_check_interval=30,
        )
    return _client


async def get_redis() -> Redis:
    """FastAPI dependency. Override it in tests to use a separate database."""
    return get_redis_client()


async def close_redis() -> None:
    """Called on application shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("Redis client closed")


RedisClient = Annotated[Redis, Depends(get_redis)]
