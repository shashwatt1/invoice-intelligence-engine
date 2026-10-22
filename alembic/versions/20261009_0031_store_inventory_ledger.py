"""store inventory — receipts and the append-only movement ledger — additive

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-09

Two new tables; no existing table or row is touched.

inventory_receipts records a person's confirmation that one invoice's goods
were received (at most one POSTED per invoice; a reversed receipt stays as
history and the invoice may be received again). inventory_movements is the ledger:
one signed quantity, in sellable units, of one product (11-digit PDI item
code) at one physical store, with its source, the conversion it used, a
unique idempotency key and the actor. Balances are sums of movements; nothing
stores an editable stock figure. Used only while INVENTORY_ENABLED is on.

Both tables get row level security with no policies and without FORCE, like
edi_exports: Supabase's Data API roles see nothing; the backend, connecting as
the table owner, is unaffected.

Revises 0030. The unreleased 0025 (identifier resolution records) descends
from 0024 and is re-parented when it is released.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

INBOUND = "'OPENING_COUNT', 'RECEIPT', 'SALE_RETURN', 'TRANSFER_IN'"
OUTBOUND = "'RETURN_TO_VENDOR', 'SALE', 'TRANSFER_OUT'"
TYPES = ("'ADJUSTMENT', 'OPENING_COUNT', 'RECEIPT', 'RETURN_TO_VENDOR', 'SALE', 'SALE_RETURN', "
         "'TRANSFER_IN', 'TRANSFER_OUT'")


def upgrade() -> None:
    op.create_table(
        "inventory_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invoice_number", sa.String(length=64), nullable=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("confirmed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_by", sa.String(length=64), nullable=False),
        sa.Column("confirmed_role", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("discrepancy_count", sa.Integer(), nullable=False),
        sa.Column("resolution_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reversed_by", sa.String(length=64), nullable=True),
        sa.Column("reversal_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reversed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('POSTED', 'REVERSED')", name="ck_inventory_receipts_status"),
        sa.CheckConstraint(
            "status <> 'REVERSED' OR "
            "(reversed_at IS NOT NULL AND reversed_by IS NOT NULL AND reversal_reason IS NOT NULL)",
            name="ck_inventory_receipts_reversal",
        ),
    )
    op.execute("ALTER TABLE inventory_receipts ENABLE ROW LEVEL SECURITY")
    # One POSTED receipt per invoice; reversed receipts stay as history.
    op.create_index("uq_inventory_receipts_invoice_posted", "inventory_receipts", ["invoice_id"], unique=True,
                    postgresql_where=sa.text("status = 'POSTED'"))
    op.create_index("idx_inventory_receipts_invoice", "inventory_receipts", ["invoice_id"])
    op.create_index("idx_inventory_receipts_store", "inventory_receipts", ["store_id", "received_on"])

    op.create_table(
        "inventory_movements",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_code", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("movement_type", sa.String(length=24), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("base_unit", sa.String(length=16), nullable=False),
        sa.Column("expected_quantity", sa.Numeric(14, 4), nullable=True),
        sa.Column("source_quantity", sa.Numeric(14, 4), nullable=True),
        sa.Column("source_unit", sa.String(length=16), nullable=True),
        sa.Column("units_per_case", sa.Integer(), nullable=True),
        sa.Column("conversion_path", sa.String(length=40), nullable=True),
        sa.Column("conversion_mapping_id", sa.String(length=64), nullable=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invoice_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reversal_of_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invoice_item_id"], ["invoice_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["receipt_id"], ["inventory_receipts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reversal_of_id"], ["inventory_movements.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_inventory_movements_idempotency"),
        sa.CheckConstraint(f"movement_type IN ({TYPES})", name="ck_inventory_movements_type"),
        sa.CheckConstraint(
            f"(movement_type NOT IN ({INBOUND}) OR quantity >= 0 OR reversal_of_id IS NOT NULL) AND "
            f"(movement_type NOT IN ({OUTBOUND}) OR quantity <= 0 OR reversal_of_id IS NOT NULL)",
            name="ck_inventory_movements_sign",
        ),
    )
    op.execute("ALTER TABLE inventory_movements ENABLE ROW LEVEL SECURITY")
    op.create_index("idx_inventory_movements_store_item", "inventory_movements",
                    ["store_id", "item_code", "business_date"])
    op.create_index("idx_inventory_movements_invoice", "inventory_movements", ["invoice_id"])


def downgrade() -> None:
    # Dropping the tables also removes their row level security settings.
    op.drop_table("inventory_movements")
    op.drop_table("inventory_receipts")
