"""invoices.store_id nullable — an invoice can be read before its store is known

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-18

Collecting a store's reference data takes longer than photographing its
invoices. An invoice must be readable, stored and correctable before the
store is settled, without a fake store standing in. NULL store_id is that
state — STORE_PENDING — and every store-scoped step (reference matching,
case mappings, proposals, EDI) refuses until a person assigns the store.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("invoices", "store_id", existing_type=sa.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    # Refuses if any invoice is still store-pending; assign them first.
    op.alter_column("invoices", "store_id", existing_type=sa.UUID(as_uuid=True), nullable=False)
