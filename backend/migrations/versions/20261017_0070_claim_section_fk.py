"""Bind claims to manuscript sections: foreign key claims.section_id -> documents.id (M6.1, spec 5.11).

Revision ID: 20261017_0070
Revises: 20261017_0069
Create Date: 2026-10-16

`claims.section_id` existed since M3.11.1 without a constraint, and no code path wrote it until M6.1. The upgrade
refuses while any claim points at a missing document, so a foreign key is never added over dangling values.
Deleting a document sets the claim's section to NULL: the claim and its evidence are kept.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261017_0070"
down_revision: Union[str, Sequence[str], None] = "20261017_0069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_NAME = "fk_claims_section_id_documents"


def upgrade() -> None:
    bind = op.get_bind()
    dangling = bind.execute(
        sa.text("SELECT COUNT(*) FROM claims WHERE section_id IS NOT NULL AND section_id NOT IN (SELECT id FROM documents)")
    ).scalar_one()
    if dangling:
        raise RuntimeError(f"Refusing to add the claims.section_id foreign key: {dangling} claim(s) point at a missing section.")
    with op.batch_alter_table("claims") as batch:
        batch.create_foreign_key(FK_NAME, "documents", ["section_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_claims_section_id", "claims", ["section_id"])


def downgrade() -> None:
    op.drop_index("ix_claims_section_id", table_name="claims")
    with op.batch_alter_table("claims") as batch:
        batch.drop_constraint(FK_NAME, type_="foreignkey")
