"""Source embeddings and thematic clusters (M3.8.1).

Revision ID: 20261007_0032
Revises: 20261007_0031
Create Date: 2026-10-07

Additive: four new tables. Vectors are JSON lists so SQLite and Postgres both work (no pgvector yet).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261007_0032"
down_revision: Union[str, Sequence[str], None] = "20261007_0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_embeddings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("dims", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_id", "model", "text_hash", name="uq_source_embedding"),
    )
    op.create_index("ix_source_embeddings_project_id", "source_embeddings", ["project_id"])
    op.create_index("ix_source_embeddings_source_id", "source_embeddings", ["source_id"])

    op.create_table(
        "cluster_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("k", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("n_sources", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cluster_runs_project_id", "cluster_runs", ["project_id"])

    op.create_table(
        "clusters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("cluster_runs.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("label_edited_by", sa.String(length=100), nullable=True),
        sa.Column("label_edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "position", name="uq_cluster_position"),
    )
    op.create_index("ix_clusters_run_id", "clusters", ["run_id"])

    op.create_table(
        "cluster_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cluster_id", sa.Uuid(), sa.ForeignKey("clusters.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("distance", sa.Float(), nullable=False),
        sa.UniqueConstraint("cluster_id", "source_id", name="uq_cluster_member"),
    )
    op.create_index("ix_cluster_members_cluster_id", "cluster_members", ["cluster_id"])
    op.create_index("ix_cluster_members_source_id", "cluster_members", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_cluster_members_source_id", table_name="cluster_members")
    op.drop_index("ix_cluster_members_cluster_id", table_name="cluster_members")
    op.drop_table("cluster_members")
    op.drop_index("ix_clusters_run_id", table_name="clusters")
    op.drop_table("clusters")
    op.drop_index("ix_cluster_runs_project_id", table_name="cluster_runs")
    op.drop_table("cluster_runs")
    op.drop_index("ix_source_embeddings_source_id", table_name="source_embeddings")
    op.drop_index("ix_source_embeddings_project_id", table_name="source_embeddings")
    op.drop_table("source_embeddings")
