"""Add corrected_fields to invoice_items

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-11

Records which of a line item's transaction values a person typed, so a
manually entered figure never continues to read as OCR/LLM output.

Per-field rather than a single row-level flag: correcting a unit price
must not make the quantity beside it look hand-entered too. A JSONB array
of field names keeps that precision in one column, and an empty/NULL
array is the normal case — every row starts fully extracted.

Needed because Google Vision interleaves the description and price
columns in parts of a receipt, so a handful of values per invoice arrive
unassociated. The model correctly reports those as null rather than
guessing; this column is how a human's replacement stays visible as such.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_items",
        sa.Column(
            "corrected_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Transaction fields replaced by a person, e.g. ['unit_price'].",
        ),
    )


def downgrade() -> None:
    op.drop_column("invoice_items", "corrected_fields")
