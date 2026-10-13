"""Alembic environment: async engine, settings-driven URL, model auto-discovery.

Common commands (run from backend/):
    alembic upgrade head                          # apply all migrations
    alembic revision --autogenerate -m "add users" # generate from model changes
    alembic downgrade -1                          # roll back one migration
    alembic check                                 # fail if models and migrations differ
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.database import Base, import_all_models

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Register every model's table on Base.metadata before comparing.
import_all_models()
target_metadata = Base.metadata

# A URL passed with `alembic -x db_url=...` wins; otherwise use app settings.
DATABASE_URL = (
    context.get_x_argument(as_dictionary=True).get("db_url") or get_settings().database_url
)


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # PostGIS: skip its internal tables, render Geography/Geometry types,
        # and create spatial indexes correctly.
        include_object=alembic_helpers.include_object,
        process_revision_directives=alembic_helpers.writer,
        render_item=alembic_helpers.render_item,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it: `alembic upgrade head --sql`."""
    _configure(url=DATABASE_URL, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
