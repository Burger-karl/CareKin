"""Facility verification stamp (Day 13, ops console)

Adds facilities.last_verified_at and facilities.verified_by. POST
/ops/facilities/{id}/verify stamps them (OP-3), and every listing shows the date
(FF-3), including the crisis screen's nearest facilities.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "facilities", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("facilities", sa.Column("verified_by", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_facilities_verified_by"), "facilities", ["verified_by"], unique=False)
    op.create_foreign_key(
        op.f("fk_facilities_verified_by_users"),
        "facilities",
        "users",
        ["verified_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_facilities_verified_by_users"), "facilities", type_="foreignkey")
    op.drop_index(op.f("ix_facilities_verified_by"), table_name="facilities")
    op.drop_column("facilities", "verified_by")
    op.drop_column("facilities", "last_verified_at")
