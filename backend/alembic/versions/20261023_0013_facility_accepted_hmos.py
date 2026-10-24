"""Facility accepted HMOs (FF-1 facility finder)

Adds facilities.accepted_hmos, the HMOs a facility accepts, so GET /facilities can
filter by HMO. A text array, as in the ERD (2.4). Existing facilities start with none.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "facilities",
        sa.Column(
            "accepted_hmos",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_facilities_accepted_hmos_limit"),
        "facilities",
        "cardinality(accepted_hmos) <= 50",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_facilities_accepted_hmos_limit"), "facilities", type_="check")
    op.drop_column("facilities", "accepted_hmos")
