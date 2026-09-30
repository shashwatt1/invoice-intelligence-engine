"""vendor master — identity status, canonical name, confirmation history — additive

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-30

Evolves the existing `vendors` table in place; no vendor row is created,
deleted, merged or re-keyed, and no invoice is touched.

`vendors` gains:
  * identity_status — 'unresolved' until a person confirms the vendor's
    identity. Every existing row becomes 'unresolved' through the column
    default: nothing before this revision could confirm a vendor, so there is
    no confirmation to preserve and none is invented.
  * display_name — the canonical vendor name a person confirmed. `name` stays
    exactly as it is: the name first observed on an invoice, which the
    extraction pipeline matches on and which exports print.

`vendor_identity_reviews` is append-only: one row per confirmation or
reopening, recording who (username, account id, role — from the
authenticated session), what (status and canonical name before and after),
when, why (the decision basis, required) and the evidence considered.

Revises 0026, the deployed head. The unreleased 0025 (identifier resolution
records) also descends from 0024 and is re-parented when it is released.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vendors", sa.Column("display_name", sa.String(length=255), nullable=True))
    op.add_column("vendors", sa.Column("identity_status", sa.String(length=16),
                                       server_default=sa.text("'unresolved'"), nullable=False))
    op.create_check_constraint("ck_vendors_identity_status", "vendors",
                               "identity_status IN ('unresolved', 'confirmed')")
    op.create_index("idx_vendors_identity_status", "vendors", ["identity_status"])

    op.create_table(
        "vendor_identity_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("previous_status", sa.String(length=16), nullable=False),
        sa.Column("new_status", sa.String(length=16), nullable=False),
        sa.Column("previous_display_name", sa.String(length=255), nullable=True),
        sa.Column("new_display_name", sa.String(length=255), nullable=True),
        sa.Column("reviewer", sa.String(length=64), nullable=False),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewer_role", sa.String(length=16), nullable=True),
        sa.Column("basis", sa.String(length=1000), nullable=False),
        sa.Column("evidence_considered", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # No cascade: a vendor with a governance history cannot be deleted out from under it.
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"]),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("decision IN ('CONFIRM', 'REOPEN')", name="ck_vendor_identity_reviews_decision"),
    )
    op.create_index("idx_vendor_identity_reviews_vendor", "vendor_identity_reviews", ["vendor_id"])
    op.create_index("idx_vendor_identity_reviews_created", "vendor_identity_reviews", ["created_at"])


def downgrade() -> None:
    op.drop_table("vendor_identity_reviews")
    op.drop_index("idx_vendors_identity_status", table_name="vendors")
    op.drop_constraint("ck_vendors_identity_status", "vendors", type_="check")
    op.drop_column("vendors", "identity_status")
    op.drop_column("vendors", "display_name")
