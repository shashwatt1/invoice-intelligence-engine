"""product master foundation — five additive tables, nothing existing touched

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-24

Creates the Product Master. Every statement here is a CREATE: no existing
table is altered, renamed, backfilled or dropped, and no data is written.
The legacy `product_identity`, `product_identifier`, `product_case_mappings`
and `product_data_proposals` tables keep owning production behaviour —
invoice processing, the mapping queue and the EDI writer do not read or
write anything created here, and will not until a later phase migrates
them deliberately.

The five tables exist because the research established that five separate
questions were being answered by one column, `product_case_mappings.units_per_case`.
PDI computes Case Retail = Item Retail x units_per_case, which makes that
field a commercial multiplier rather than a count of package contents. A
case of MICHELOB ULTRA C-18 12OZ physically holds 18 cans while its PDI
item accounts for 1 selling unit; both numbers are correct, and one column
could only ever hold one of them. `master_pack_compositions` holds the 18
and `master_commercial_mappings` holds the 1.

The identifier table is the other half. An 11-digit value means different
things depending on which system wrote it — a distributor's Excel float
lost a leading zero, an Item Sales scan code lost its check digit — so the
derivation that produced the canonical value is recorded per row instead of
being applied invisibly. Identity is then whatever two raw values share
once each has resolved on its own, which removes the need for pairwise
bridge rules between source formats.

The only FK reaching outside this group is master_commercial_mappings.store_id
-> stores.id, with ON DELETE RESTRICT so a commercial mapping can never
outlive the store it configures.

Down-migration drops only these five tables, in dependency order.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "master_products",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("canonical_key", sa.String(length=128), nullable=False),
        sa.Column("canonical_upc", sa.String(length=14), nullable=True),
        sa.Column("identity_basis", sa.String(length=32), nullable=False),
        sa.Column("identity_state", sa.String(length=32), nullable=False),
        sa.Column("canonical_description", sa.String(length=255), nullable=True),
        sa.Column("brand", sa.String(length=128), nullable=True),
        sa.Column("supplier", sa.String(length=128), nullable=True),
        sa.Column("product_class", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_key", name="uq_master_products_canonical_key"),
    )
    op.create_index("idx_master_products_canonical_upc", "master_products", ["canonical_upc"])
    op.create_index("idx_master_products_identity_state", "master_products", ["identity_state"])

    op.create_table(
        "master_product_identifiers",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_value", sa.String(length=128), nullable=False),
        sa.Column("normalized_value", sa.String(length=64), nullable=True),
        sa.Column("identifier_type", sa.String(length=32), nullable=False),
        sa.Column("derivation", sa.String(length=48), nullable=False),
        sa.Column("derivation_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_state", sa.String(length=32), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_distributor", sa.String(length=64), nullable=True),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.Column("source_sheet", sa.String(length=128), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("source_date", sa.Date(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["master_products.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("product_id", "identifier_type", "raw_value", "source_system",
                            name="uq_master_identifier_source_value"),
    )
    op.create_index("idx_master_identifier_lookup", "master_product_identifiers",
                    ["identifier_type", "normalized_value"])
    op.create_index("idx_master_identifier_normalized", "master_product_identifiers",
                    ["normalized_value"])
    op.create_index("idx_master_identifier_product", "master_product_identifiers", ["product_id"])

    op.create_table(
        "master_product_descriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("normalized_description", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("evidence_state", sa.String(length=32), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.Column("source_sheet", sa.String(length=128), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("observed_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["master_products.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("product_id", "normalized_description", "role", "source_system",
                            name="uq_master_description_role_source"),
    )
    op.create_index("idx_master_description_product", "master_product_descriptions", ["product_id"])
    op.create_index("idx_master_description_normalized", "master_product_descriptions",
                    ["normalized_description"])

    op.create_table(
        "master_pack_compositions",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("parent_product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("child_quantity", sa.Integer(), nullable=False),
        sa.Column("composition_basis", sa.String(length=32), nullable=False),
        sa.Column("evidence_state", sa.String(length=32), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.Column("source_sheet", sa.String(length=128), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["parent_product_id"], ["master_products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["child_product_id"], ["master_products.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("parent_product_id", "child_product_id", "child_quantity",
                            "source_system", name="uq_master_pack_composition"),
    )
    op.create_index("idx_master_pack_parent", "master_pack_compositions", ["parent_product_id"])
    op.create_index("idx_master_pack_child", "master_pack_compositions", ["child_product_id"])

    op.create_table(
        "master_commercial_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pdi_item_code", sa.String(length=32), nullable=False),
        sa.Column("commercial_unit_basis", sa.String(length=32), nullable=False),
        sa.Column("units_accounted_for", sa.Integer(), nullable=True),
        sa.Column("case_cost", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("approval_state", sa.String(length=32), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.Column("source_sheet", sa.String(length=128), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["master_products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("store_id", "product_id", "effective_from",
                            name="uq_master_commercial_store_product_effective"),
    )
    op.create_index("idx_master_commercial_store_item", "master_commercial_mappings",
                    ["store_id", "pdi_item_code"])
    op.create_index("idx_master_commercial_product", "master_commercial_mappings", ["product_id"])


def downgrade() -> None:
    # Dependency order: children first, then the products they hang from.
    op.drop_table("master_commercial_mappings")
    op.drop_table("master_pack_compositions")
    op.drop_table("master_product_descriptions")
    op.drop_table("master_product_identifiers")
    op.drop_table("master_products")
