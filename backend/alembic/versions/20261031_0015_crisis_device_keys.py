"""Crisis device keys: one-tap crisis without signing in

A device that a patient or guardian has set up keeps a key that can only start a
crisis for that patient. Only the key's SHA-256 is stored.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crisis_device_keys",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_crisis_device_keys_patient_id_patients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_crisis_device_keys_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crisis_device_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_crisis_device_keys_key_hash")),
    )
    op.create_index(
        op.f("ix_crisis_device_keys_patient_id"), "crisis_device_keys", ["patient_id"], unique=False
    )
    op.create_index(
        op.f("ix_crisis_device_keys_user_id"), "crisis_device_keys", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_crisis_device_keys_user_id"), table_name="crisis_device_keys")
    op.drop_index(op.f("ix_crisis_device_keys_patient_id"), table_name="crisis_device_keys")
    op.drop_table("crisis_device_keys")
