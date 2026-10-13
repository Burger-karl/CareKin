# from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
# from sqlalchemy.orm import DeclarativeBase

# from app.config import settings

# engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
# SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# class Base(DeclarativeBase):
#     pass


# async def get_db():
#     async with SessionLocal() as session:
#         yield session




"""Database engine, sessions and the declarative base every model inherits from.

Conventions (see docs/architecture/CareKin_ERD.md):
- Every model subclasses `Base`. Constraint names follow NAMING_CONVENTION so
  Alembic migrations are deterministic and can be downgraded.
- UUID primary keys come from `UUIDPrimaryKeyMixin`; timestamps from
  `CreatedAtMixin` or `TimestampMixin`. All datetimes are timezone-aware (UTC).
- Sessions do not autocommit. Services call `await session.commit()` explicitly,
  so money and health-data writes are never committed by accident. Anything not
  committed when the request ends is rolled back.

Usage in a router:
    from app.database import DbSession

    @router.get("/things/{id}")
    async def get_thing(id: UUID, db: DbSession): ...
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, ClassVar

from fastapi import Depends
from sqlalchemy import DateTime, MetaData, Uuid, func, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Deterministic constraint names, e.g. fk_bookings_patient_id_patients.
# Without these, Postgres invents names and Alembic can't reliably drop or alter them.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every CareKin model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),  # timestamptz everywhere
        uuid.UUID: Uuid(as_uuid=True),  # native Postgres uuid
    }

    def __repr__(self) -> str:
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class UUIDPrimaryKeyMixin:
    """`id uuid PRIMARY KEY DEFAULT gen_random_uuid()`.

    The id is assigned when the object is constructed, so `obj.id` is usable
    before flush: services need it to link related rows and audit entries in
    one transaction. The server default covers rows inserted with raw SQL.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
        sort_order=-100,  # id is the first column in every table
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.id is None:
            self.id = uuid.uuid4()


class CreatedAtMixin:
    """`created_at timestamptz NOT NULL DEFAULT now()`. For append-only tables."""

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False, sort_order=100
    )


class TimestampMixin(CreatedAtMixin):
    """Adds `updated_at`, refreshed on every ORM update."""

    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False, sort_order=101
    )


# ---------------------------------------------------------------- engine
def build_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine from settings.

    Tests use NullPool: every connection is opened and closed on the current
    event loop, which avoids "attached to a different loop" errors in pytest.
    """
    connect_args: dict[str, Any] = {
        "server_settings": {
            "application_name": f"carekin-api-{settings.ENVIRONMENT.value}",
            "timezone": "UTC",
            "statement_timeout": str(settings.DB_STATEMENT_TIMEOUT_MS),
        }
    }
    if settings.is_test:
        return create_async_engine(
            settings.database_url,
            echo=settings.DB_ECHO,
            poolclass=NullPool,
            connect_args=connect_args,
        )
    return create_async_engine(
        settings.database_url,
        echo=settings.DB_ECHO,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_recycle=settings.DB_POOL_RECYCLE,
        connect_args=connect_args,
    )


engine: AsyncEngine = build_engine(get_settings())

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,  # objects stay usable after commit (e.g. in the response)
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request.

    Uncommitted work is rolled back when the request ends, including on errors.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]


# ---------------------------------------------------------------- health
async def check_database(db_engine: AsyncEngine | None = None) -> dict[str, str | None]:
    """Round-trip to Postgres and report the PostGIS version.

    Raises if the database is unreachable. `postgis` is None if the extension
    isn't installed yet (run `alembic upgrade head`).
    """
    db_engine = db_engine or engine
    async with db_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        server_version = await conn.scalar(text("SHOW server_version"))
        postgis = await conn.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'postgis'")
        )
    return {"postgres": server_version, "postgis": postgis}


async def dispose_engine() -> None:
    """Close all pooled connections. Called on application shutdown."""
    await engine.dispose()
    logger.info("Database engine disposed")


# ---------------------------------------------------------------- models
def import_all_models() -> list[str]:
    """Import every `models` module so all tables register on Base.metadata.

    Alembic autogenerate and the test suite call this, so a new domain's
    models are picked up without editing a central list. Looks in
    app/domains/*/models.py, app/agents/models.py and app/core/*.py.
    """
    imported: list[str] = []
    from app import domains

    for mod in pkgutil.iter_modules(domains.__path__):
        if mod.ispkg:
            name = f"app.domains.{mod.name}.models"
            if importlib.util.find_spec(name) is not None:
                importlib.import_module(name)
                imported.append(name)
    for name in ("app.agents.models", "app.core.audit"):
        if importlib.util.find_spec(name) is not None:
            importlib.import_module(name)
            imported.append(name)
    return imported


