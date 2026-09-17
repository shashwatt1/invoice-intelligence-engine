"""manual corrections with history — who changed what, from what, when, why

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-18

Extraction is not perfect and photos miss rows. A person can now add a
line, void a line, change a line's values and correct the printed totals
(grand total included), after which validation runs again on the
corrected invoice. None of it is silent: every change is appended to a
per-row history (old value, new value, who, when, why), a line a person
typed carries entry_source 'manual', and the invoice lists the header
fields a person replaced. The original extracted values stay in
raw_extraction_json and in the history entries.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice_items", sa.Column("entry_source", sa.String(16), nullable=False,
                                             server_default="extracted"))
    op.add_column("invoice_items", sa.Column("correction_history",
                                             postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("invoices", sa.Column("corrected_fields",
                                        postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("invoices", sa.Column("correction_history",
                                        postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "correction_history")
    op.drop_column("invoices", "corrected_fields")
    op.drop_column("invoice_items", "correction_history")
    op.drop_column("invoice_items", "entry_source")
