"""Family links can come from an emergency contact

Access granted to an emergency contact remembers which contact it came from, so the
two are managed together and removing the contact removes the access.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("family_links", sa.Column("emergency_contact_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_family_links_emergency_contact_id_emergency_contacts"),
        "family_links",
        "emergency_contacts",
        ["emergency_contact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_family_links_emergency_contact_id"),
        "family_links",
        ["emergency_contact_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_family_links_emergency_contact_id"), table_name="family_links")
    op.drop_constraint(
        op.f("fk_family_links_emergency_contact_id_emergency_contacts"),
        "family_links",
        type_="foreignkey",
    )
    op.drop_column("family_links", "emergency_contact_id")
