"""Add deposit and fuel surcharge columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-05

Prompt v3 extracts per-line container deposits and invoice-level
deposit/fuel-surcharge totals that the schema had nowhere to store. These
are needed by the reconciliation engine: layouts that fold the deposit
into the extended line total cannot be balanced without them.

`invoice_items.discount` already existed and was never populated, so no
column is added for it — persistence simply starts writing it.

All columns are nullable with no default: an invoice that does not print
a deposit or fuel surcharge stores NULL ("not on the document"), which is
deliberately distinct from 0.00 ("printed as zero").
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_items",
        sa.Column("deposit", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("deposit_total", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("fuel_surcharge", sa.Numeric(14, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoices", "fuel_surcharge")
    op.drop_column("invoices", "deposit_total")
    op.drop_column("invoice_items", "deposit")
