"""Allow NULL unit_price / line_total on invoice_items

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-26

Extraction can legitimately fail to read a price. Until now those
columns were NOT NULL, so the repository coerced a null to Decimal("0")
before saving — turning an honest "I could not read this" into an
apparently confident $0.00.

That coercion was actively dangerous:
  - a 0.00 price satisfies quantity x unit_price = line_total trivially,
    so no validation rule ever flagged it;
  - the PDI formatter would encode it as a 000000 case cost, silently
    telling PDI the goods were free.

Observed on Rocco J. Testani invoice 228245: the model returned null at
confidence 0.5 for LAB 30 PACK CANS and LAB LIGHT 30 PACK CA (both
printed at $22.70) and both were persisted as $0.00.

Making the columns nullable preserves the distinction between "unknown"
and a genuine zero. PDI export is blocked while any line's cost is
unknown, so nothing downstream has to guess.

Widening NOT NULL to NULL is not destructive and needs no data
migration; the downgrade backfills zeros only because the old schema
cannot represent the missing values.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("invoice_items", "unit_price",
                    existing_type=sa.Numeric(14, 4), nullable=True)
    op.alter_column("invoice_items", "line_total",
                    existing_type=sa.Numeric(14, 2), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE invoice_items SET unit_price = 0 WHERE unit_price IS NULL")
    op.execute("UPDATE invoice_items SET line_total = 0 WHERE line_total IS NULL")
    op.alter_column("invoice_items", "unit_price",
                    existing_type=sa.Numeric(14, 4), nullable=False)
    op.alter_column("invoice_items", "line_total",
                    existing_type=sa.Numeric(14, 2), nullable=False)
