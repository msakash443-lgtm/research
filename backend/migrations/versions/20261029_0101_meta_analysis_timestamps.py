"""Add missing server timestamps to meta_analysis_plans rows (search_queries: 20261029_0099).

`search_queries` (20261005_0021) and `meta_analysis_plans` (M5.1-era) declared `created_at`/
`updated_at` as `NOT NULL` but never gave Postgres a `DEFAULT now()` for them — the ORM model
(`Timestamped`, `app/models.py`) only sets `server_default`, so nothing client-side supplies a
value. Reproduced live 2026-10-29 (X.160 walkthrough): inserting a `SearchQuery` row on a
migrated Postgres database raised `NotNullViolation: null value in column "created_at"`,
discarding an otherwise-successful search. Uses `batch_alter_table` (unlike
`20261027_0098_criteria_seed_timestamps`, X.164) so it runs on SQLite too and does not add to
that scratch-DB verification gap.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261029_0101"
down_revision: Union[str, Sequence[str], None] = "20261029_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("meta_analysis_plans",):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("created_at", server_default=sa.func.now(), existing_type=sa.DateTime(timezone=True), existing_nullable=False)
            batch.alter_column("updated_at", server_default=sa.func.now(), existing_type=sa.DateTime(timezone=True), existing_nullable=False)


def downgrade() -> None:
    for table in ("meta_analysis_plans",):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("updated_at", server_default=None, existing_type=sa.DateTime(timezone=True), existing_nullable=False)
            batch.alter_column("created_at", server_default=None, existing_type=sa.DateTime(timezone=True), existing_nullable=False)
