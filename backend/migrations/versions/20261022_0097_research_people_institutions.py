"""Add research people and institutions with validated ORCID / ROR identifiers (X.18.3)."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261022_0097"
down_revision: Union[str, Sequence[str], None] = "20261022_0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_institutions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("ror_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_institutions_project_id", "research_institutions", ["project_id"])
    op.create_table(
        "research_people",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("orcid", sa.String(length=19), nullable=True),
        sa.Column("institution_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["institution_id"], ["research_institutions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_people_project_id", "research_people", ["project_id"])
    op.create_index("ix_research_people_institution_id", "research_people", ["institution_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM research_people")).scalar_one():
        raise RuntimeError("Refusing to drop research_people while rows exist")
    if bind.execute(sa.text("SELECT COUNT(*) FROM research_institutions")).scalar_one():
        raise RuntimeError("Refusing to drop research_institutions while rows exist")
    op.drop_index("ix_research_people_institution_id", table_name="research_people")
    op.drop_index("ix_research_people_project_id", table_name="research_people")
    op.drop_table("research_people")
    op.drop_index("ix_research_institutions_project_id", table_name="research_institutions")
    op.drop_table("research_institutions")
