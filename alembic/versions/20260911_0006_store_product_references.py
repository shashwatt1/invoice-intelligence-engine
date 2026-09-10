"""Add store_product_references

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-11

Per-store product facts imported from the store's own Item Sales Summary
export: identity, the store's per-unit cost, and the retail it achieves.

A reference layer only. It writes no EDI byte and overrides no invoice
value. product_case_mappings remains the sole authority for the
units-per-case that reaches the EDI, which is why this table has no
units_per_case column — a reference-derived value is a suggestion
computed at review time, never a stored fact.

avg_cost is nullable and must never be written as 0: two thirds of this
store's catalogue has no cost on file, and a stored zero would be
indistinguishable from a free product (the defect migration 0005 removed
from invoice_items).

Unique on (store_number, item_code) so re-importing an export updates in
place rather than duplicating, and so a second store can be added
without a migration.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "store_product_references",
        sa.Column("id", sa.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"),
                  nullable=False),
        sa.Column("store_number", sa.String(32), nullable=False),
        sa.Column("item_code", sa.String(32), nullable=False),
        sa.Column("scan_code_raw", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("avg_cost", sa.Numeric(14, 4), nullable=True),
        sa.Column("avg_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("source_file", sa.String(255), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_number", "item_code",
                            name="uq_store_product_reference_store_item"),
    )
    op.create_index("idx_store_product_references_item_code",
                    "store_product_references", ["item_code"])
    op.create_index("idx_store_product_references_store",
                    "store_product_references", ["store_number"])


def downgrade() -> None:
    op.drop_index("idx_store_product_references_store", table_name="store_product_references")
    op.drop_index("idx_store_product_references_item_code", table_name="store_product_references")
    op.drop_table("store_product_references")
