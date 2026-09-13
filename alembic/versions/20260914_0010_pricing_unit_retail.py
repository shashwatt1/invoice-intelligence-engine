"""Add unit_retail to product_pricing

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-14

The store's Item Sales Summary exports carry Avg Price — what the
scanned unit actually sold for. That is direct evidence of what the
store's sellable unit IS, which is the question units-per-case asks:
a UPC that scans at $25.72 against a $22.70 case is sold as the case.

product_pricing had no home for a retail figure. This adds one so an
Item Sales export can be imported as a dated, source-aware pricing row
instead of overwriting the single-row-per-product
store_product_references table.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_pricing",
        sa.Column("unit_retail", sa.Numeric(14, 4), nullable=True,
                  comment="What the scanned unit sold for on average over the report period."),
    )


def downgrade() -> None:
    op.drop_column("product_pricing", "unit_retail")
