"""Scope product_pricing's source-row key by store

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-14

A pricing row is one source row of one workbook, and the importer
upserts on (source_file, source_sheet, source_row). That key was not
store-scoped: a second store's "Beer Inventory.xlsx" — the same
filename, because the exporter names it that — would have matched the
first store's rows, rewritten their values and flipped their
store_number. Filenames are not identities; a store's file is.

Adding store_number to the key lets two stores import identically named
workbooks side by side, and a re-import of the same file for the same
store still updates in place.
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_product_pricing_source_row", "product_pricing", type_="unique")
    op.create_unique_constraint(
        "uq_product_pricing_source_row", "product_pricing",
        ["store_number", "source_file", "source_sheet", "source_row"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_product_pricing_source_row", "product_pricing", type_="unique")
    op.create_unique_constraint(
        "uq_product_pricing_source_row", "product_pricing",
        ["source_file", "source_sheet", "source_row"],
    )
