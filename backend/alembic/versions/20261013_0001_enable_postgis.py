"""Enable PostGIS

The first migration. Every table with a location (facilities, caregivers,
bookings, crisis events, check-ins) depends on PostGIS, so it comes first.

On managed Postgres (Render, RDS, Supabase) the app user may not be allowed to
create extensions. If this fails with "permission denied", have an admin run
`CREATE EXTENSION postgis;` once; this migration then does nothing.

gen_random_uuid() is built into Postgres 13+, so pgcrypto isn't needed.

Revision ID: 0001
Revises:
Create Date: 2026-10-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS postgis")
