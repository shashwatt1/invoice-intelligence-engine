"""store inventory baselines — additive

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-29

One new table; no existing table or row is touched, and nothing is
initialized here. Baselines are recorded only by an administrator running
scripts/initialize_inventory_baselines.py (dry run by default).

A row is a store's inventory start: products at that store with no
product-level opening count start at zero on baseline_date — the arrival date
of the store's earliest posted receipt, or the initialization date when it had
none. Product-level opening counts are untouched and always take precedence.

At most one row per store (unique store_id). Rows are never edited or deleted
(ORM guards in app.models.inventory); the foreign keys to stores and
inventory_receipts RESTRICT, so the evidence cannot disappear under a row.

Row level security with no policies and without FORCE, like the other
inventory tables: Supabase's Data API roles see nothing; the backend,
connecting as the table owner, is unaffected.

The downgrade refuses while any baseline exists: dropping one would silently
change every balance that rests on it.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

TABLE = "store_inventory_baselines"
BASES = ("EARLIEST_POSTED_RECEIPT", "INITIALIZATION_DATE")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("baseline_date", sa.Date(), nullable=False),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column("earliest_receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("initialization_date", sa.Date(), nullable=False),
        sa.Column("initialized_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("initialized_by", sa.String(length=64), nullable=False),
        sa.Column("initialized_by_role", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["earliest_receipt_id"], ["inventory_receipts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["initialized_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("store_id", name="uq_store_inventory_baselines_store"),
        sa.CheckConstraint("basis IN (" + ", ".join(f"'{b}'" for b in sorted(BASES)) + ")",
                           name="ck_store_inventory_baselines_basis"),
        sa.CheckConstraint("(basis = 'EARLIEST_POSTED_RECEIPT') = (earliest_receipt_id IS NOT NULL)",
                           name="ck_store_inventory_baselines_evidence"),
        sa.CheckConstraint("baseline_date <= initialization_date", name="ck_store_inventory_baselines_dates"),
        sa.CheckConstraint("length(trim(reason)) > 0", name="ck_store_inventory_baselines_reason"),
    )
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM {TABLE}) THEN "
        f"RAISE EXCEPTION '{TABLE} holds store inventory baselines; downgrading 0036 would discard them'; "
        "END IF; END $$"
    )
    op.drop_table(TABLE)
