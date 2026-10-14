"""Store scholar-selected meta-analysis plans and computed results."""

from alembic import op
import sqlalchemy as sa

revision = "20261013_0050"
down_revision = "20261013_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meta_analysis_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("primary_model", sa.String(10), nullable=False),
        sa.Column("studies_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("result_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meta_analysis_plans_project_id", "meta_analysis_plans", ["project_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM meta_analysis_plans")).scalar_one():
        raise RuntimeError("Cannot drop nonempty meta-analysis plans")
    op.drop_index("ix_meta_analysis_plans_project_id", table_name="meta_analysis_plans")
    op.drop_table("meta_analysis_plans")
