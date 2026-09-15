"""invoice_items.line_type — product rows vs charge rows

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-16

A real invoice (T.J. Sheehan 101497) prints "MISCELLANEOUS DELIVERY
CHARGE 5.00" as a row of the item table with a placeholder UPC of
twelve zeros. Persisted as a product it would need a case mapping,
block the export, and — mapped — become a PDI product record. It is
kept as a line (the amount is real and reconciles the grand total) but
typed as a charge, which the PDI selection, the mapping gate and the
audit all skip. Existing rows are products.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_items",
        sa.Column("line_type", sa.String(16), nullable=False, server_default="product"),
    )


def downgrade() -> None:
    op.drop_column("invoice_items", "line_type")
