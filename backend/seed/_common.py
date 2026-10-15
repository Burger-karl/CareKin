"""Shared helpers for seed and simulator scripts. Run them from backend/:

python -m seed.facilities --demo-verified
python -m seed.crisis_simulator --presses 5
"""

from __future__ import annotations

import sys

from app.config import Environment, get_settings
from app.database import engine, import_all_models


def refuse_unsafe_environment(*, allow_staging: bool = False) -> None:
    """Seeds write demo data. Never in production; staging only when asked."""
    env = get_settings().ENVIRONMENT
    if env is Environment.PRODUCTION or (env is Environment.STAGING and not allow_staging):
        sys.exit(f"Refusing to seed ENVIRONMENT={env.value}.")


def prepare() -> None:
    import_all_models()


async def close() -> None:
    await engine.dispose()
