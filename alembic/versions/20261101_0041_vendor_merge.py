"""vendor merge: a duplicate vendor is kept as a tombstone pointing at the vendor it was merged into — additive

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-01

Additive; no existing row is changed or rewritten.

  * vendors.merged_into_id (nullable, self-reference): set on the duplicate that was merged away, never on the vendor that
    stays. The duplicate row is kept, not deleted: its identity-review history is an audit record that must keep pointing at
    it, and a later invoice that prints its old spelling must still resolve to the vendor that stays instead of creating the
    duplicate again.
  * vendor_merges (new, empty): who merged which vendor into which, why, the plan fingerprint the merge was applied
    from and what was repointed. One row per merged-away vendor. Row level security on, like the other ledgers.

Downgrade drops both. A downgrade after a merge leaves the repointed invoices on the vendor that stayed.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vendors", sa.Column("merged_into_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_vendors_merged_into", "vendors", "vendors", ["merged_into_id"], ["id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_vendors_not_merged_into_itself", "vendors",
                               "merged_into_id IS NULL OR merged_into_id <> id")
    op.create_index("idx_vendors_merged_into", "vendors", ["merged_into_id"])

    op.create_table(
        "vendor_merges",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("loser_vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("winner_vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merged_by", sa.String(length=64), nullable=False),
        sa.Column("merged_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["loser_vendor_id"], ["vendors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["winner_vendor_id"], ["vendors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["merged_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("loser_vendor_id <> winner_vendor_id", name="ck_vendor_merges_distinct"),
        sa.UniqueConstraint("loser_vendor_id", name="uq_vendor_merges_loser"),
    )
    op.execute("ALTER TABLE vendor_merges ENABLE ROW LEVEL SECURITY")
    op.create_index("idx_vendor_merges_winner", "vendor_merges", ["winner_vendor_id"])


def downgrade() -> None:
    op.drop_table("vendor_merges")
    op.drop_index("idx_vendors_merged_into", table_name="vendors")
    op.drop_constraint("ck_vendors_not_merged_into_itself", "vendors", type_="check")
    op.drop_constraint("fk_vendors_merged_into", "vendors", type_="foreignkey")
    op.drop_column("vendors", "merged_into_id")
