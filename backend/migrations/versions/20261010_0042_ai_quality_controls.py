"""AI quality controls for screening (M2.5, spec 5.3 human gate / quality checks).

Revision ID: 20261010_0042
Revises: 20261010_0041
Create Date: 2026-10-10

Additive and off by default (rule 18): `projects.ai_autoexclude_enabled` and
`ai_prescreen_disabled` both default false, so existing projects' screening behaviour is unchanged
until an owner turns auto-exclude on, or the agreement guard trips. `auto_exclude_audits` is a new
table holding the human sampling audit for AI excludes that would otherwise count without a person
looking at them individually.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261010_0042"
down_revision: Union[str, Sequence[str], None] = "20261010_0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("ai_autoexclude_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("projects", sa.Column("ai_autoexclude_confidence_threshold", sa.Float(), nullable=True))
    op.add_column("projects", sa.Column("ai_prescreen_disabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("projects", sa.Column("ai_prescreen_disabled_reason", sa.Text(), nullable=True))
    op.create_table(
        "auto_exclude_audits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status in ('pending','confirmed','overturned')", name="ck_auto_exclude_audit_status"),
        sa.CheckConstraint(
            "(status = 'pending' and reviewed_by is null and reviewed_at is null) or "
            "(status != 'pending' and reviewed_by is not null and reviewed_at is not null)",
            name="ck_auto_exclude_audit_review_fields",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.ForeignKeyConstraint(["decision_id"], ["screening_decisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", name="uq_auto_exclude_audit_decision"),
    )
    op.create_index("ix_auto_exclude_audits_project_id", "auto_exclude_audits", ["project_id"])
    op.create_index("ix_auto_exclude_audits_source_id", "auto_exclude_audits", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_auto_exclude_audits_source_id", table_name="auto_exclude_audits")
    op.drop_index("ix_auto_exclude_audits_project_id", table_name="auto_exclude_audits")
    op.drop_table("auto_exclude_audits")
    op.drop_column("projects", "ai_prescreen_disabled_reason")
    op.drop_column("projects", "ai_prescreen_disabled")
    op.drop_column("projects", "ai_autoexclude_confidence_threshold")
    op.drop_column("projects", "ai_autoexclude_enabled")
