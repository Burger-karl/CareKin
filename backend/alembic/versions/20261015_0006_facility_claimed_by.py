"""Facility ownership: facilities.claimed_by

Adds the facility admin who registered a listing (ERD 2.4), so only that admin or
ops can manage it. Nullable, ON DELETE SET NULL: listings created by ops start
unclaimed, and deleting a user leaves the listing in place.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("facilities", sa.Column("claimed_by", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_facilities_claimed_by"), "facilities", ["claimed_by"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_facilities_claimed_by_users"),
        "facilities",
        "users",
        ["claimed_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_facilities_claimed_by_users"), "facilities", type_="foreignkey")
    op.drop_index(op.f("ix_facilities_claimed_by"), table_name="facilities")
    op.drop_column("facilities", "claimed_by")
