"""PM Approvals work assignment: commercial_review_assignments — additive

An administrator hands out the Product Master review queue in order, so two reviewers never work the same
candidate. One row per assigned candidate (primary key = the candidate); no row = unassigned. Nothing existing
is changed: candidates, their review versions and history are untouched.

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

TABLE = "commercial_review_assignments"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("mapping_id", UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_username", sa.String(length=64), nullable=False),
        sa.Column("assigned_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_by", sa.String(length=64), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("mapping_id"),
        sa.ForeignKeyConstraint(["mapping_id"], ["master_commercial_mappings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.create_index("idx_commercial_review_assignments_user", TABLE, ["assigned_user_id"])


def downgrade() -> None:
    op.drop_index("idx_commercial_review_assignments_user", table_name=TABLE)
    op.drop_table(TABLE)
