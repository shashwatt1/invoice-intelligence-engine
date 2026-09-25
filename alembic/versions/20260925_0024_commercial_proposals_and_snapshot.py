"""commercial proposals + source evidence snapshot — additive

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-25

Additive only; five columns on `master_commercial_mappings`. No existing
table is altered and nothing outside the Product Master is touched.

`proposed_*` lets a USER record a suggested multiplier without being able
to write an authoritative one: the proposal sits beside the candidate
until a MANAGER or ADMIN approves or rejects it through the same
transactional review service that already governs approval. A proposal is
not a mapping.

`source_snapshot` exists because the reference workbooks under
data/reference/ are gitignored source evidence and are not present in the
deployed container. Without a persisted snapshot the live dashboard could
not show a reviewer the actual values a proposal rests on. Fields the
source never carried are left absent rather than invented.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("master_commercial_mappings",
                  sa.Column("proposed_units_accounted_for", sa.Integer(), nullable=True))
    op.add_column("master_commercial_mappings",
                  sa.Column("proposed_by", sa.String(length=64), nullable=True))
    op.add_column("master_commercial_mappings",
                  sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("master_commercial_mappings",
                  sa.Column("proposed_note", sa.String(length=1000), nullable=True))
    op.add_column(
        "master_commercial_mappings",
        sa.Column("source_snapshot", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("master_commercial_mappings", "source_snapshot")
    op.drop_column("master_commercial_mappings", "proposed_note")
    op.drop_column("master_commercial_mappings", "proposed_at")
    op.drop_column("master_commercial_mappings", "proposed_by")
    op.drop_column("master_commercial_mappings", "proposed_units_accounted_for")
