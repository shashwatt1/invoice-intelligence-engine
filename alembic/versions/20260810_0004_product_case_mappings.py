"""Add product_case_mappings

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-10

Persistent UPC -> units-per-case mapping, the source of truth for the
PDI EDI's units-per-case field. Previously that value came from the
LLM's per-invoice pack_size extraction, which is unreliable and gave no
way to reuse a human's confirmation on the next invoice.

Unique on item_code: one mapping per product, enforced in the database
rather than only in application code.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_case_mappings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("item_code", sa.String(32), nullable=False),
        sa.Column("units_per_case", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_code", name="uq_product_case_mapping_item_code"),
    )
    op.create_index(
        "idx_product_case_mappings_item_code", "product_case_mappings", ["item_code"]
    )


def downgrade() -> None:
    op.drop_index("idx_product_case_mappings_item_code", table_name="product_case_mappings")
    op.drop_table("product_case_mappings")
