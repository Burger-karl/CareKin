"""Patients tables: patients, guardians, emergency_contacts, care_profiles, family_links

ERD section 2.2 (ON-3, HP-4). Health columns are bytea: they hold Fernet ciphertext.

care_profiles.primary_facility_id has no foreign key yet: the migration that
creates `facilities` should add fk_care_profiles_primary_facility_id_facilities
(ON DELETE SET NULL).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patients",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("sex", sa.String(length=20), nullable=True),
        sa.Column("genotype", sa.LargeBinary(), nullable=True),
        sa.Column("blood_group", sa.LargeBinary(), nullable=True),
        sa.Column(
            "home_location",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=True,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_patients_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_patients_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patients")),
        sa.UniqueConstraint("user_id", name=op.f("uq_patients_user_id")),
    )
    op.create_index(op.f("ix_patients_created_by"), "patients", ["created_by"], unique=False)
    op.create_index(
        "idx_patients_home_location",
        "patients",
        ["home_location"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_table(
        "care_profiles",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("known_triggers", sa.LargeBinary(), nullable=True),
        sa.Column("medications_overview", sa.LargeBinary(), nullable=True),
        sa.Column("allergies", sa.LargeBinary(), nullable=True),
        sa.Column("care_instructions", sa.LargeBinary(), nullable=True),
        sa.Column("primary_facility_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_care_profiles_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_care_profiles_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_care_profiles")),
        sa.UniqueConstraint("patient_id", name=op.f("uq_care_profiles_patient_id")),
    )
    op.create_index(
        op.f("ix_care_profiles_primary_facility_id"),
        "care_profiles",
        ["primary_facility_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_care_profiles_updated_by"), "care_profiles", ["updated_by"], unique=False
    )
    op.create_table(
        "emergency_contacts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("relationship", sa.String(length=50), nullable=False),
        sa.Column(
            "channel",
            sa.Enum("sms", "whatsapp", "push", name="alert_channel"),
            server_default="sms",
            nullable=False,
        ),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_emergency_contacts_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_emergency_contacts")),
    )
    op.create_index(
        op.f("ix_emergency_contacts_patient_id"), "emergency_contacts", ["patient_id"], unique=False
    )
    op.create_table(
        "family_links",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "share_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("can_fund", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_family_links_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_family_links_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_family_links")),
        sa.UniqueConstraint(
            "patient_id", "user_id", name=op.f("uq_family_links_patient_id_user_id")
        ),
    )
    op.create_index(
        op.f("ix_family_links_patient_id"), "family_links", ["patient_id"], unique=False
    )
    op.create_index(op.f("ix_family_links_user_id"), "family_links", ["user_id"], unique=False)
    op.create_table(
        "guardians",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("relationship", sa.String(length=50), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "can_approve_caregivers", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "receives_crisis_alerts", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_guardians_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_guardians_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guardians")),
        sa.UniqueConstraint("patient_id", "user_id", name=op.f("uq_guardians_patient_id_user_id")),
    )
    op.create_index(op.f("ix_guardians_patient_id"), "guardians", ["patient_id"], unique=False)
    op.create_index(op.f("ix_guardians_user_id"), "guardians", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_guardians_user_id"), table_name="guardians")
    op.drop_index(op.f("ix_guardians_patient_id"), table_name="guardians")
    op.drop_table("guardians")
    op.drop_index(op.f("ix_family_links_user_id"), table_name="family_links")
    op.drop_index(op.f("ix_family_links_patient_id"), table_name="family_links")
    op.drop_table("family_links")
    op.drop_index(op.f("ix_emergency_contacts_patient_id"), table_name="emergency_contacts")
    op.drop_table("emergency_contacts")
    op.drop_index(op.f("ix_care_profiles_updated_by"), table_name="care_profiles")
    op.drop_index(op.f("ix_care_profiles_primary_facility_id"), table_name="care_profiles")
    op.drop_table("care_profiles")
    op.drop_index("idx_patients_home_location", table_name="patients", postgresql_using="gist")
    op.drop_index(op.f("ix_patients_created_by"), table_name="patients")
    op.drop_table("patients")
    # Autogenerate does not drop Postgres enum types; without this a re-upgrade fails.
    sa.Enum(name="alert_channel").drop(op.get_bind(), checkfirst=True)
