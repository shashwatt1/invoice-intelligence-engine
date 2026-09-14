"""Store-scope the authoritative case mappings; put the store on the invoice

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-15

Two things stopped being true once a second store's reference data
arrived:

1. product_case_mappings was keyed by item_code alone, so a units-per-
   case value approved on one store's evidence would have applied to
   every store carrying that UPC — with no proposal and no reviewer.
   The key becomes (store_number, item_code).

2. Invoices did not know which store they belonged to; the pipeline
   read one global setting. Every reference lookup — pricing, identity,
   catalogue, mappings, proposals — needs the invoice's own store.

Backfill is deterministic and evidence-based: a mapping takes the store
of the proposal whose approval created it (every existing row has one);
anything without a proposal, and every existing invoice, is assigned
the store the system has been operating for, 47708760. Nothing is
copied to any other store.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

ORIGINAL_STORE = "47708760"


def upgrade() -> None:
    # --- product_case_mappings -------------------------------------------
    op.add_column("product_case_mappings",
                  sa.Column("store_number", sa.String(32), nullable=True))
    op.execute(
        "UPDATE product_case_mappings m SET store_number = p.store_number "
        "FROM product_data_proposals p WHERE m.approved_proposal_id = p.id"
    )
    op.execute(
        f"UPDATE product_case_mappings SET store_number = '{ORIGINAL_STORE}' "
        "WHERE store_number IS NULL"
    )
    op.alter_column("product_case_mappings", "store_number", nullable=False)
    op.drop_constraint("uq_product_case_mapping_item_code", "product_case_mappings", type_="unique")
    op.create_unique_constraint("uq_product_case_mapping_store_item", "product_case_mappings",
                                ["store_number", "item_code"])
    op.create_index("idx_product_case_mappings_store", "product_case_mappings", ["store_number"])

    # --- invoices -----------------------------------------------------------
    op.add_column("invoices", sa.Column("store_number", sa.String(32), nullable=True))
    op.execute(f"UPDATE invoices SET store_number = '{ORIGINAL_STORE}' WHERE store_number IS NULL")
    op.alter_column("invoices", "store_number", nullable=False)
    op.create_index("idx_invoices_store", "invoices", ["store_number"])


def downgrade() -> None:
    op.drop_index("idx_invoices_store", table_name="invoices")
    op.drop_column("invoices", "store_number")
    op.drop_index("idx_product_case_mappings_store", table_name="product_case_mappings")
    op.drop_constraint("uq_product_case_mapping_store_item", "product_case_mappings", type_="unique")
    # Only safe when every item_code is mapped in one store.
    op.create_unique_constraint("uq_product_case_mapping_item_code", "product_case_mappings", ["item_code"])
    op.drop_column("product_case_mappings", "store_number")
