"""Add invoice_items.pack_size

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-10

The PDI detail record carries units-per-case in bytes [53:57]; live PDI
confirmed it drives the "Units Per Case" and "Case Retail" columns.
Prompt v3 already extracts pack_size as printed (e.g. "24/12OZ") but had
nowhere to store it, so the formatter could not populate that field.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice_items", sa.Column("pack_size", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("invoice_items", "pack_size")
