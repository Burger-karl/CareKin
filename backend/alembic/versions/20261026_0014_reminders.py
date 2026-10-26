"""Reminders for the WhatsApp care companion (Day 15)

Creates reminders (ERD section 2.10): hydration, medication, appointment and daily
check-in reminders that Celery Beat sends by WhatsApp or SMS. The schedule is a
local time plus days of the week, or a one-off date; app/agents/models.py explains
how this differs from the ERD. reminders.channel reuses the alert_channel enum type
created in 0003.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("hydration", "medication", "appointment", "check_in", name="reminder_kind"),
            nullable=False,
        ),
        sa.Column("local_time", sa.Time(), nullable=False),
        sa.Column(
            "days_of_week",
            postgresql.ARRAY(sa.SmallInteger()),
            server_default=sa.text("'{}'::smallint[]"),
            nullable=False,
        ),
        sa.Column("on_date", sa.Date(), nullable=True),
        sa.Column(
            "channel",
            postgresql.ENUM("sms", "whatsapp", "push", name="alert_channel", create_type=False),
            server_default="whatsapp",
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "on_date IS NOT NULL OR cardinality(days_of_week) BETWEEN 1 AND 7",
            name=op.f("ck_reminders_schedule"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_reminders_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_reminders_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminders")),
    )
    op.create_index(op.f("ix_reminders_created_by"), "reminders", ["created_by"], unique=False)
    op.create_index(op.f("ix_reminders_next_due_at"), "reminders", ["next_due_at"], unique=False)
    op.create_index(op.f("ix_reminders_patient_id"), "reminders", ["patient_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_reminders_patient_id"), table_name="reminders")
    op.drop_index(op.f("ix_reminders_next_due_at"), table_name="reminders")
    op.drop_index(op.f("ix_reminders_created_by"), table_name="reminders")
    op.drop_table("reminders")
    # alert_channel belongs to migration 0003 and is left alone.
    sa.Enum(name="reminder_kind").drop(op.get_bind(), checkfirst=True)
