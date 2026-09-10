"""Create the initial research workspace schema.

Revision ID: 20260907_0001
Revises:
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260907_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    context_kind = sa.Enum("question", "objective", "hypothesis", "methodology", "variable", "decision", name="context_kind")
    claim_status = sa.Enum("supports", "contradicts", "qualifies", "unclear", name="claim_status")
    run_status = sa.Enum(
        "queued", "running", "completed", "needs_sources", "needs_configuration", "failed", name="research_run_status"
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "external_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "subject", name="uq_provider_subject"),
    )
    op.create_index("ix_external_identities_user_id", "external_identities", ["user_id"])
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_table(
        "research_context_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", context_kind, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_context_items_project_id", "research_context_items", ["project_id"])
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("url", sa.String(length=2000), nullable=True),
        sa.Column("authors", sa.JSON(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("metadata_verified", sa.Boolean(), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sources_project_id", "sources", ["project_id"])
    op.create_index("ix_sources_doi", "sources", ["doi"])
    op.create_table(
        "source_excerpts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("locator", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_excerpts_source_id", "source_excerpts", ["source_id"])
    op.create_index("ix_source_excerpts_content_hash", "source_excerpts", ["content_hash"])
    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", claim_status, nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claims_project_id", "claims", ["project_id"])
    op.create_table(
        "claim_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("relationship", claim_status, nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("locator", sa.String(length=255), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_id", "source_id", name="uq_claim_source"),
    )
    op.create_index("ix_claim_evidence_claim_id", "claim_evidence", ["claim_id"])
    op.create_index("ix_claim_evidence_source_id", "claim_evidence", ["source_id"])
    op.create_table(
        "research_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("research_plan", sa.JSON(), nullable=True),
        sa.Column("input_snapshot", sa.JSON(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("provider_model", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_runs_project_id", "research_runs", ["project_id"])
    op.create_index("ix_research_runs_status", "research_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_research_runs_status", table_name="research_runs")
    op.drop_index("ix_research_runs_project_id", table_name="research_runs")
    op.drop_table("research_runs")
    op.drop_index("ix_claim_evidence_source_id", table_name="claim_evidence")
    op.drop_index("ix_claim_evidence_claim_id", table_name="claim_evidence")
    op.drop_table("claim_evidence")
    op.drop_index("ix_claims_project_id", table_name="claims")
    op.drop_table("claims")
    op.drop_index("ix_source_excerpts_content_hash", table_name="source_excerpts")
    op.drop_index("ix_source_excerpts_source_id", table_name="source_excerpts")
    op.drop_table("source_excerpts")
    op.drop_index("ix_sources_doi", table_name="sources")
    op.drop_index("ix_sources_project_id", table_name="sources")
    op.drop_table("sources")
    op.drop_index("ix_research_context_items_project_id", table_name="research_context_items")
    op.drop_table("research_context_items")
    op.drop_index("ix_projects_owner_id", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_external_identities_user_id", table_name="external_identities")
    op.drop_table("external_identities")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    sa.Enum(name="research_run_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="claim_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="context_kind").drop(op.get_bind(), checkfirst=True)
