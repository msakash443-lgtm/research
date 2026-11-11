"""Add provenance_reports.included_sections (X.31.24, owner answer W6, 2026-10-15): the report sections the
researcher chose to show when the report was generated ("produced", "ai", "pasted", "statement", "limits").

Revision ID: 20261020_0084
Revises: 20261019_0083
Create Date: 2026-10-20

Additive, backward-compatible: a NOT NULL column with a server default of every section (today's unfiltered
report), so every existing row reads the same way it always did. `app/provenance_report.py` always computes and
stores the full report regardless of selection (`app/section_approval.py` reads `report_json["ai"]`/`["shares"]`
unconditionally); this column only narrows what reads/exports show back.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261020_0084"
down_revision: Union[str, Sequence[str], None] = "20261019_0083"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALL_SECTIONS_JSON = '["produced", "ai", "pasted", "statement", "limits"]'


def upgrade() -> None:
    op.add_column(
        "provenance_reports",
        sa.Column("included_sections", sa.JSON(), nullable=False, server_default=ALL_SECTIONS_JSON),
    )


def downgrade() -> None:
    op.drop_column("provenance_reports", "included_sections")
