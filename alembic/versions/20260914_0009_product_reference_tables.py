"""Add product_identity, product_identifier, product_pricing

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-14

Source-aware reference tables for the Beer Inventory workbook (and any
later price list). One pricing row per source row, provenance on every
row, and no units_per_case column the formatter could read —
product_case_mappings stays the sole authority for that.

See app/models/product_reference.py for why three tables rather than one.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _common():
    return [
        sa.Column("id", sa.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "product_identity",
        *_common(),
        sa.Column("store_number", sa.String(32), nullable=False),
        sa.Column("item_code", sa.String(32), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("brand", sa.String(128), nullable=True),
        sa.Column("supplier", sa.String(128), nullable=True),
        sa.Column("product_class", sa.String(64), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default="{}"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_number", "item_code", name="uq_product_identity_store_item"),
    )
    op.create_index("idx_product_identity_item_code", "product_identity", ["item_code"])

    op.create_table(
        "product_identifier",
        *_common(),
        sa.Column("store_number", sa.String(32), nullable=False),
        sa.Column("item_code", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("distributor", sa.String(64), nullable=True),
        sa.Column("source_file", sa.String(255), nullable=False),
        sa.Column("source_sheet", sa.String(128), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_number", "item_code", "kind", "value", "distributor",
                            name="uq_product_identifier"),
    )
    op.create_index("idx_product_identifier_lookup", "product_identifier",
                    ["store_number", "kind", "value", "distributor"])

    op.create_table(
        "product_pricing",
        *_common(),
        sa.Column("store_number", sa.String(32), nullable=False),
        sa.Column("item_code", sa.String(32), nullable=False),
        sa.Column("distributor", sa.String(64), nullable=True),
        sa.Column("pricing_basis", sa.String(32), nullable=False),
        sa.Column("case_cost", sa.Numeric(14, 4), nullable=True),
        sa.Column("unit_cost", sa.Numeric(14, 4), nullable=True),
        sa.Column("previous_case_cost", sa.Numeric(14, 4), nullable=True),
        sa.Column("package", sa.String(128), nullable=True),
        sa.Column("items_per_case_stated", sa.Integer(), nullable=True),
        sa.Column("items_per_case_derived", sa.Integer(), nullable=True),
        sa.Column("items_per_case_derivation", sa.String(16), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_conflicted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("conflict_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_file", sa.String(255), nullable=False),
        sa.Column("source_sheet", sa.String(128), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("raw_identifier", sa.String(64), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_file", "source_sheet", "source_row",
                            name="uq_product_pricing_source_row"),
        sa.CheckConstraint(
            "pricing_basis IN ('period_average','promo','frontline','price_change')",
            name="ck_product_pricing_basis"),
    )
    op.create_index("idx_product_pricing_item", "product_pricing", ["store_number", "item_code"])
    op.create_index("idx_product_pricing_conflicted", "product_pricing", ["is_conflicted"])


def downgrade() -> None:
    op.drop_table("product_pricing")
    op.drop_table("product_identifier")
    op.drop_table("product_identity")
