"""shared commercial mappings and vendor item crosswalk — additive

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-30

No existing row is changed; every existing value means what it meant.

  * invoice_items.supplier_item_id — the vendor's own item number, as
    extraction read it. Until now it survived only inside the raw model
    response. Nullable; backfilled (optionally) by
    scripts/backfill_supplier_item_ids.py, never here.
  * product_data_proposals.vendor_id — whose item number a vendor_item
    proposal is about. Nullable; NULL for every existing (case_mapping) row.
  * master_commercial_mappings.store_id becomes nullable: NULL is the shared
    scope, one units-per-case for every physical store. At most one APPROVED
    shared value per item code (partial unique index).
  * master_commercial_mappings.override_reason — set only on a physical
    store's deliberate exception to the shared value.
  * vendor_item_crosswalk — confirmed (vendor, item number) -> PDI item code
    links, each from an approved proposal. Row level security, no policies.

The downgrade refuses while any shared mapping, override, crosswalk link or
vendor_item proposal exists: dropping them would silently discard reviewed
decisions. supplier_item_id is recoverable from the raw responses and is
dropped.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
CROSSWALK = "vendor_item_crosswalk"


def upgrade() -> None:
    op.add_column("invoice_items", sa.Column("supplier_item_id", sa.String(length=64), nullable=True))

    op.add_column("product_data_proposals", sa.Column("vendor_id", UUID, nullable=True))
    op.create_foreign_key("fk_product_data_proposals_vendor", "product_data_proposals", "vendors",
                          ["vendor_id"], ["id"], ondelete="RESTRICT")
    op.create_index("idx_product_data_proposals_vendor", "product_data_proposals", ["vendor_id"])

    op.alter_column("master_commercial_mappings", "store_id", existing_type=UUID, nullable=True)
    op.add_column("master_commercial_mappings", sa.Column("override_reason", sa.String(length=500), nullable=True))
    op.create_check_constraint("ck_master_commercial_override_store_scoped", "master_commercial_mappings",
                               "override_reason IS NULL OR store_id IS NOT NULL")
    op.create_index("uq_master_commercial_shared_approved", "master_commercial_mappings", ["pdi_item_code"],
                    unique=True, postgresql_where=sa.text("store_id IS NULL AND approval_state = 'APPROVED'"))

    op.create_table(
        CROSSWALK,
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("vendor_id", UUID, nullable=False),
        sa.Column("vendor_item_code", sa.String(length=64), nullable=False),
        sa.Column("vendor_item_code_raw", sa.String(length=128), nullable=False),
        sa.Column("item_code", sa.String(length=32), nullable=False),
        sa.Column("upc", sa.String(length=14), nullable=True),
        sa.Column("product_id", UUID, nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("proposal_id", UUID, nullable=True),
        sa.Column("confirmed_by", sa.String(length=128), nullable=False),
        sa.Column("confirmed_by_role", sa.String(length=16), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_by", sa.String(length=128), nullable=True),
        sa.Column("retire_reason", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["master_products.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["proposal_id"], ["product_data_proposals.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("state IN ('CONFIRMED', 'RETIRED')", name="ck_vendor_item_crosswalk_state"),
        sa.CheckConstraint(
            "(state = 'CONFIRMED' AND retired_at IS NULL AND retired_by IS NULL AND retire_reason IS NULL) OR "
            "(state = 'RETIRED' AND retired_at IS NOT NULL AND retired_by IS NOT NULL AND retire_reason IS NOT NULL)",
            name="ck_vendor_item_crosswalk_retired"),
        sa.CheckConstraint("length(item_code) = 11", name="ck_vendor_item_crosswalk_item_code"),
    )
    op.execute(f"ALTER TABLE {CROSSWALK} ENABLE ROW LEVEL SECURITY")
    op.create_index("uq_vendor_item_crosswalk_confirmed", CROSSWALK, ["vendor_id", "vendor_item_code"],
                    unique=True, postgresql_where=sa.text("state = 'CONFIRMED'"))
    op.create_index("idx_vendor_item_crosswalk_item_code", CROSSWALK, ["item_code"])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM {CROSSWALK}) "
        "OR EXISTS (SELECT 1 FROM master_commercial_mappings WHERE store_id IS NULL OR override_reason IS NOT NULL) "
        "OR EXISTS (SELECT 1 FROM product_data_proposals WHERE vendor_id IS NOT NULL) THEN "
        "RAISE EXCEPTION 'shared mappings, overrides or vendor item links exist; downgrading 0038 would discard them'; "
        "END IF; END $$"
    )
    op.drop_index("idx_vendor_item_crosswalk_item_code", table_name=CROSSWALK)
    op.drop_index("uq_vendor_item_crosswalk_confirmed", table_name=CROSSWALK)
    op.drop_table(CROSSWALK)
    op.drop_index("uq_master_commercial_shared_approved", table_name="master_commercial_mappings")
    op.drop_constraint("ck_master_commercial_override_store_scoped", "master_commercial_mappings", type_="check")
    op.drop_column("master_commercial_mappings", "override_reason")
    op.alter_column("master_commercial_mappings", "store_id", existing_type=UUID, nullable=False)
    op.drop_index("idx_product_data_proposals_vendor", table_name="product_data_proposals")
    op.drop_constraint("fk_product_data_proposals_vendor", "product_data_proposals", type_="foreignkey")
    op.drop_column("product_data_proposals", "vendor_id")
    op.drop_column("invoice_items", "supplier_item_id")
