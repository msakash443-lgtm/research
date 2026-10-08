"""Extractions: data pulled from one paper by one extraction schema (M3.2).

Revision ID: 20261007_0029
Revises: 20261006_0028
Create Date: 2026-10-07

Additive. A row can only be `verified_by_human` with a verifier and a time (check constraint); the API
in M3.2 never sets it, the verification step (M3.4/M3.6) will.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261007_0029"
down_revision: Union[str, Sequence[str], None] = "20261006_0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("fields_json", sa.JSON(), nullable=False),
        sa.Column("evidence_spans", sa.JSON(), nullable=False),
        sa.Column("verified_by_human", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verified_by", sa.String(length=100), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extracted_by", sa.String(length=10), nullable=False),
        sa.Column("extractor", sa.String(length=100), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "verified_by_human = false OR (verified_by IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_extraction_verified_has_verifier",
        ),
        sa.CheckConstraint("extracted_by IN ('human', 'ai')", name="ck_extraction_extracted_by"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_extractions_project_id", "extractions", ["project_id"])
    op.create_index("ix_extractions_source_id", "extractions", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_extractions_source_id", table_name="extractions")
    op.drop_index("ix_extractions_project_id", table_name="extractions")
    op.drop_table("extractions")
