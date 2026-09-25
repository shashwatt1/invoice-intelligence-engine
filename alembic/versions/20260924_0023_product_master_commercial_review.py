"""product master commercial review — cost basis, review columns, decision log

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-24

Additive only. Three columns are added to `master_commercial_mappings` and
one table is created; no existing table is altered, and nothing outside the
Product Master group is touched. `product_case_mappings` remains the sole
source EDI reads, and approving a row created here changes no export.

`cost_basis` becomes a column rather than staying an evidence key because
cost is reviewed independently of the commercial unit. A mapping can be
approved on solid units-per-case evidence while its cost remains
unresolved, and that is only expressible if the two statuses are separate
fields rather than one approval_state standing for both.

`master_commercial_reviews` is append-only. It exists instead of reusing
`product_data_proposals` because that table governs the legacy
case-mapping workflow, is keyed for it, and is authoritative for EDI —
writing this workflow's history into it would blur those semantics and
would mean modifying a legacy table this phase must leave alone. It records
only the transitions this one workflow makes, not a general audit
framework.

Down-migration drops the review table and the three added columns, leaving
0022's shape exactly as it was.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "master_commercial_mappings",
        sa.Column("cost_basis", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "master_commercial_mappings",
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "master_commercial_mappings",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "master_commercial_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("mapping_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("previous_approval_state", sa.String(length=32), nullable=False),
        sa.Column("new_approval_state", sa.String(length=32), nullable=False),
        sa.Column("previous_commercial_unit_basis", sa.String(length=32), nullable=False),
        sa.Column("new_commercial_unit_basis", sa.String(length=32), nullable=False),
        sa.Column("previous_units_accounted_for", sa.Integer(), nullable=True),
        sa.Column("new_units_accounted_for", sa.Integer(), nullable=True),
        sa.Column("reviewer", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("evidence_considered", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["mapping_id"], ["master_commercial_mappings.id"],
                                ondelete="CASCADE"),
    )
    op.create_index("idx_master_commercial_review_mapping", "master_commercial_reviews",
                    ["mapping_id"])
    op.create_index("idx_master_commercial_review_created", "master_commercial_reviews",
                    ["created_at"])


def downgrade() -> None:
    op.drop_table("master_commercial_reviews")
    op.drop_column("master_commercial_mappings", "reviewed_at")
    op.drop_column("master_commercial_mappings", "reviewed_by")
    op.drop_column("master_commercial_mappings", "cost_basis")
