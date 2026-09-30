"""invoices — store/date and vendor/date indexes — additive

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-30

Two composite indexes on `invoices`; nothing else. No row, column or
constraint changes.

The receiving layer and the store and vendor views ask one question in
different forms: "this store's (or this vendor's) invoices over this date
range". invoices has single-column indexes on store_id and vendor_id but none
that serves a range on invoice_date within a store or a vendor. The existing
single-column indexes are left in place.

Revises 0028, the deployed head. The unreleased 0025 (identifier resolution
records) also descends from 0024 and is re-parented when it is released.
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("idx_invoices_store_date", "invoices", ["store_id", "invoice_date"])
    op.create_index("idx_invoices_vendor_date", "invoices", ["vendor_id", "invoice_date"])


def downgrade() -> None:
    op.drop_index("idx_invoices_vendor_date", table_name="invoices")
    op.drop_index("idx_invoices_store_date", table_name="invoices")
